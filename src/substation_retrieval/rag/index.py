"""Document registry + RAG index (SQLite, on disk) with index versioning.

documents     one row per drawing (sha256 unique) -> extraction JSON path, status, quality, version
substations   one row per substation: feature vector (JSON), canonical mask raster (packed bits),
              searchable text, optional embedding
versions      every publish bumps the index version; engines reload when the version changes
"""
from __future__ import annotations

import json
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..core.ontology import base_equipment_type
from ..core.util import load_json, now_iso
from ..geometry import raster as R
from ..geometry.als import room_geom_from_substation

FLOORS = ("basement", "stilt", "podium", "ground", "first", "second", "terrace")


def substation_features(sub: dict, doc: dict) -> dict:
    md = doc.get("metadata") or {}
    civ = sub.get("civil") or {}
    eq = sub["electrical"]["equipment"]
    counts = sub.get("counts") or {}
    kv = [e["kva"] for e in eq if e.get("kva") and base_equipment_type(e.get("ontology_class") or "") in ("transformer", "packaged")]
    ops = sub["access"]["openings"]
    doors = [o.get("width_mm") for o in ops if o.get("type") in ("door", "rolling_shutter", "gate") and o.get("width_mm")]
    L, B = civ.get("length_mm"), civ.get("width_mm")
    return {
        "installation": md.get("installation"), "arrangement": md.get("arrangement"), "context": md.get("context"),
        "floor": sub.get("floor") or md.get("floor"), "kind": md.get("kind"), "voltage_kv": md.get("voltage_kv"),
        "multiple_substations": bool(md.get("multiple_substations")), "substation_count": md.get("substation_count") or 1,
        "area_m2": civ.get("area_m2"), "length_mm": max(L, B) if L and B else L, "width_mm": min(L, B) if L and B else B,
        "clear_height_mm": civ.get("clear_height_mm"), "beam_bottom_mm": civ.get("beam_bottom_mm"),
        "plinth_mm": civ.get("plinth_mm"), "platform": bool((civ.get("platform") or {}).get("height_mm") or (civ.get("platform") or {}).get("type")),
        "shape": civ.get("shape"),
        "counts": counts, "kva": sorted(kv),
        "door_max_mm": max(doors) if doors else None, "n_doors": len(doors),
        "rolling_shutter": any(o.get("type") == "rolling_shutter" for o in ops),
        "fire_exit": any(o.get("type") == "fire_exit" for o in ops),
        "staircase": any(o.get("type") == "staircase" for o in ops),
        "ventilation": bool(sub["ventilation"]["openings"]),
        "cutout": bool(sub["cable"].get("cutouts")),
        "beam": any(s.get("type") == "beam" for s in civ.get("structures", [])) or bool(civ.get("beam_bottom_mm")),
        "trench": bool(sub["cable"].get("routes")) or any(f.get("cls") == "trench" for f in (doc.get("site") or {}).get("features", [])),
        "access": any(f.get("cls") in ("access_path", "wayleave") for f in (doc.get("site") or {}).get("features", [])),
        "status": (doc.get("quality") or {}).get("status"),
        "building": _places().doc_building(doc),
        "localities": _places().localities(_places().doc_text(doc)),
    }


def _places():
    from ..core import places
    return places


def substation_text(sub: dict, doc: dict) -> str:
    parts = [doc["source"]["file_name"], sub.get("name") or "", doc.get("text") or ""]
    for e in sub["electrical"]["equipment"]:
        parts.append(f'{e.get("label") or ""} {e.get("equipment_type")} {e.get("subtype") or ""} {e.get("kva") or ""} kva')
    for o in sub["access"]["openings"] + sub["ventilation"]["openings"]:
        parts.append(f'{o.get("type")} {o.get("label") or ""}')
    md = doc.get("metadata") or {}
    parts += [str(md.get(k) or "") for k in ("installation", "arrangement", "context", "floor", "location_in_building", "kind")]
    return " ".join(p for p in parts if p)


class IndexDB:
    def __init__(self, path: str | Path, var_dir: str | Path | None = None):
        self.path = Path(path)
        self.var_dir = Path(var_dir) if var_dir else self.path.parent.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS documents(doc_id TEXT PRIMARY KEY, file_name TEXT, sha256 TEXT UNIQUE, status TEXT,
                quality REAL, method TEXT, json_path TEXT, source_path TEXT, created_at TEXT, updated_at TEXT,
                version INTEGER, n_substations INTEGER, deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS substations(sid TEXT PRIMARY KEY, doc_id TEXT, name TEXT, features TEXT,
                raster BLOB, raster_scale REAL, text TEXT, embedding BLOB);
            CREATE INDEX IF NOT EXISTS ix_sub_doc ON substations(doc_id);
            CREATE TABLE IF NOT EXISTS versions(version INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, note TEXT, doc_ids TEXT);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(substations)").fetchall()}
        if "img_embedding" not in cols:
            self.db.execute("ALTER TABLE substations ADD COLUMN img_embedding BLOB")
        self.db.commit()

    # ------------------------------------------------------------ writes
    def upsert(self, doc: dict, json_path: str, embedder=None, image_embedder=None) -> int:
        """Index one document (replaces its substations). Returns the number of substations."""
        did = doc["doc_id"]
        rows = []
        for sub in doc.get("substations", []):
            rg = room_geom_from_substation(sub)
            ras, scale = R.rasterise(rg)
            text = substation_text(sub, doc)
            emb = None
            if embedder is not None:
                try:
                    emb = np.asarray(embedder([text])[0], np.float32).tobytes()
                except Exception:
                    emb = None
            iemb = None
            if image_embedder is not None and ras is not None:
                try:
                    iemb = np.asarray(image_embedder.embed(ras), np.float32).tobytes()
                except Exception:
                    iemb = None
            rows.append((f"{did}:{sub['id']}", did, sub.get("name"), json.dumps(substation_features(sub, doc)),
                         R.pack(ras) if ras is not None else None, scale, text, emb, iemb))
        q = doc.get("quality") or {}
        with self._lock, self.db:
            self.db.execute("DELETE FROM substations WHERE doc_id=?", (did,))
            self.db.execute("DELETE FROM documents WHERE sha256=? AND doc_id!=?", (doc["source"]["sha256"], did))
            prev = self.db.execute("SELECT created_at FROM documents WHERE doc_id=?", (did,)).fetchone()
            self.db.execute("""INSERT OR REPLACE INTO documents(doc_id,file_name,sha256,status,quality,method,json_path,source_path,
                               created_at,updated_at,version,n_substations,deleted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                            (did, doc["source"]["file_name"], doc["source"]["sha256"], q.get("status"), q.get("score"),
                             (doc.get("provenance") or {}).get("method"), self._rel(json_path), doc["source"].get("path"),
                             prev[0] if prev else now_iso(), now_iso(), None, len(rows)))
            self.db.executemany("INSERT INTO substations(sid,doc_id,name,features,raster,raster_scale,text,embedding,img_embedding) "
                                "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        _load_doc_cached.cache_clear()
        return len(rows)

    def _rel(self, p) -> str:
        """Store extraction paths relative to var/ so the workspace can move between machines / OSes."""
        try:
            return Path(p).resolve().relative_to(self.var_dir.resolve()).as_posix()
        except ValueError:
            return str(p)

    def delete(self, doc_id: str) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM substations WHERE doc_id=?", (doc_id,))
            self.db.execute("UPDATE documents SET deleted=1, updated_at=? WHERE doc_id=?", (now_iso(), doc_id))
        _load_doc_cached.cache_clear()

    def publish(self, note: str, doc_ids: list[str]) -> int:
        with self._lock, self.db:
            cur = self.db.execute("INSERT INTO versions(created_at,note,doc_ids) VALUES(?,?,?)", (now_iso(), note, json.dumps(doc_ids)))
            v = cur.lastrowid
            if doc_ids:
                q = ",".join("?" * len(doc_ids))
                self.db.execute(f"UPDATE documents SET version=? WHERE doc_id IN ({q})", (v, *doc_ids))
        return int(v)

    def set_meta(self, key: str, value) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        with self._lock:
            r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    # ------------------------------------------------------------ reads
    def version(self) -> int:
        with self._lock:
            r = self.db.execute("SELECT MAX(version) FROM versions").fetchone()
        return int(r[0] or 0)

    def versions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT version, created_at, note, doc_ids FROM versions ORDER BY version DESC LIMIT ?", (limit,)).fetchall()
        return [{"version": r[0], "created_at": r[1], "note": r[2], "n_docs": len(json.loads(r[3] or "[]"))} for r in rows]

    def documents(self, include_deleted: bool = False) -> list[dict]:
        cols = ["doc_id", "file_name", "sha256", "status", "quality", "method", "json_path", "source_path", "created_at",
                "updated_at", "version", "n_substations", "deleted"]
        sql = "SELECT " + ",".join(cols) + " FROM documents" + ("" if include_deleted else " WHERE deleted=0") + " ORDER BY file_name"
        with self._lock:
            return [dict(zip(cols, r)) for r in self.db.execute(sql).fetchall()]

    _COLS = ["doc_id", "file_name", "sha256", "status", "quality", "method", "json_path", "source_path", "created_at",
             "updated_at", "version", "n_substations", "deleted"]

    def _one(self, where: str, arg) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT " + ",".join(self._COLS) + " FROM documents WHERE " + where, (arg,)).fetchone()
        return dict(zip(self._COLS, r)) if r else None

    def document(self, doc_id: str) -> dict | None:
        return self._one("doc_id=?", doc_id)

    def by_sha(self, sha: str) -> dict | None:
        return self._one("sha256=?", sha)

    def substations(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute("""SELECT s.sid, s.doc_id, s.name, s.features, s.raster, s.raster_scale, s.text, s.embedding,
                                      s.img_embedding FROM substations s JOIN documents d ON d.doc_id = s.doc_id WHERE d.deleted=0""").fetchall()
        return [{"sid": r[0], "doc_id": r[1], "name": r[2], "features": json.loads(r[3]), "raster": r[4], "raster_scale": r[5],
                 "text": r[6], "embedding": np.frombuffer(r[7], np.float32) if r[7] else None,
                 "img_embedding": np.frombuffer(r[8], np.float32) if r[8] else None} for r in rows]

    def load_doc(self, doc_id: str) -> dict | None:
        d = self.document(doc_id)
        if not d or not d.get("json_path"):
            return None
        p = Path(d["json_path"])
        if not p.is_absolute():
            p = self.var_dir / p
        if not p.exists():
            p = self.var_dir / "extractions" / doc_id / "doc.json"
        if not p.exists():
            return None
        return _load_doc_cached(str(p), d.get("updated_at"))

    def stats(self) -> dict:
        with self._lock:
            n_doc = self.db.execute("SELECT COUNT(*) FROM documents WHERE deleted=0").fetchone()[0]
            n_sub = self.db.execute("SELECT COUNT(*) FROM substations").fetchone()[0]
            st = dict(self.db.execute("SELECT status, COUNT(*) FROM documents WHERE deleted=0 GROUP BY status").fetchall())
        return {"documents": n_doc, "substations": n_sub, "by_status": st, "version": self.version(), "path": str(self.path)}


@lru_cache(maxsize=256)
def _load_doc_cached(path: str, stamp: str | None) -> dict:
    return load_json(path)
