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


class GremlinGraphStore:
    """JanusGraph / Gremlin Server via gremlinpython bytecode. Vertices carry `vid` and `pk` properties
    so native ids never leak; props that are lists/dicts are JSON strings."""
    name = "gremlin"

    def __init__(self, url: str = "ws://localhost:8182/gremlin", source: str = "g", timeout_s: float = 5.0):
        from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
        from gremlin_python.process.anonymous_traversal import traversal
        self.url = url
        self.conn = DriverRemoteConnection(url, source)
        self.g = traversal().with_remote(self.conn)
        self.timeout_s = timeout_s

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

    def _add_vertices(self, V, batch=50):
        for i in range(0, len(V), batch):
            t = self.g
            for v in V[i:i + batch]:
                t = t.addV(v["label"]).property("vid", v["vid"]).property("pk", v["pk"])
                for k, x in v["props"].items():
                    t = t.property(k, x)
            t.iterate()

    def _add_edges(self, E):
        from gremlin_python.process.graph_traversal import __
        for e in E:
            t = self.g.V().has("vid", e["out"]).addE(e["label"]).to(__.V().has("vid", e["in"])).property("pk", e.get("pk", ""))
            for k, x in e["props"].items():
                t = t.property(k, x)
            t.iterate()

    def upsert_partition(self, pk, V, E):
        self.delete_partition(pk)
        self._add_vertices(V)
        self._add_edges(E)

    def delete_partition(self, pk):
        self.g.V().has("pk", pk).drop().iterate()

    def upsert_shared(self, V, E):
        from gremlin_python.process.graph_traversal import __
        for v in V:
            t = self.g.V().has("vid", v["vid"]).fold().coalesce(__.unfold(), __.addV(v["label"]).property("vid", v["vid"]).property("pk", v["pk"]))
            for k, x in v["props"].items():
                t = t.property(k, x)
            t.iterate()
        for e in E:
            exists = self.g.V().has("vid", e["out"]).outE(e["label"]).where(__.inV().has("vid", e["in"])).limit(1).count().next()
            if not exists:
                self._add_edges([e])

    def replace_edges(self, label, E):
        self.g.E().hasLabel(label).drop().iterate()
        self._add_edges(E)

    def _vmap(self, t):
        out = []
        for r in t.valueMap().to_list():
            props = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in r.items()}
            vid, pk = props.pop("vid", None), props.pop("pk", None)
            out.append({"vid": vid, "pk": pk, "props": _decode(props)})
        return out

    def partition(self, pk):
        from gremlin_python.process.graph_traversal import __
        V = []
        for r in self.g.V().has("pk", pk).project("label", "m").by(__.label()).by(__.valueMap()).to_list():
            props = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in r["m"].items()}
            vid, p = props.pop("vid", None), props.pop("pk", None)
            V.append({"vid": vid, "label": r["label"], "pk": p, "props": _decode(props)})
        E = []
        for r in (self.g.V().has("pk", pk).outE().project("label", "out", "in", "m")
                  .by(__.label()).by(__.outV().values("vid")).by(__.inV().values("vid")).by(__.valueMap()).to_list()):
            props = {k: v for k, v in r["m"].items() if k != "pk"}
            E.append({"label": r["label"], "out": r["out"], "in": r["in"], "props": _decode(props)})
        ids = {v["vid"] for v in V}
        for vid in {e["in"] for e in E if e["in"] not in ids}:
            for r in self.g.V().has("vid", vid).project("label", "m").by(__.label()).by(__.valueMap()).to_list():
                props = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in r["m"].items()}
                V.append({"vid": props.pop("vid", vid), "label": r["label"], "pk": props.pop("pk", "_shared"), "props": _decode(props)})
        return {"vertices": V, "edges": E}

    def substations(self):
        return [{**x, "label": "substation"} for x in self._vmap(self.g.V().hasLabel("substation"))]

    def documents(self):
        return [{**x, "label": "document"} for x in self._vmap(self.g.V().hasLabel("document"))]

    def facet_degrees(self):
        from gremlin_python.process.graph_traversal import __
        rows = self.g.V().hasLabel("facet").project("vid", "deg").by("vid").by(__.inE("HAS_FACET").values("pk").dedup().count()).to_list()
        return {r["vid"]: r["deg"] for r in rows}

    def edge_label_df(self):
        from gremlin_python.process.graph_traversal import __
        return {k: v for k, v in self.g.E().group().by(__.label()).by(__.values("pk").dedup().count()).next().items()}

    def shortlist(self, facets, concepts):
        from gremlin_python.process.graph_traversal import __
        from gremlin_python.process.traversal import P as GP
        hits: dict[str, int] = defaultdict(int)
        if facets:
            for vid, n in (self.g.V().has("facet", "vid", GP.within(*facets)).in_("HAS_FACET")
                           .coalesce(__.hasLabel("substation"), __.out("HAS_SUBSTATION")).groupCount().by("vid").next().items()):
                hits[vid] += n
        if concepts:
            for vid, n in (self.g.V().has("concept", "vid", GP.within(*concepts)).in_("IS_A").in_("CONTAINS")
                           .hasLabel("substation").groupCount().by("vid").next().items()):
                hits[vid] += n
        return dict(hits)

    def neighbours(self, vid, label=None):
        from gremlin_python.process.graph_traversal import __
        t = self.g.V().has("vid", vid).bothE()
        if label:
            t = t.hasLabel(label)
        return [{"label": r["label"], "out": r["out"], "in": r["in"], "props": {}} for r in
                t.project("label", "out", "in").by(__.label()).by(__.outV().values("vid")).by(__.inV().values("vid")).to_list()]

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


def open_store(settings, log=print) -> "GraphStore":
    backend = str(settings.get("graph.backend", "sqlite")).lower()
    if backend == "gremlin":
        url = settings.get("graph.gremlin_url")
        try:
            t0 = time.time()
            st = GremlinGraphStore(url, settings.get("graph.traversal_source", "g"), settings.get("engines.kg.timeout_s", 5.0))
            if not st.ping():
                raise RuntimeError("ping failed")
            log(f"graph: gremlin at {url} ({time.time() - t0:.1f}s)")
            return st
        except Exception as e:  # pragma: no cover - needs a server
            log(f"graph: gremlin unavailable ({type(e).__name__}: {e}); using sqlite")
    return SqliteGraphStore(settings.var("db", "kg.sqlite"))
