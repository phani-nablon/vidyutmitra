"""FastAPI application: retrieval, chat, uploads, incremental ingestion, ontology and admin endpoints,
plus the single-page UI at /. Run:  sr serve   (or  uvicorn substation_retrieval.api.app:app)"""
from __future__ import annotations

import logging
import mimetypes
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..chat.agent import ChatAgent
from ..core.ontology import ONTOLOGY, family_of, to_tree, walk
from ..retrieval.overlay import overlay, substation_geometry
from ..retrieval.query import build_query
from ..retrieval.siteplan import analyze as analyze_siteplan
from ..services import get_services

log = logging.getLogger("substation_retrieval.api")
STATIC = Path(__file__).with_name("static")
SITEPLANS: dict[str, dict] = {}


def _watch_loop(sv, stop: threading.Event):
    inbox = sv.settings.path("paths.watch_dir")
    inbox.mkdir(parents=True, exist_ok=True)
    done = inbox / "_submitted"
    done.mkdir(exist_ok=True)
    while not stop.is_set():
        for p in sorted(inbox.iterdir()):
            if p.is_file() and p.suffix.lower() in (".pdf", ".dxf", ".dwg"):
                try:
                    sv.ingest.submit_file(p, options={"source": "watch_folder"})
                    shutil.move(str(p), str(done / p.name))
                except Exception as e:  # pragma: no cover
                    log.warning("watch: %s: %s", p.name, e)
        stop.wait(float(sv.settings.get("server.watch_interval_s", 10)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    sv = get_services()
    sv.ingest.start_workers(int(sv.settings.get("server.workers_in_process", 1)))
    app.state.chat = ChatAgent(sv)
    stop = threading.Event()
    if sv.settings.get("server.watch_folder"):
        threading.Thread(target=_watch_loop, args=(sv, stop), daemon=True, name="watch").start()
    yield
    stop.set()
    sv.stop_workers()


app = FastAPI(title="CSS Design Retrieval", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


# ------------------------------------------------------------ models
class SearchIn(BaseModel):
    text: str = ""
    structured: Optional[dict[str, Any]] = None
    siteplan_id: Optional[str] = None
    mode: Optional[str] = None
    k: int = 10
    overlays: bool = True


class ChatIn(BaseModel):
    message: str
    session_id: Optional[str] = None
    siteplan_id: Optional[str] = None
    mode: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    k: int = 5


class EnginesIn(BaseModel):
    retrieval_mode: Optional[str] = None
    kg_enabled: Optional[bool] = None
    rag_enabled: Optional[bool] = None


class PathsIn(BaseModel):
    paths: list[str]
    force: bool = False
    use_v1: bool = True


def _add_overlays(sv, q: dict, res: dict, n: int = 10) -> None:
    for r in res.get("results", [])[:n]:
        doc = sv.index.load_doc(r["doc_id"])
        if doc:
            try:
                r["overlay"] = overlay(q, doc, r["substation_id"], sv.settings.get("scoring.align_refine_deg", 10))
            except Exception as e:  # pragma: no cover
                r["overlay"] = {"error": str(e)}


# ------------------------------------------------------------ health / admin
@app.get("/api/health")
def health():
    sv = get_services()
    return {"ok": True, "version": __version__, "index": sv.index.stats(), "graph": sv.store.stats(),
            "engines": sv.orchestrator.health(), "jobs": sv.jobs.counts(), "llm": sv.llm.describe()}


@app.get("/api/engines")
def engines_get():
    return get_services().orchestrator.flags()


@app.put("/api/engines")
def engines_put(body: EnginesIn):
    try:
        return get_services().orchestrator.set_flags(body.retrieval_mode, body.kg_enabled, body.rag_enabled)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/settings")
def settings_get():
    return get_services().settings.public()


@app.get("/api/versions")
def versions():
    return get_services().index.versions(50)


# ------------------------------------------------------------ retrieval
@app.post("/api/search")
def search(body: SearchIn):
    sv = get_services()
    sp = SITEPLANS.get(body.siteplan_id) if body.siteplan_id else None
    try:
        q = build_query(body.text, body.structured, sp)
        res = sv.orchestrator.search(q, body.k, body.mode)
    except Exception as e:
        log.exception("search failed")
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    if body.overlays:
        _add_overlays(sv, q, res)
    return res


@app.post("/api/chat")
def chat(body: ChatIn):
    sv = get_services()
    sp = SITEPLANS.get(body.siteplan_id) if body.siteplan_id else None
    try:
        out = app.state.chat.handle(body.message, body.session_id, sp, body.mode, body.structured, body.k)
    except Exception as e:
        log.exception("chat failed")
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    if out.get("search"):
        _add_overlays(sv, out["search"]["query"], out["search"], body.k)
    return out


@app.post("/api/siteplan")
async def siteplan(file: UploadFile = File(...), color: str = Form("red"), mm_per_px: Optional[float] = Form(None),
                   known_length_mm: Optional[float] = Form(None), known_area_m2: Optional[float] = Form(None)):
    sv = get_services()
    sid = uuid.uuid4().hex[:10]
    d = sv.settings.var("siteplans", sid, mkdir=False)
    d.mkdir(parents=True, exist_ok=True)
    p = d / Path(file.filename or "siteplan.png").name
    with open(p, "wb") as f:
        shutil.copyfileobj(file.file, f)
    res = analyze_siteplan(str(p), color, mm_per_px, known_length_mm, known_area_m2, str(d))
    res["id"] = sid
    if res.get("preview"):
        res["preview_url"] = f"/api/siteplan/{sid}/preview"
    SITEPLANS[sid] = res
    return res


@app.get("/api/siteplan/{sid}/preview")
def siteplan_preview(sid: str):
    r = SITEPLANS.get(sid)
    if not r or not r.get("preview"):
        raise HTTPException(404)
    return FileResponse(r["preview"])


# ------------------------------------------------------------ ingestion
@app.post("/api/ingest")
async def ingest(files: list[UploadFile] = File(...), force: bool = Form(False), use_v1: bool = Form(True),
                 review_gate: Optional[str] = Form(None)):
    sv = get_services()
    batch = uuid.uuid4().hex[:8]
    jobs = []
    for uf in files:
        name = Path(uf.filename or "drawing.pdf").name
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
            shutil.copyfileobj(uf.file, tmp)
        try:
            jobs.append(sv.ingest.submit_file(tmp.name, name, {"force": force, "use_v1": use_v1, "review_gate": review_gate}, batch))
        except ValueError as e:
            jobs.append({"file_name": name, "status": "rejected", "error": str(e)})
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return {"batch_id": batch, "jobs": jobs}


@app.post("/api/ingest/paths")
def ingest_paths(body: PathsIn):
    """Bulk ingest of files already on the server (e.g. the AEML drawing share)."""
    sv = get_services()
    paths = []
    for p in body.paths:
        pp = Path(p)
        if pp.is_dir():
            paths += [x for x in sorted(pp.iterdir()) if x.suffix.lower() in (".pdf", ".dxf", ".dwg")]
        elif pp.exists():
            paths.append(pp)
    if not paths:
        raise HTTPException(400, "no PDF/DXF/DWG files found")
    return sv.ingest.submit_many(paths, {"force": body.force, "use_v1": body.use_v1})


@app.get("/api/jobs")
def jobs(status: Optional[str] = None, batch_id: Optional[str] = None, limit: int = 200):
    return get_services().jobs.list(status, limit, batch_id)


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = get_services().jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    return j


@app.post("/api/admin/import-v1")
def import_v1():
    return get_services().ingest.import_v1(log_fn=log.info)


@app.post("/api/admin/rebuild")
def rebuild():
    return get_services().ingest.rebuild(log_fn=log.info)


# ------------------------------------------------------------ library
@app.get("/api/documents")
def documents():
    return get_services().index.documents()


def _doc(doc_id: str) -> dict:
    d = get_services().index.load_doc(doc_id)
    if d is None:
        p = get_services().ingest.doc_path(doc_id)
        if p.exists():
            from ..core.util import load_json
            return load_json(p)
        raise HTTPException(404, "unknown document")
    return d


@app.get("/api/documents/{doc_id}")
def document(doc_id: str):
    return _doc(doc_id)


@app.get("/api/documents/{doc_id}/geometry")
def document_geometry(doc_id: str):
    d = _doc(doc_id)
    return {"doc_id": doc_id, "file_name": d["source"]["file_name"], "metadata": d["metadata"], "quality": d["quality"],
            "substations": substation_geometry(d), "site": d.get("site"), "layouts": d.get("layouts"),
            "artifacts": _artifact_list(doc_id)}


@app.get("/api/documents/{doc_id}/graph")
def document_graph(doc_id: str):
    """Instance knowledge graph of one site (its partition + the ontology classes it uses)."""
    sv = get_services()
    part = sv.store.partition(doc_id)
    if not part["vertices"]:
        raise HTTPException(404, "document not in the graph")
    concept_family = {f"c:{n.id}": family_of(n.id) for n, _, _ in walk(ONTOLOGY)}
    nodes = []
    for v in part["vertices"]:
        p = v["props"]
        fam = concept_family.get(f"c:{p.get('cls')}") if p.get("cls") else None
        if v["label"] == "concept":
            fam = concept_family.get(v["vid"])
        nodes.append({"id": v["vid"], "label": v["label"], "name": p.get("label") or p.get("name") or p.get("cls") or v["vid"].split(":")[-1],
                      "cls": p.get("cls"), "family": fam, "props": {k: x for k, x in p.items() if k not in ("polygon_mm", "polyline_mm", "geo_wkt")}})
    return {"doc_id": doc_id, "nodes": nodes, "edges": part["edges"]}


@app.post("/api/documents/{doc_id}/approve")
def approve(doc_id: str):
    return get_services().ingest.approve(doc_id)


@app.delete("/api/documents/{doc_id}")
def delete(doc_id: str):
    return get_services().ingest.delete(doc_id)


def _artifact_list(doc_id: str) -> list[str]:
    root = get_services().settings.var("artifacts", doc_id, mkdir=False)
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


@app.get("/api/artifacts/{doc_id}/{path:path}")
def artifact(doc_id: str, path: str):
    root = get_services().settings.var("artifacts", doc_id, mkdir=False).resolve()
    p = (root / path).resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")


# ------------------------------------------------------------ ontology (always available)
@app.get("/api/ontology")
def ontology():
    sv = get_services()
    counts = {}
    try:
        isa = sv.store.schema_counts().get("is_a", {})
        counts = {k[2:]: v for k, v in isa.items()}
    except Exception:
        pass
    # roll counts up the tree
    def roll(node):
        c = counts.get(node.id, 0) + sum(roll(ch) for ch in node.children)
        counts[node.id] = c
        return c
    roll(ONTOLOGY)
    try:
        sc = sv.store.schema_counts()
    except Exception:
        sc = {"vertices": {}, "edges": {}}
    return {"tree": to_tree(ONTOLOGY, counts), "vertex_counts": sc.get("vertices"), "edge_counts": sc.get("edges"),
            "vertex_labels": ["document", "layout", "substation", "equipment", "opening", "structure", "route", "site_feature",
                              "concept", "facet", "catalogue_item"],
            "edge_labels": ["HAS_LAYOUT", "HAS_SUBSTATION", "HAS_FEATURE", "SECTION_OF", "DRAWN_IN", "CONTAINS", "ON_WALL", "UNDER",
                            "BELOW_BEAM", "CLEARANCE", "FACES", "CONNECTS", "DISTANCE_TO", "IS_A", "SUBCLASS_OF",
                            "MATCHES_CATALOGUE", "HAS_FACET", "SIMILAR_TO"]}


@app.exception_handler(Exception)
async def unhandled(request, exc):  # pragma: no cover
    log.exception("unhandled")
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)
