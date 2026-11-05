"""Classic repository browse over the VidyutMitra SubstationRepository (read only).

The VidyutMitra platform files every drawing by the five axes of the detailed design
(Category -> Division -> Project -> Stage -> Revision):

    <repo>/drawings/<category>/<division>/<zldp>_<developer>/{inputs,tentative,detailed,pwd}/<file>
    <repo>/_system/metadata.json     areas, projects, drawings (the Azure SQL stand-in)

This module only reads that repository; uploads, versioning and approval stay with VidyutMitra, which owns
the folder (it is a synced share, so a write here would reach SharePoint). The metadata file is re-read
whenever its mtime or size changes, for the same reason VidyutMitra's jsonStore does: other processes
(its server, `npm run seed`) rewrite it underneath us.

Each repository drawing is linked to this library's extraction of the same file, by content hash when the
file is on disk and by name otherwise, so a project page can jump to the review page or seed a new design.

Location: `repository.path` in config/settings.yaml or SR_REPO_PATH; default is VidyutMitra's default
LOCAL_BUCKET_PATH under the user's home folder.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import get_services

router = APIRouter(prefix="/api/repo", tags=["repository"])

DEFAULT_REPO = Path.home() / "Adani" / "AEML _ExternalShare - VidyutMitra project" / "CSS_DSS Drawings for Automation" / "SubstationRepository"
CATEGORIES = {"css": {"key": "css", "label": "CSS", "long": "Consumer Substation"},
              "dss": {"key": "dss", "label": "DSS", "long": "Distribution Substation"}}
STAGES = [{"key": "inputs", "code": "INP", "label": "Inputs", "about": "Raw plot from the developer and the equipment information"},
          {"key": "tentative", "code": "TEN", "label": "Tentative", "about": "First-pass layout for the developer to agree"},
          {"key": "detailed", "code": "DET", "label": "Detailed", "about": "The issued design: equipment, clearances, sections"},
          {"key": "pwd", "code": "PWD", "label": "PWD", "about": "Detailed layout prepared for authority submission"}]
STAGE_KEYS = [s["key"] for s in STAGES]

_lock = threading.Lock()
_cache: dict = {"stamp": None, "data": None}
_sha_cache: dict[tuple, str] = {}


def repo_root() -> Path:
    sv = get_services()
    p = sv.settings.get("repository.path")
    return Path(str(p)).expanduser() if p else DEFAULT_REPO


def _meta_path() -> Path:
    return repo_root() / "_system" / "metadata.json"


def load() -> dict | None:
    p = _meta_path()
    try:
        st = p.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["stamp"] != stamp:
            d = json.loads(p.read_text(encoding="utf-8"))
            _cache.update(stamp=stamp, data={k: d.get(k) or [] for k in ("areas", "projects", "drawings")})
        return _cache["data"]


def _need() -> dict:
    d = load()
    if d is None:
        raise HTTPException(404, f"No VidyutMitra repository found at {repo_root()}. Set repository.path in "
                                 "config/settings.yaml (or SR_REPO_PATH) to the SubstationRepository folder.")
    return d


# ------------------------------------------------------------ linking to this library
def _norm(name: str | None) -> str:
    stem = Path(str(name or "")).stem.lower().split("__")[0]          # our PDFs carry "__Layout1" / "__Model" suffixes
    return re.sub(r"[^a-z0-9]+", "", stem)


def _library() -> tuple[dict, dict]:
    docs = get_services().index.documents()
    by_name: dict[str, str] = {}
    for d in docs:
        by_name.setdefault(_norm(d["file_name"]), d["doc_id"])
    return by_name, {d["sha256"]: d["doc_id"] for d in docs if d.get("sha256")}


def _sha(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


def _file(drawing: dict) -> Path | None:
    root = repo_root().resolve()
    p = (root / str(drawing.get("blobKey") or "")).resolve()
    return p if root in p.parents and p.is_file() else None


def _link(drawing: dict, by_name: dict, by_sha: dict | None = None) -> str | None:
    if by_sha is not None:
        f = _file(drawing)
        if f is not None:
            sha = _sha(f)
            if sha in by_sha:
                return by_sha[sha]
    return by_name.get(_norm(drawing.get("originalName"))) or by_name.get(_norm(drawing.get("filename")))


# ------------------------------------------------------------ shapes
def _proj_summary(p: dict, drawings: list[dict]) -> dict:
    mine = [d for d in drawings if d.get("projectId") == p["id"]]
    stages = {s: max((d.get("version") or 0 for d in mine if d.get("stage") == s), default=0) for s in STAGE_KEYS}
    last = max((d.get("uploadedAt") or "" for d in mine), default=p.get("createdAt"))
    return {"id": p["id"], "category": p.get("category"), "division": p.get("area"), "zldp": p.get("zlp"),
            "zldp_status": "allocated" if p.get("zlp") else "unassigned", "developer": p.get("developer"),
            "site": p.get("site"), "folder": p.get("folder"), "stages": stages, "drawings": len(mine), "last_activity": last}


def _params_summary(params: dict | None) -> dict | None:
    if not params:
        return None
    room = params.get("room") or {}
    return {"construction": params.get("constructionType"), "placement": params.get("placement"),
            "placement_sub": params.get("placementSubType"), "floor": params.get("floor"), "length_m": room.get("lengthM"),
            "width_m": room.get("widthM"), "area_sqm": params.get("areaSqm") or room.get("areaSqm"),
            "clear_height_mm": params.get("clearHeightMm"), "drawing_no": params.get("drawingNo"), "revision": params.get("revision"),
            "engine": params.get("engine"),
            "equipment": [{"label": e.get("label"), "category": e.get("category"), "rating": e.get("rating"), "quantity": e.get("quantity"),
                           "provisional": bool(e.get("provisional"))} for e in params.get("equipment") or []]}


def _drawing(d: dict, link: str | None, current: bool) -> dict:
    return {"id": d["id"], "stage": d.get("stage"), "version": d.get("version"), "filename": d.get("filename"),
            "original_name": d.get("originalName"), "size": d.get("size"), "uploaded_by": d.get("uploadedBy"),
            "uploaded_at": d.get("uploadedAt"), "kind": d.get("kind"), "note": d.get("note"), "source": d.get("source"),
            "current": current, "doc_id": link, "on_disk": _file(d) is not None, "params": _params_summary(d.get("params"))}


# ------------------------------------------------------------ endpoints
@router.get("/status")
def status():
    d = load()
    root = repo_root()
    if d is None:
        return {"configured": False, "path": str(root),
                "message": "No VidyutMitra repository at this path. Set repository.path (or SR_REPO_PATH) to the SubstationRepository folder."}
    return {"configured": True, "path": str(root), "areas": len(d["areas"]), "projects": len(d["projects"]), "drawings": len(d["drawings"])}


@router.get("/categories")
def categories():
    d = _need()
    out = []
    for key, c in CATEGORIES.items():
        projs = [p for p in d["projects"] if p.get("category") == key]
        ids = {p["id"] for p in projs}
        out.append({**c, "divisions": sum(1 for a in d["areas"] if a.get("category") == key), "projects": len(projs),
                    "drawings": sum(1 for x in d["drawings"] if x.get("projectId") in ids)})
    return out


@router.get("/{category}/divisions")
def divisions(category: str):
    d = _need()
    if category not in CATEGORIES:
        raise HTTPException(404, "category must be css or dss")
    out = []
    for a in sorted((a for a in d["areas"] if a.get("category") == category), key=lambda a: a.get("name") or ""):
        projs = [p for p in d["projects"] if p.get("category") == category and p.get("area") == a.get("slug")]
        ids = {p["id"] for p in projs}
        out.append({"slug": a.get("slug"), "name": a.get("name"), "projects": len(projs),
                    "unassigned": sum(1 for p in projs if not p.get("zlp")),
                    "drawings": sum(1 for x in d["drawings"] if x.get("projectId") in ids)})
    return {"category": CATEGORIES[category], "divisions": out}


@router.get("/{category}/{division}/projects")
def projects(category: str, division: str, q: Optional[str] = None):
    d = _need()
    area = next((a for a in d["areas"] if a.get("category") == category and a.get("slug") == division), None)
    if area is None:
        raise HTTPException(404, "no such division")
    needle = (q or "").strip().lower()
    rows = [_proj_summary(p, d["drawings"]) for p in d["projects"] if p.get("category") == category and p.get("area") == division]
    if needle:
        rows = [r for r in rows if needle in " ".join(str(r.get(k) or "") for k in ("zldp", "developer", "site", "folder")).lower()]
    rows.sort(key=lambda r: r["last_activity"] or "", reverse=True)
    return {"category": CATEGORIES.get(category), "division": {"slug": area.get("slug"), "name": area.get("name")}, "projects": rows}


@router.get("/projects/{project_id}")
def project(project_id: str):
    d = _need()
    p = next((x for x in d["projects"] if x["id"] == project_id), None)
    if p is None:
        raise HTTPException(404, "no such project")
    mine = [x for x in d["drawings"] if x.get("projectId") == project_id]
    by_name, by_sha = _library()
    stages = []
    for s in STAGES:
        revs = sorted((x for x in mine if x.get("stage") == s["key"]), key=lambda x: (x.get("version") or 0, x.get("uploadedAt") or ""), reverse=True)
        top = revs[0]["version"] if revs else None
        stages.append({**s, "revisions": [_drawing(x, _link(x, by_name, by_sha), x.get("version") == top) for x in revs]})
    # parameters on file: the newest parsed detailed drawing, else tentative
    on_file = None
    for key in ("detailed", "tentative"):
        for x in next(st for st in stages if st["key"] == key)["revisions"]:
            if x["params"] and (x["params"].get("area_sqm") or x["params"].get("equipment")):
                on_file = {**x["params"], "from": x["filename"], "stage": key}
                break
        if on_file:
            break
    area = next((a for a in d["areas"] if a.get("category") == p.get("category") and a.get("slug") == p.get("area")), {})
    return {"project": {**_proj_summary(p, d["drawings"]), "division_name": area.get("name") or p.get("area"),
                        "created_by": p.get("createdBy"), "created_at": p.get("createdAt")},
            "stages": stages, "parameters": on_file}


@router.get("/search")
def search(q: Optional[str] = None, category: Optional[str] = None, division: Optional[str] = None, stage: Optional[str] = None,
           min_area: Optional[float] = None, max_area: Optional[float] = None, equipment: Optional[str] = None, limit: int = 200):
    """B6 Find a drawing: the same parameters the similarity search compares, over the repository metadata."""
    d = _need()
    projects_by_id = {p["id"]: p for p in d["projects"]}
    by_name, _ = _library()
    needle = (q or "").strip().lower()
    out = []
    for x in d["drawings"]:
        p = projects_by_id.get(x.get("projectId"))
        if not p or (category and p.get("category") != category) or (division and p.get("area") != division) or (stage and x.get("stage") != stage):
            continue
        prm = x.get("params") or {}
        area = prm.get("areaSqm") or (prm.get("room") or {}).get("areaSqm")
        if (min_area is not None and (area is None or area < min_area)) or (max_area is not None and (area is None or area > max_area)):
            continue
        if equipment and not any(equipment.lower() in str(e.get("category") or "").lower() for e in prm.get("equipment") or []):
            continue
        hay = " ".join(str(v or "") for v in (x.get("filename"), x.get("originalName"), p.get("zlp"), p.get("developer"), p.get("site"))).lower()
        if needle and needle not in hay:
            continue
        out.append({**_drawing(x, _link(x, by_name), False), "project": _proj_summary(p, d["drawings"])})
        if len(out) >= limit:
            break
    return out


@router.get("/files/{drawing_id}")
def file(drawing_id: str, download: bool = False):
    d = _need()
    x = next((y for y in d["drawings"] if y["id"] == drawing_id), None)
    f = _file(x) if x else None
    if f is None:
        raise HTTPException(404, "file is not in the repository folder (it may be a cloud-only file on the share)")
    name = x.get("filename") or f.name
    return FileResponse(f, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream", filename=name,
                        content_disposition_type="attachment" if download else "inline")
