"""Graph stores behind one interface.

SqliteGraphStore   default; persistent on disk (var/db/kg.sqlite), WAL mode, no server needed.
Neo4jGraphStore    Neo4j 5 over Bolt (Cypher). Community edition runs locally via `sr neo4j-start`,
                   or point graph.neo4j_uri at Aura / any Neo4j server. Same primitives.

Primitives used by ingestion and retrieval
  upsert_partition(pk, V, E)   delete-by-pk then insert (idempotent per drawing)
  delete_partition(pk)
  upsert_shared(V, E)          ontology concepts, facets, catalogue items
  partition(pk)                {vertices, edges} incl. edges to shared vertices
  substations()                [{vid, pk, props}]
  facet_degrees()              facet vid -> number of substations/documents carrying it
  edge_label_df()              edge label -> number of substations having >= 1 such edge
  shortlist(facets, concepts)  substation vid -> hits (cheap candidate generation)
  stats(), ping(), close()
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Protocol



def _neo4j_password(settings) -> str:
    from ..neo4j_server import password
    return password(settings)

class GraphStore(Protocol):
    name: str

    def upsert_partition(self, pk: str, V: list[dict], E: list[dict]) -> None: ...
    def delete_partition(self, pk: str) -> None: ...
    def upsert_shared(self, V: list[dict], E: list[dict]) -> None: ...
    def partition(self, pk: str) -> dict: ...
    def substations(self) -> list[dict]: ...
    def facet_degrees(self) -> dict[str, int]: ...
    def edge_label_df(self) -> dict[str, int]: ...
    def shortlist(self, facets: list[str], concepts: list[str]) -> dict[str, int]: ...
    def stats(self) -> dict: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...


def _decode(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        if isinstance(v, str) and v[:1] in "[{":
            try:
                out[k] = json.loads(v)
                continue
            except ValueError:
                pass
        out[k] = v
    return out


class SqliteGraphStore:
    name = "sqlite"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS vertices(vid TEXT PRIMARY KEY, label TEXT NOT NULL, pk TEXT NOT NULL, props TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS edges(eid INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, out_v TEXT NOT NULL,
                                             in_v TEXT NOT NULL, pk TEXT NOT NULL, props TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_v_label ON vertices(label);
            CREATE INDEX IF NOT EXISTS ix_v_pk ON vertices(pk);
            CREATE INDEX IF NOT EXISTS ix_e_out ON edges(out_v, label);
            CREATE INDEX IF NOT EXISTS ix_e_in ON edges(in_v, label);
            CREATE INDEX IF NOT EXISTS ix_e_pk ON edges(pk);
        """)
        self.db.commit()

    # ------------------------------------------------------------ writes
    def upsert_partition(self, pk, V, E):
        with self._lock, self.db:
            self.db.execute("DELETE FROM edges WHERE pk=?", (pk,))
            self.db.execute("DELETE FROM vertices WHERE pk=?", (pk,))
            self.db.executemany("INSERT OR REPLACE INTO vertices VALUES(?,?,?,?)",
                                [(v["vid"], v["label"], pk, json.dumps(v["props"])) for v in V])
            self.db.executemany("INSERT INTO edges(label,out_v,in_v,pk,props) VALUES(?,?,?,?,?)",
                                [(e["label"], e["out"], e["in"], pk, json.dumps(e["props"])) for e in E])

    def delete_partition(self, pk):
        with self._lock, self.db:
            self.db.execute("DELETE FROM edges WHERE pk=?", (pk,))
            self.db.execute("DELETE FROM vertices WHERE pk=?", (pk,))

    def upsert_shared(self, V, E):
        with self._lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO vertices VALUES(?,?,?,?)",
                                [(v["vid"], v["label"], v["pk"], json.dumps(v["props"])) for v in V])
            for e in E:
                hit = self.db.execute("SELECT 1 FROM edges WHERE label=? AND out_v=? AND in_v=? AND pk=?",
                                      (e["label"], e["out"], e["in"], e["pk"])).fetchone()
                if not hit:
                    self.db.execute("INSERT INTO edges(label,out_v,in_v,pk,props) VALUES(?,?,?,?,?)",
                                    (e["label"], e["out"], e["in"], e["pk"], json.dumps(e["props"])))

    def replace_edges(self, label: str, E: list[dict]) -> None:
        """Replace every edge of one label (used for SIMILAR_TO refresh)."""
        with self._lock, self.db:
            self.db.execute("DELETE FROM edges WHERE label=?", (label,))
            self.db.executemany("INSERT INTO edges(label,out_v,in_v,pk,props) VALUES(?,?,?,?,?)",
                                [(e["label"], e["out"], e["in"], e["pk"], json.dumps(e["props"])) for e in E])

    # ------------------------------------------------------------ reads
    def _rows_v(self, sql, args=()):
        with self._lock:
            return [{"vid": r[0], "label": r[1], "pk": r[2], "props": _decode(json.loads(r[3]))}
                    for r in self.db.execute(sql, args).fetchall()]

    def partition(self, pk):
        V = self._rows_v("SELECT vid,label,pk,props FROM vertices WHERE pk=?", (pk,))
        with self._lock:
            E = [{"label": r[0], "out": r[1], "in": r[2], "props": _decode(json.loads(r[3]))}
                 for r in self.db.execute("SELECT label,out_v,in_v,props FROM edges WHERE pk=?", (pk,)).fetchall()]
        ids = {v["vid"] for v in V}
        shared = {e["in"] for e in E if e["in"] not in ids}
        if shared:
            q = ",".join("?" * len(shared))
            V += self._rows_v(f"SELECT vid,label,pk,props FROM vertices WHERE vid IN ({q})", tuple(shared))
        return {"vertices": V, "edges": E}

    def partitions(self, pks: list[str]) -> dict[str, dict]:
        return {pk: self.partition(pk) for pk in pks}

    def substations(self):
        return self._rows_v("SELECT vid,label,pk,props FROM vertices WHERE label='substation'")

    def documents(self):
        return self._rows_v("SELECT vid,label,pk,props FROM vertices WHERE label='document'")

    def facet_degrees(self):
        with self._lock:
            rows = self.db.execute("SELECT in_v, COUNT(DISTINCT pk) FROM edges WHERE label='HAS_FACET' GROUP BY in_v").fetchall()
        return {r[0]: r[1] for r in rows}

    def edge_label_df(self):
        with self._lock:
            rows = self.db.execute("SELECT label, COUNT(DISTINCT pk) FROM edges WHERE pk != '_shared' GROUP BY label").fetchall()
        return {r[0]: r[1] for r in rows}

    def shortlist(self, facets, concepts):
        hits: dict[str, int] = defaultdict(int)
        with self._lock:
            if facets:
                q = ",".join("?" * len(facets))
                for out_v, pk in self.db.execute(f"SELECT out_v, pk FROM edges WHERE label='HAS_FACET' AND in_v IN ({q})", tuple(facets)):
                    if out_v == pk:   # document-level facet -> all its substations
                        for (sid,) in self.db.execute("SELECT in_v FROM edges WHERE label='HAS_SUBSTATION' AND out_v=?", (pk,)):
                            hits[sid] += 1
                    else:
                        hits[out_v] += 1
            if concepts:
                q = ",".join("?" * len(concepts))
                rows = self.db.execute(f"""SELECT c.out_v FROM edges i JOIN edges c ON c.in_v = i.out_v AND c.label='CONTAINS'
                                           WHERE i.label='IS_A' AND i.in_v IN ({q})""", tuple(concepts)).fetchall()
                rows += self.db.execute(f"""SELECT w.in_v FROM edges i JOIN edges w ON w.out_v = i.out_v AND w.label='ON_WALL'
                                            WHERE i.label='IS_A' AND i.in_v IN ({q})""", tuple(concepts)).fetchall()
                for (sid,) in rows:
                    hits[sid] += 1
        return dict(hits)

    def neighbours(self, vid: str, label: str | None = None) -> list[dict]:
        with self._lock:
            if label:
                rows = self.db.execute("SELECT label,out_v,in_v,props FROM edges WHERE (out_v=? OR in_v=?) AND label=?", (vid, vid, label)).fetchall()
            else:
                rows = self.db.execute("SELECT label,out_v,in_v,props FROM edges WHERE out_v=? OR in_v=?", (vid, vid)).fetchall()
        return [{"label": r[0], "out": r[1], "in": r[2], "props": _decode(json.loads(r[3]))} for r in rows]

    def schema_counts(self) -> dict:
        with self._lock:
            v = dict(self.db.execute("SELECT label, COUNT(*) FROM vertices GROUP BY label").fetchall())
            e = dict(self.db.execute("SELECT label, COUNT(*) FROM edges GROUP BY label").fetchall())
            isa = dict(self.db.execute("SELECT in_v, COUNT(*) FROM edges WHERE label='IS_A' GROUP BY in_v").fetchall())
        return {"vertices": v, "edges": e, "is_a": isa}

    def stats(self):
        c = self.schema_counts()
        return {"backend": self.name, "path": str(self.path), "vertices": sum(c["vertices"].values()),
                "edges": sum(c["edges"].values()), "by_label": c["vertices"]}

    def ping(self):
        try:
            with self._lock:
                self.db.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------- Neo4j
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BASE_LABEL = "Node"            # every vertex also carries its ontology label (document, substation, ...)
_ESCAPE_KEYS = {"vid", "pk"}


def _ident(s: str) -> str:
    """Labels / relationship types are interpolated into Cypher, so only plain identifiers are allowed."""
    if not _IDENT.match(s or ""):
        raise ValueError(f"unsafe graph label {s!r}")
    return f"`{s}`"


def _val(x):
    """Neo4j properties are primitives (or homogeneous arrays); lists/dicts travel as JSON strings."""
    if isinstance(x, (list, tuple, dict)):
        return json.dumps(x)
    return x


def _clean(props: dict) -> dict:
    return {k: _val(x) for k, x in props.items() if x is not None and k not in _ESCAPE_KEYS}


def _props_out(m: dict) -> dict:
    return _decode({k: v for k, v in m.items() if k not in ("vid", "pk")})


class Neo4jGraphStore:
    """Neo4j via the official Bolt driver.

    Vertices are `(:Node:<label> {vid, pk, ...props})`, edges are typed relationships carrying `pk` (the owning
    partition = doc_id). `vid` is unique, `pk` is indexed, so re-ingesting a drawing is DETACH DELETE by pk
    followed by a batched UNWIND insert, all in one transaction. List/dict props are JSON strings."""
    name = "neo4j"

    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = "",
                 database: str = "neo4j", timeout_s: float = 5.0, batch: int = 1000):
        from neo4j import GraphDatabase
        self.uri, self.database, self.batch = uri, database, batch
        auth = (user, password) if user else None
        self.driver = GraphDatabase.driver(uri, auth=auth, connection_timeout=timeout_s, max_connection_lifetime=1800)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ plumbing
    def _read(self, cypher: str, **params) -> list[dict]:
        def work(tx):
            return [r.data() for r in tx.run(cypher, **params)]
        with self.driver.session(database=self.database) as s:
            return s.execute_read(work)

    def _write(self, fn):
        with self.driver.session(database=self.database) as s:
            return s.execute_write(fn)

    def close(self):
        try:
            self.driver.close()
        except Exception:
            pass

    def ping(self):
        try:
            self._read("RETURN 1 AS ok")
            return True
        except Exception:
            return False

    def ensure_schema(self) -> list:
        """Unique vid, indexes on pk (nodes) and on the edge pk of the labels that are filtered by it. Idempotent."""
        stmts = [
            f"CREATE CONSTRAINT node_vid IF NOT EXISTS FOR (n:{BASE_LABEL}) REQUIRE n.vid IS UNIQUE",
            f"CREATE INDEX node_pk IF NOT EXISTS FOR (n:{BASE_LABEL}) ON (n.pk)",
        ]
        done = []
        with self.driver.session(database=self.database) as s:
            for q in stmts:
                s.run(q).consume()
                done.append(q.split(" IF ")[0].replace("CREATE ", "").lower())
        return done

    # ------------------------------------------------------------ writes
    @staticmethod
    def _tx_add_vertices(tx, V, batch):
        by_label: dict[str, list] = defaultdict(list)
        for v in V:
            by_label[v["label"]].append({"vid": v["vid"], "pk": v["pk"], "props": _clean(v["props"])})
        for label, rows in by_label.items():
            q = (f"UNWIND $rows AS r MERGE (n:{BASE_LABEL} {{vid: r.vid}}) SET n:{_ident(label)}, n.pk = r.pk, n += r.props")
            for i in range(0, len(rows), batch):
                tx.run(q, rows=rows[i:i + batch]).consume()

    @staticmethod
    def _tx_add_edges(tx, E, batch, merge: bool):
        by_label: dict[str, list] = defaultdict(list)
        for e in E:
            by_label[e["label"]].append({"o": e["out"], "i": e["in"], "pk": e.get("pk", ""), "props": _clean(e["props"])})
        for label, rows in by_label.items():
            verb = "MERGE" if merge else "CREATE"
            pat = (f"(a)-[x:{_ident(label)} {{pk: r.pk}}]->(b)" if merge else f"(a)-[x:{_ident(label)}]->(b)")
            q = (f"UNWIND $rows AS r MATCH (a:{BASE_LABEL} {{vid: r.o}}) MATCH (b:{BASE_LABEL} {{vid: r.i}}) "
                 f"{verb} {pat} SET x.pk = r.pk, x += r.props")
            for i in range(0, len(rows), batch):
                tx.run(q, rows=rows[i:i + batch]).consume()

    def upsert_partition(self, pk, V, E):
        E = [{**e, "pk": pk} for e in E]
        with self._lock:
            def work(tx):
                tx.run(f"MATCH (n:{BASE_LABEL} {{pk: $pk}}) DETACH DELETE n", pk=pk).consume()
                self._tx_add_vertices(tx, V, self.batch)
                self._tx_add_edges(tx, E, self.batch, merge=False)
            self._write(work)

    def delete_partition(self, pk):
        with self._lock:
            self._write(lambda tx: tx.run(f"MATCH (n:{BASE_LABEL} {{pk: $pk}}) DETACH DELETE n", pk=pk).consume())

    def upsert_shared(self, V, E):
        with self._lock:
            def work(tx):
                if V:
                    self._tx_add_vertices(tx, V, self.batch)
                if E:
                    self._tx_add_edges(tx, E, self.batch, merge=True)
            self._write(work)

    def replace_edges(self, label, E):
        with self._lock:
            def work(tx):
                tx.run(f"MATCH ()-[r:{_ident(label)}]->() DELETE r").consume()
                self._tx_add_edges(tx, E, self.batch, merge=False)
            self._write(work)

    # ------------------------------------------------------------ reads
    @staticmethod
    def _vrow(r: dict) -> dict:
        m = r["m"]
        label = next((l for l in r["l"] if l != BASE_LABEL), BASE_LABEL)
        return {"vid": m.get("vid"), "label": label, "pk": m.get("pk"), "props": _props_out(m)}

    @staticmethod
    def _erow(r: dict) -> dict:
        return {"label": r["l"], "out": r["o"], "in": r["i"], "props": _props_out(r["m"])}

    def _vrows(self, where: str, **params) -> list[dict]:
        return [self._vrow(r) for r in self._read(
            f"MATCH (n:{BASE_LABEL}) WHERE {where} RETURN labels(n) AS l, properties(n) AS m", **params)]

    def partition(self, pk):
        return self.partitions([pk])[pk]

    def partitions(self, pks: list[str]) -> dict[str, dict]:
        out = {pk: {"vertices": [], "edges": []} for pk in pks}
        for i in range(0, len(pks), 200):
            chunk = pks[i:i + 200]
            owner: dict[str, str] = {}
            for v in self._vrows("n.pk IN $pks", pks=chunk):
                out.setdefault(v["pk"], {"vertices": [], "edges": []})["vertices"].append(v)
                owner[v["vid"]] = v["pk"]
            rows = self._read(f"MATCH (a:{BASE_LABEL})-[r]->(b:{BASE_LABEL}) WHERE a.pk IN $pks "
                              "RETURN type(r) AS l, a.vid AS o, b.vid AS i, properties(r) AS m, a.pk AS pk", pks=chunk)
            shared: set[str] = set()
            for r in rows:
                out[r["pk"]]["edges"].append(self._erow(r))
                if owner.get(r["i"]) != r["pk"]:
                    shared.add(r["i"])
            sv: dict[str, dict] = {}
            sl = sorted(shared)
            for j in range(0, len(sl), 500):
                for v in self._vrows("n.vid IN $vids", vids=sl[j:j + 500]):
                    sv[v["vid"]] = v
            for pk in chunk:
                have = {v["vid"] for v in out[pk]["vertices"]}
                out[pk]["vertices"] += [sv[x] for x in {e["in"] for e in out[pk]["edges"]} - have if x in sv]
        return out

    def substations(self):
        return self._vrows("n:substation")

    def documents(self):
        return self._vrows("n:document")

    def facet_degrees(self):
        rows = self._read("MATCH (f:facet) OPTIONAL MATCH (f)<-[r:HAS_FACET]-() "
                          "RETURN f.vid AS vid, count(DISTINCT r.pk) AS deg")
        return {r["vid"]: r["deg"] for r in rows}

    def edge_label_df(self):
        rows = self._read("MATCH ()-[r]->() WHERE r.pk <> '_shared' RETURN type(r) AS l, count(DISTINCT r.pk) AS n")
        return {r["l"]: r["n"] for r in rows}

    def shortlist(self, facets, concepts):
        hits: dict[str, int] = defaultdict(int)
        if facets:
            rows = self._read(
                f"MATCH (f:{BASE_LABEL})<-[:HAS_FACET]-(x:substation) WHERE f.vid IN $v RETURN x.vid AS vid "
                "UNION ALL "
                f"MATCH (f:{BASE_LABEL})<-[:HAS_FACET]-(:document)-[:HAS_SUBSTATION]->(x:substation) WHERE f.vid IN $v RETURN x.vid AS vid",
                v=list(facets))
            for r in rows:
                hits[r["vid"]] += 1
        if concepts:
            rows = self._read(
                f"MATCH (c:{BASE_LABEL})<-[:IS_A]-(x) WHERE c.vid IN $v MATCH (s:substation)-[:CONTAINS]->(x) RETURN s.vid AS vid "
                "UNION ALL "
                f"MATCH (c:{BASE_LABEL})<-[:IS_A]-(x)-[:ON_WALL]->(s:substation) WHERE c.vid IN $v RETURN s.vid AS vid",
                v=list(concepts))
            for r in rows:
                hits[r["vid"]] += 1
        return dict(hits)

    def neighbours(self, vid, label=None):
        cond = " AND type(r) = $label" if label else ""
        rows = self._read(f"MATCH (a:{BASE_LABEL} {{vid: $vid}})-[r]-(b:{BASE_LABEL}) WHERE true{cond} "
                          "RETURN type(r) AS l, startNode(r).vid AS o, endNode(r).vid AS i, properties(r) AS m",
                          vid=vid, label=label)
        return [self._erow(r) for r in rows]

    def schema_counts(self):
        v = self._read(f"MATCH (n:{BASE_LABEL}) UNWIND labels(n) AS l WITH l WHERE l <> '{BASE_LABEL}' RETURN l, count(*) AS n")
        e = self._read("MATCH ()-[r]->() RETURN type(r) AS l, count(*) AS n")
        isa = self._read("MATCH (c:concept) OPTIONAL MATCH (c)<-[r:IS_A]-() RETURN c.vid AS vid, count(r) AS n")
        return {"vertices": {r["l"]: r["n"] for r in v}, "edges": {r["l"]: r["n"] for r in e},
                "is_a": {r["vid"]: r["n"] for r in isa}}

    def stats(self):
        c = self.schema_counts()
        return {"backend": self.name, "uri": self.uri, "vertices": sum(c["vertices"].values()),
                "edges": sum(c["edges"].values()), "by_label": c["vertices"]}


class Neo4jProxy:
    """Neo4j store that (re)connects on demand: if the server is down or still starting, calls raise a clear
    error (KG engine -> RAG fallback in auto mode) and the next call after `retry_s` tries again."""

    def __init__(self, settings, log=print, retry_s: float = 5.0):
        self.settings, self.log, self.retry_s = settings, log, retry_s
        self.uri = settings.get("graph.neo4j_uri", "bolt://localhost:7687")
        self._st: Neo4jGraphStore | None = None
        self._next_try = 0.0
        self.error = None
        self._lock = threading.Lock()
        try:
            self._get(quiet=True)
        except RuntimeError:
            pass

    @property
    def name(self):
        return "neo4j" if self._st else "neo4j (unreachable)"

    def _fail(self):
        return RuntimeError(f"Neo4j at {self.uri} is not reachable ({self.error}); start it with: sr neo4j-start")

    def _get(self, quiet: bool = False) -> Neo4jGraphStore:
        with self._lock:
            if self._st is not None:
                return self._st
            if time.time() < self._next_try:
                raise self._fail()
            st = None
            try:
                st = Neo4jGraphStore(self.uri, self.settings.get("graph.neo4j_user", "neo4j"),
                                     _neo4j_password(self.settings),
                                     self.settings.get("graph.neo4j_database", "neo4j"),
                                     float(self.settings.get("engines.kg.timeout_s", 5.0)))
                st.driver.verify_connectivity()
                if self.settings.get("graph.ensure_schema", True):
                    try:
                        st.ensure_schema()
                    except Exception as e:  # pragma: no cover
                        self.log(f"graph: schema not applied ({e})")
                self._st, self.error = st, None
                import atexit
                atexit.register(st.close)
                self.log(f"graph: Neo4j connected at {self.uri}")
                return st
            except Exception as e:
                if st is not None:
                    st.close()
                self.error = f"{type(e).__name__}: {e}"
                self._next_try = time.time() + self.retry_s
                if not quiet:
                    self.log(f"graph: Neo4j unavailable at {self.uri} ({self.error})")
                raise self._fail()

    def ping(self):
        try:
            ok = self._get().ping()
            if not ok:
                self._st = None
            return ok
        except Exception:
            return False

    def stats(self):
        try:
            return self._get().stats()
        except Exception:
            return {"backend": self.name, "uri": self.uri, "error": self.error, "vertices": 0, "edges": 0, "by_label": {}}

    def schema_counts(self):
        try:
            return self._get().schema_counts()
        except Exception:
            return {"vertices": {}, "edges": {}, "is_a": {}}

    def close(self):
        if self._st:
            self._st.close()

    def __getattr__(self, item):
        if item.startswith("_") or getattr(Neo4jGraphStore, item, None) is None:
            raise AttributeError(item)

        def call(*a, **k):
            st = self._get()
            try:
                return getattr(st, item)(*a, **k)
            except Exception:
                if not st.ping():          # connection lost: reconnect on the next call
                    self._st = None
                raise
        return call


def open_store(settings, log=print) -> "GraphStore":
    backend = str(settings.get("graph.backend", "neo4j")).lower()
    if backend == "neo4j":
        proxy = Neo4jProxy(settings, log)
        uri = str(settings.get("graph.neo4j_uri", ""))
        if proxy._st is None and settings.get("graph.autostart", True) and ("localhost" in uri or "127.0.0.1" in uri):
            from .. import neo4j_server
            print("Neo4j is not running - starting it (first time: one-off ~130 MB download) ...", flush=True)
            if neo4j_server.start(settings, wait_s=int(settings.get("graph.autostart_wait_s", 180)),
                                  log=lambda m: print(m, flush=True)) == 0:
                proxy._next_try = 0.0
                try:
                    proxy._get()
                except RuntimeError:
                    pass
        if proxy._st is None and settings.get("graph.fallback_sqlite", False):
            log(f"graph: Neo4j unavailable ({proxy.error}); using sqlite (graph.fallback_sqlite)")
            return SqliteGraphStore(settings.var("db", "kg.sqlite"))
        return proxy
    return SqliteGraphStore(settings.var("db", "kg.sqlite"))
