"""Graph stores behind one interface.

SqliteGraphStore   default; persistent on disk (var/db/kg.sqlite), WAL mode, no server needed.
GremlinGraphStore  Apache TinkerPop Gremlin server: JanusGraph (BerkeleyDB-JE/Lucene locally,
                   Cloud Bigtable + Elasticsearch on GCP) or Gremlin Server. Same primitives.

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
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Protocol


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


RESERVED = {"label", "key", "id", "value", "vertex", "edge", "property"}


def _ek(k: str) -> str:
    """JanusGraph reserves some property names; store them prefixed."""
    return f"p_{k}" if k in RESERVED else k


def _dk(k: str) -> str:
    return k[2:] if k.startswith("p_") and k[2:] in RESERVED else k


def _props_in(m: dict) -> dict:
    out = {}
    for k, v in m.items():
        if k in ("vid", "pk"):
            continue
        out[_dk(k)] = v[0] if isinstance(v, list) and len(v) == 1 else v
    return _decode(out)


class GremlinGraphStore:
    """JanusGraph (or any TinkerPop Gremlin Server) via gremlinpython bytecode.

    Vertices carry `vid` (our id) and `pk` (partition = doc_id) as indexed properties, so native ids never
    leak; list/dict props are JSON strings; JanusGraph-reserved names (label, key, id, value) are stored as
    p_<name>. Writes are batched (one round trip per `batch` vertices / edges)."""
    name = "gremlin"

    def __init__(self, url: str = "ws://localhost:8182/gremlin", source: str = "g", timeout_s: float = 5.0, batch: int = 40):
        from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
        from gremlin_python.process.anonymous_traversal import traversal
        self.url = url
        self.conn = DriverRemoteConnection(url, source)
        self.g = traversal().with_remote(self.conn)
        self.timeout_s = timeout_s
        self.batch = batch
        self._lock = threading.RLock()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def ping(self):
        try:
            self.g.V().limit(1).count().next()
            return True
        except Exception:
            return False

    def ensure_schema(self, script_path=None) -> list:
        """Create the composite indexes (vid, pk, cls, edge pk) if missing. Needs script evaluation on the server
        (enabled in the JanusGraph distribution). Indexes must exist before data is loaded to be used."""
        from gremlin_python.driver.client import Client
        p = Path(script_path) if script_path else Path(__file__).resolve().parents[3] / "scripts" / "janusgraph" / "schema.groovy"
        script = "\n".join(l for l in p.read_text(encoding="utf-8").splitlines() if not l.strip().startswith("//"))
        c = Client(self.url, "g")
        try:
            return c.submit(script).all().result()
        finally:
            c.close()

    # ------------------------------------------------------------ writes
    def _add_vertices(self, V):
        for i in range(0, len(V), self.batch):
            t = self.g
            for v in V[i:i + self.batch]:
                t = t.addV(v["label"]).property("vid", v["vid"]).property("pk", v["pk"])
                for k, x in v["props"].items():
                    if x is not None:
                        t = t.property(_ek(k), x)
            t.iterate()

    def _ids(self, vids) -> dict:
        from gremlin_python.process.graph_traversal import __
        from gremlin_python.process.traversal import P as GP
        out = {}
        vids = list(vids)
        for i in range(0, len(vids), 500):
            for r in self.g.V().has("vid", GP.within(*vids[i:i + 500])).project("v", "id").by("vid").by(__.id_()).to_list():
                out[r["v"]] = r["id"]
        return out

    def _add_edges(self, E):
        from gremlin_python.process.graph_traversal import __
        ids = self._ids({e["out"] for e in E} | {e["in"] for e in E})
        E = [e for e in E if e["out"] in ids and e["in"] in ids]
        for i in range(0, len(E), self.batch):
            t = self.g
            for e in E[i:i + self.batch]:
                t = t.addE(e["label"]).from_(__.V(ids[e["out"]])).to(__.V(ids[e["in"]])).property("pk", e.get("pk", ""))
                for k, x in e["props"].items():
                    if x is not None:
                        t = t.property(_ek(k), x)
            t.iterate()

    def upsert_partition(self, pk, V, E):
        with self._lock:
            self.delete_partition(pk)
            self._add_vertices(V)
            self._add_edges([{**e, "pk": pk} for e in E])

    def delete_partition(self, pk):
        with self._lock:
            self.g.V().has("pk", pk).drop().iterate()

    def upsert_shared(self, V, E):
        from gremlin_python.process.graph_traversal import __
        with self._lock:
            have = set(self._ids([v["vid"] for v in V])) if V else set()
            new = [v for v in V if v["vid"] not in have]
            self._add_vertices(new)
            if E:
                existing = set()
                for r in (self.g.E().has("pk", P_within([e["pk"] for e in E])).project("l", "o", "i")
                          .by(__.label()).by(__.outV().values("vid")).by(__.inV().values("vid")).to_list()):
                    existing.add((r["l"], r["o"], r["i"]))
                self._add_edges([e for e in E if (e["label"], e["out"], e["in"]) not in existing])

    def replace_edges(self, label, E):
        with self._lock:
            self.g.E().hasLabel(label).drop().iterate()
            self._add_edges(E)

    # ------------------------------------------------------------ reads
    def _vrows(self, t) -> list[dict]:
        from gremlin_python.process.graph_traversal import __
        out = []
        for r in t.project("l", "m").by(__.label()).by(__.valueMap()).to_list():
            m = r["m"]
            first = lambda x: x[0] if isinstance(x, list) and x else x  # noqa: E731
            out.append({"vid": first(m.get("vid")), "label": r["l"], "pk": first(m.get("pk")), "props": _props_in(m)})
        return out

    def _erows(self, t) -> list[dict]:
        from gremlin_python.process.graph_traversal import __
        return [{"label": r["l"], "out": r["o"], "in": r["i"], "props": _props_in(r["m"])}
                for r in t.project("l", "o", "i", "m").by(__.label()).by(__.outV().values("vid")).by(__.inV().values("vid"))
                .by(__.valueMap()).to_list()]

    def partition(self, pk):
        V = self._vrows(self.g.V().has("pk", pk))
        E = self._erows(self.g.V().has("pk", pk).outE())
        ids = {v["vid"] for v in V}
        shared = sorted({e["in"] for e in E if e["in"] not in ids})
        for i in range(0, len(shared), 500):
            V += self._vrows(self.g.V().has("vid", P_within(shared[i:i + 500])))
        return {"vertices": V, "edges": E}

    def partitions(self, pks: list[str]) -> dict[str, dict]:
        """Many partitions in three round trips per 200 (vertices, edges, shared targets)."""
        out = {pk: {"vertices": [], "edges": []} for pk in pks}
        for i in range(0, len(pks), 200):
            chunk = pks[i:i + 200]
            V = self._vrows(self.g.V().has("pk", P_within(chunk)))
            owner = {}
            for v in V:
                out.setdefault(v["pk"], {"vertices": [], "edges": []})["vertices"].append(v)
                owner[v["vid"]] = v["pk"]
            from gremlin_python.process.graph_traversal import __
            rows = (self.g.V().has("pk", P_within(chunk)).outE().project("l", "o", "i", "m", "pk")
                    .by(__.label()).by(__.outV().values("vid")).by(__.inV().values("vid")).by(__.valueMap()).by(__.outV().values("pk")).to_list())
            shared = set()
            for r in rows:
                pk = r["pk"]
                out[pk]["edges"].append({"label": r["l"], "out": r["o"], "in": r["i"], "props": _props_in(r["m"])})
                if r["i"] not in owner or owner[r["i"]] != pk:
                    shared.add(r["i"])
            sv = {}
            sl = sorted(shared)
            for j in range(0, len(sl), 500):
                for v in self._vrows(self.g.V().has("vid", P_within(sl[j:j + 500]))):
                    sv[v["vid"]] = v
            for pk in chunk:
                need = {e["in"] for e in out[pk]["edges"]} - {v["vid"] for v in out[pk]["vertices"]}
                out[pk]["vertices"] += [sv[x] for x in need if x in sv]
        return out

    def substations(self):
        return self._vrows(self.g.V().hasLabel("substation"))

    def documents(self):
        return self._vrows(self.g.V().hasLabel("document"))

    def facet_degrees(self):
        from gremlin_python.process.graph_traversal import __
        rows = self.g.V().hasLabel("facet").project("vid", "deg").by("vid").by(__.inE("HAS_FACET").values("pk").dedup().count()).to_list()
        return {r["vid"]: r["deg"] for r in rows}

    def edge_label_df(self):
        from gremlin_python.process.graph_traversal import __
        rows = self.g.E().has("pk", P_neq("_shared")).group().by(__.label()).by(__.values("pk").dedup().count()).to_list()
        return dict(rows[0]) if rows else {}

    def shortlist(self, facets, concepts):
        from gremlin_python.process.graph_traversal import __
        hits: dict[str, int] = defaultdict(int)
        if facets:
            rows = (self.g.V().has("vid", P_within(facets)).in_("HAS_FACET")
                    .union(__.hasLabel("substation"), __.hasLabel("document").out("HAS_SUBSTATION")).values("vid").to_list())
            for vid in rows:
                hits[vid] += 1
        if concepts:
            rows = (self.g.V().has("vid", P_within(concepts)).in_("IS_A")
                    .union(__.in_("CONTAINS"), __.out("ON_WALL")).hasLabel("substation").values("vid").to_list())
            for vid in rows:
                hits[vid] += 1
        return dict(hits)

    def neighbours(self, vid, label=None):
        t = self.g.V().has("vid", vid).bothE()
        if label:
            t = t.hasLabel(label)
        return self._erows(t)

    def schema_counts(self):
        from gremlin_python.process.graph_traversal import __
        v = self.g.V().groupCount().by(__.label()).next()
        e = self.g.E().groupCount().by(__.label()).next()
        isa = self.g.V().hasLabel("concept").project("vid", "n").by("vid").by(__.inE("IS_A").count()).to_list()
        return {"vertices": dict(v), "edges": dict(e), "is_a": {r["vid"]: r["n"] for r in isa}}

    def stats(self):
        c = self.schema_counts()
        return {"backend": self.name, "url": self.url, "vertices": sum(c["vertices"].values()),
                "edges": sum(c["edges"].values()), "by_label": c["vertices"]}


def P_within(vals):
    from gremlin_python.process.traversal import P as GP
    return GP.within(*list(vals))


def P_neq(v):
    from gremlin_python.process.traversal import P as GP
    return GP.neq(v)


class JanusGraphProxy:
    """JanusGraph store that (re)connects on demand: if the server is down or still starting, calls raise a
    clear error (KG engine -> RAG fallback in auto mode) and the next call after `retry_s` tries again."""

    def __init__(self, settings, log=print, retry_s: float = 5.0):
        self.settings, self.log, self.retry_s = settings, log, retry_s
        self.url = settings.get("graph.gremlin_url")
        self._st: GremlinGraphStore | None = None
        self._next_try = 0.0
        self.error = None
        self._lock = threading.Lock()
        try:
            self._get(quiet=True)
        except RuntimeError:
            pass

    @property
    def name(self):
        return "janusgraph" if self._st else "janusgraph (unreachable)"

    def _get(self, quiet: bool = False) -> GremlinGraphStore:
        with self._lock:
            if self._st is not None:
                return self._st
            if time.time() < self._next_try:
                raise RuntimeError(f"JanusGraph at {self.url} is not reachable ({self.error}); start scripts/janusgraph/start")
            try:
                st = GremlinGraphStore(self.url, self.settings.get("graph.traversal_source", "g"), self.settings.get("engines.kg.timeout_s", 5.0))
                try:
                    st.g.V().limit(1).count().next()
                except Exception as e:
                    st.close()
                    raise RuntimeError(f"server not answering ({type(e).__name__}: {e})")
                if self.settings.get("graph.ensure_schema", True):
                    try:
                        st.ensure_schema()
                    except Exception as e:  # pragma: no cover
                        self.log(f"graph: schema script not applied ({e})")
                self._st, self.error = st, None
                self.log(f"graph: JanusGraph connected at {self.url}")
                return st
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                self._next_try = time.time() + self.retry_s
                if not quiet:
                    self.log(f"graph: JanusGraph unavailable at {self.url} ({self.error})")
                raise RuntimeError(f"JanusGraph at {self.url} is not reachable ({self.error}); start scripts/janusgraph/start")

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
            return {"backend": self.name, "url": self.url, "error": self.error, "vertices": 0, "edges": 0, "by_label": {}}

    def schema_counts(self):
        try:
            return self._get().schema_counts()
        except Exception:
            return {"vertices": {}, "edges": {}, "is_a": {}}

    def close(self):
        if self._st:
            self._st.close()

    def __getattr__(self, item):
        attr = getattr(GremlinGraphStore, item, None)
        if attr is None:
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
    backend = str(settings.get("graph.backend", "janusgraph")).lower()
    if backend in ("janusgraph", "gremlin"):
        proxy = JanusGraphProxy(settings, log)
        if proxy._st is None and settings.get("graph.fallback_sqlite", False):
            log(f"graph: JanusGraph unavailable ({proxy.error}); using sqlite (graph.fallback_sqlite)")
            return SqliteGraphStore(settings.var("db", "kg.sqlite"))
        return proxy
    return SqliteGraphStore(settings.var("db", "kg.sqlite"))
