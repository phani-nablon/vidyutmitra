"""Incremental ingestion (update side, separate from retrieval).

Per file:  register (sha256, dedupe) -> extract (adapter + LLM passes, or v1 import) -> review gate
           -> save extraction JSON -> upsert graph partition (delete by pk) -> upsert RAG index
           -> refresh SIMILAR_TO -> publish a new index version.
Bulk:      many jobs, or `import_v1` / `rebuild` which publish once at the end.
Rebuild:   re-index every stored extraction without calling the LLM (schema or scoring changes).
Future AEML integration only needs to call `submit_file` (or POST /api/ingest) per pushed drawing.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from ..core.util import doc_id_for, dump_json, load_json, now_iso, sha256_file, slug
from ..geometry import raster as R
from ..kg.build import build_partition, ontology_partition, shared_for
from .jobs import JobDB, WorkerPool

log = logging.getLogger(__name__)
SUPPORTED = {".pdf", ".dxf", ".dwg"}


class IngestService:
    def __init__(self, settings, store, index, jobs: JobDB, extractor=None):
        self.s = settings
        self.store = store
        self.index = index
        self.jobs = jobs
        self._extractor = extractor
        self.image_embedder = None
        self.pool: WorkerPool | None = None

    @property
    def extractor(self):
        if self._extractor is None:
            from ..extraction.pipeline import Extractor
            self._extractor = Extractor(self.s)
        return self._extractor

    # ------------------------------------------------------------ setup
    def ensure_ontology(self) -> None:
        V, E = ontology_partition()
        self.store.upsert_shared(V, E)

    def start_workers(self, n: int = 1) -> None:
        self.pool = WorkerPool(self.jobs, self.handle, n)
        self.pool.start()

    def doc_path(self, doc_id: str) -> Path:
        return self.s.var("extractions", doc_id, "doc.json")

    # ------------------------------------------------------------ submit
    def submit_file(self, src: str | Path, file_name: str | None = None, options: dict | None = None,
                    batch_id: str | None = None, copy: bool = True) -> dict:
        src = Path(src)
        name = file_name or src.name
        if Path(name).suffix.lower() not in SUPPORTED:
            raise ValueError(f"unsupported file type {Path(name).suffix}; use PDF, DXF or DWG")
        dest = src
        if copy:
            dest = self.s.var("uploads", f"{uuid.uuid4().hex[:8]}_{slug(Path(name).stem, 80)}{Path(name).suffix.lower()}")
            shutil.copyfile(src, dest)
        return self.jobs.add(name, str(dest), options or {}, batch_id)

    def submit_many(self, paths: list[str | Path], options: dict | None = None) -> dict:
        batch = uuid.uuid4().hex[:8]
        jobs = [self.submit_file(p, options=options, batch_id=batch) for p in paths]
        return {"batch_id": batch, "jobs": jobs}

    # ------------------------------------------------------------ job handler
    def handle(self, job: dict) -> None:
        jid = job["job_id"]
        opts = job.get("options") or {}
        path = Path(job["path"])

        def progress(stage: str, frac: float):
            self.jobs.update(jid, stage=stage, progress=round(frac, 2))
        progress("hashing", 0.02)
        sha = sha256_file(path)
        self.jobs.update(jid, sha256=sha, doc_id=doc_id_for(sha))
        prev = self.index.by_sha(sha)
        if prev and not prev["deleted"] and not opts.get("force"):
            self.jobs.update(jid, status="duplicate", stage="already indexed", progress=1.0, doc_id=prev["doc_id"],
                             message=f"same file already indexed as {prev['file_name']}")
            return
        doc = None
        if opts.get("use_v1", True) and path.suffix.lower() == ".pdf":
            doc = self._v1_for(job["file_name"], path)
            if doc:
                progress("imported v1 extraction", 0.6)
        if doc is None:
            progress("extracting", 0.05)
            doc = self.extractor.extract(path, sha, progress)
        doc["source"]["file_name"] = job["file_name"]
        doc["source"]["path"] = str(path)
        gate = opts.get("review_gate") or self.s.get("extraction.review_gate", "auto")
        jp = self.doc_path(doc["doc_id"])
        dump_json(doc, jp)
        if gate == "hold" and doc["quality"]["status"] != "reviewed":
            self.jobs.update(jid, status="awaiting_review", stage="awaiting review", progress=0.8, doc_id=doc["doc_id"],
                             message=f"quality {doc['quality']['score']}; approve to index")
            return
        progress("indexing", 0.85)
        self.index_doc(doc, jp)
        v = self.index.publish(f"ingest {job['file_name']}", [doc["doc_id"]])
        self.jobs.update(jid, status="indexed", stage=f"published v{v}", progress=1.0, doc_id=doc["doc_id"],
                         message=f"{len(doc['substations'])} substation(s), quality {doc['quality']['score']} ({doc['quality']['status']})")

    def _v1_for(self, file_name: str, path: Path) -> dict | None:
        v1_dir = self.s.path("paths.v1_parsed_dir")
        stem = Path(file_name).stem
        f = v1_dir / stem / f"{stem}.json"
        if not f.exists():
            return None
        from ..extraction.v1_import import convert
        doc = convert(f, None, self.s.get("rules"), self.s.get("extraction"))
        sha = sha256_file(path)
        doc["source"]["sha256"] = sha
        doc["doc_id"] = doc_id_for(sha)
        return doc

    # ------------------------------------------------------------ index ops
    def index_doc(self, doc: dict, json_path: Path, similar: bool = True) -> None:
        V, E = build_partition(doc)
        self.store.upsert_shared(shared_for(V, E), [])
        self.store.upsert_partition(doc["doc_id"], V, E)
        self.index.upsert(doc, str(json_path), self._embedder(), self.image_embedder)
        if similar:
            self.refresh_similar([doc["doc_id"]])

    def _embedder(self):
        if self.s.get("engines.rag.embedding", "none") != "openai":
            return None
        from ..extraction.llm import LLMClient
        c = LLMClient(self.s)
        return c.embed if c.available else None

    def refresh_similar(self, doc_ids: list[str] | None = None, top: int = 3, min_sim: float = 0.5) -> int:
        subs = self.index.substations()
        ras = {s["sid"]: R.unpack(s["raster"]) for s in subs}
        targets = [s for s in subs if doc_ids is None or s["doc_id"] in doc_ids]
        E = []
        for s in targets:
            if ras[s["sid"]] is None:
                continue
            sims = []
            for o in subs:
                if o["doc_id"] == s["doc_id"] or ras[o["sid"]] is None:
                    continue
                v = R.raster_similarity(ras[s["sid"]], ras[o["sid"]])
                if v >= min_sim:
                    sims.append((v, o["sid"]))
            for v, osid in sorted(sims, reverse=True)[:top]:
                E.append({"label": "SIMILAR_TO", "out": s["sid"], "in": osid, "pk": s["doc_id"], "props": {"score": round(v, 3), "by": "mask_raster"}})
        if E:
            self.store.upsert_shared([], E)
        return len(E)

    def import_v1(self, v1_dir: str | Path | None = None, pdf_dir: str | Path | None = None, log_fn=print) -> dict:
        from ..extraction.v1_import import convert, find_v1_files
        v1_dir = Path(v1_dir or self.s.path("paths.v1_parsed_dir"))
        pdf_dir = Path(pdf_dir or self.s.path("paths.data_dir"))
        self.ensure_ontology()
        done, failed = [], []
        files = find_v1_files(v1_dir)
        for i, f in enumerate(files):
            try:
                doc = convert(f, pdf_dir if pdf_dir.exists() else None, self.s.get("rules"), self.s.get("extraction"))
                jp = self.doc_path(doc["doc_id"])
                dump_json(doc, jp)
                self.index_doc(doc, jp, similar=False)
                done.append(doc["doc_id"])
                log_fn(f"[{i + 1}/{len(files)}] {doc['source']['file_name']}: {len(doc['substations'])} substation(s)")
            except Exception as e:
                failed.append({"file": str(f), "error": f"{type(e).__name__}: {e}"})
                log_fn(f"[{i + 1}/{len(files)}] FAILED {f.name}: {e}")
        n_sim = self.refresh_similar(None)
        v = self.index.publish(f"v1 import ({len(done)} docs)", done)
        return {"imported": len(done), "failed": failed, "similar_edges": n_sim, "version": v}

    def rebuild(self, log_fn=print) -> dict:
        """Re-index every stored extraction (no LLM calls)."""
        self.ensure_ontology()
        root = self.s.var("extractions")
        done = []
        for jp in sorted(root.glob("*/doc.json")):
            doc = load_json(jp)
            if self.index.document(doc["doc_id"]) and self.index.document(doc["doc_id"])["deleted"]:
                continue
            from ..extraction.normalise import enrich
            from ..core.schema import validate_doc
            doc = validate_doc(enrich(doc, self.s.get("rules"), self.s.get("extraction")))
            dump_json(doc, jp)
            self.index_doc(doc, jp, similar=False)
            done.append(doc["doc_id"])
            log_fn(f"re-indexed {doc['source']['file_name']}")
        n_sim = self.refresh_similar(None)
        v = self.index.publish(f"rebuild ({len(done)} docs)", done)
        return {"reindexed": len(done), "similar_edges": n_sim, "version": v}

    def approve(self, doc_id: str) -> dict:
        jp = self.doc_path(doc_id)
        doc = load_json(jp)
        doc["quality"]["status"] = "reviewed"
        doc["quality"]["reviewed_at"] = now_iso()
        dump_json(doc, jp)
        self.index_doc(doc, jp)
        v = self.index.publish(f"approve {doc['source']['file_name']}", [doc_id])
        for j in self.jobs.list(status="awaiting_review"):
            if j["doc_id"] == doc_id:
                self.jobs.update(j["job_id"], status="indexed", stage=f"approved, published v{v}", progress=1.0)
        return {"doc_id": doc_id, "version": v}

    def delete(self, doc_id: str) -> dict:
        self.store.delete_partition(doc_id)
        self.index.delete(doc_id)
        v = self.index.publish(f"delete {doc_id}", [doc_id])
        return {"deleted": doc_id, "version": v}
