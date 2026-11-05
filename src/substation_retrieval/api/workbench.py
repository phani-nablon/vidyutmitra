"""Endpoints behind the two-page workbench UI (frontend/, served at /).

Page 1  Create new   query builder or plain-text prompt -> /api/search, /api/chat (already in app.py)
Page 2  Review       drawing list sorted by open actions -> fields + image -> edit -> submit

Edits are written into the drawing's extraction JSON (var/extractions/<doc>/doc.json) and logged under
`review.edits` in the same file. Nothing reaches the graph or the RAG index until the reviewer submits:
submit = the existing approve path, which marks the drawing reviewed, re-indexes it and publishes a version.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import REPO_ROOT
from ..core.glossary import FIELDS
from ..core.rules import RuleStore
from ..core.util import dump_json, load_json, now_iso
from ..services import get_services
from .marks import Marks
from .review import EQUIP_KEYS, FEATURE_KEYS, OPEN_KEYS, ROOM_KEYS, ROUTE_KEYS, STRUCT_KEYS, build_review

router = APIRouter(prefix="/api/workbench", tags=["workbench"])
_write_lock = threading.Lock()

# Geometry and provenance stay out of the form: a reviewer corrects numbers and labels, not outlines.
_NOT_EDITABLE = {"polygon_mm", "polygon_px", "polyline_mm", "bbox_px", "centre_mm", "catalogue", "kva_source", "height_source",
                 "ontology_class", "mm_per_px", "cls"}
EDITABLE = {kind: [k for k in keys if k not in _NOT_EDITABLE] for kind, keys in {
    "room": ROOM_KEYS, "equipment": EQUIP_KEYS, "opening": OPEN_KEYS, "cutout": OPEN_KEYS, "structure": STRUCT_KEYS,
    "route": ROUTE_KEYS, "site_feature": FEATURE_KEYS}.items()}
# Room fields that live on the substation itself rather than under `civil`.
_ROOM_ON_SUB = {"name", "floor"}
# A room without these cannot be matched on size or height, so a missing one is an open action.
KEY_ROOM_FIELDS = ("length_mm", "width_mm", "clear_height_mm")


def _ftype(name: str) -> tuple[str, str]:
    if name in FIELDS:
        t, u, _ = FIELDS[name]
        return t, u
    n = name.lower()
    if n.endswith(("_mm", "_m2", "_kg", "_m")) or n in ("kva",):
        return "number", "mm" if n.endswith("_mm") else ""
    return "string", ""


def _marks() -> Marks:
    return Marks(get_services().settings.var("review", "marks"))


def _rules() -> list[dict]:
    sv = get_services()
    store = RuleStore(REPO_ROOT / "config" / "rules_seed.json", sv.settings.var("rules.json", mkdir=False))
    return store.load().get("rules") or []


def _load(doc_id: str) -> tuple[dict, Path]:
    """Read the extraction from disk, not the index cache: edits land on disk before the index sees them."""
    sv = get_services()
    p = sv.ingest.doc_path(doc_id)
    if p.exists():
        return load_json(p), p
    row = sv.index.document(doc_id)
    if row and row.get("json_path"):
        jp = Path(row["json_path"])
        jp = jp if jp.is_absolute() else sv.settings.var_dir / jp
        if jp.exists():
            return load_json(jp), jp
    raise HTTPException(404, "unknown document")


# ------------------------------------------------------------ open actions
def open_actions(doc: dict, marks: dict, awaiting: bool) -> list[dict]:
    """What a reviewer still has to do on one drawing, most important first."""
    out: list[dict] = []
    q = doc.get("quality") or {}
    if awaiting:
        out.append({"kind": "gate", "text": "Held at the review gate: not searchable until submitted."})
    elif q.get("status") != "reviewed":
        out.append({"kind": "unreviewed", "text": f"Extraction is {q.get('status') or 'provisional'}; nobody has checked it yet."})
    for msg in q.get("issues") or []:
        out.append({"kind": "issue", "text": msg})
    for s in doc.get("substations") or []:
        civ = s.get("civil") or {}
        miss = [k for k in KEY_ROOM_FIELDS if civ.get(k) in (None, "", 0)]
        if miss:
            out.append({"kind": "missing", "item_id": f"{s['id']}:room",
                        "text": f"{s.get('name') or s['id']}: {', '.join(m.replace('_mm', '').replace('_', ' ') for m in miss)} not read"})
    for c in (doc.get("site") or {}).get("clearance_checks") or []:
        if c.get("meets_norm") is False:
            out.append({"kind": "clearance", "text": f"{c.get('feature') or c.get('cls')} is closer than the norm"})
    for item_id, m in marks.items():
        if m.get("verdict") in ("wrong", "unsure"):
            out.append({"kind": "flagged", "item_id": item_id, "text": f"Marked {m['verdict']}: {m.get('note') or item_id}"})
    return out


@router.get("/queue")
def queue():
    """Every drawing with its open actions, sorted so the most work is on top."""
    sv = get_services()
    mk = _marks()
    awaiting = {j["doc_id"]: j for j in sv.jobs.list(status="awaiting_review", limit=1000) if j.get("doc_id")}
    rows = {d["doc_id"]: d for d in sv.index.documents()}
    out = []
    for doc_id in list(rows) + [d for d in awaiting if d not in rows]:
        try:
            doc, _ = _load(doc_id)
        except HTTPException:
            continue
        acts = open_actions(doc, mk.load(doc_id), doc_id in awaiting)
        q = doc.get("quality") or {}
        md = doc.get("metadata") or {}
        subs = doc.get("substations") or []
        out.append({"doc_id": doc_id, "file_name": doc["source"]["file_name"], "status": "awaiting_review" if doc_id in awaiting else q.get("status"),
                    "score": q.get("score"), "open_actions": len(acts), "actions": acts[:6],
                    "installation": md.get("installation"), "floor": md.get("floor"), "n_substations": len(subs),
                    "n_equipment": sum(len((s.get("electrical") or {}).get("equipment") or []) for s in subs),
                    "reviewed_at": q.get("reviewed_at"), "updated_at": (rows.get(doc_id) or {}).get("updated_at")})
    out.sort(key=lambda r: (-r["open_actions"], r["score"] if r["score"] is not None else 1.0, r["file_name"].lower()))
    return {"documents": out, "total_open": sum(r["open_actions"] for r in out)}


# ------------------------------------------------------------ one drawing
@router.get("/documents/{doc_id}")
def review_doc(doc_id: str):
    sv = get_services()
    doc, _ = _load(doc_id)
    rv = build_review(sv.settings, doc, _rules())
    marks = _marks().load(doc_id)
    for g in rv["groups"]:
        for it in g["items"]:
            have = {v["name"] for v in it["values"]}
            for v in it["values"]:
                v["editable"] = v["name"] in EDITABLE.get(it["kind"], []) and v["type"] in ("string", "integer", "number", "boolean", "enum")
            # fields the drawing did not give: offered as blanks so the reviewer can fill them in
            it["missing"] = [{"name": k, "type": _ftype(k)[0], "unit": _ftype(k)[1], "help": FIELDS.get(k, ("", "", ""))[2]}
                             for k in EDITABLE.get(it["kind"], []) if k not in have]
            it["mark"] = marks.get(it["id"])
    awaiting = any(j.get("doc_id") == doc_id for j in sv.jobs.list(status="awaiting_review", limit=1000))
    q = doc.get("quality") or {}
    rv.update(status="awaiting_review" if awaiting else q.get("status"), score=q.get("score"), issues=q.get("issues") or [],
              actions=open_actions(doc, marks, awaiting), metadata=doc.get("metadata") or {},
              edits=(doc.get("review") or {}).get("edits", [])[-50:], marks=marks)
    return rv


class MarkIn(BaseModel):
    item_id: str
    verdict: Optional[str] = None
    note: str = ""


def _who(request: Request) -> str | None:
    u = getattr(request.state, "user", None)
    return u["username"] if u else None


@router.put("/documents/{doc_id}/marks")
def set_mark(doc_id: str, body: MarkIn, request: Request):
    try:
        return _marks().set(doc_id, body.item_id, body.verdict, body.note, by=_who(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


class EditIn(BaseModel):
    item_id: str
    name: str
    value: Any = None


class EditsIn(BaseModel):
    edits: list[EditIn]
    by: Optional[str] = None


def _target(doc: dict, item_id: str, name: str) -> tuple[dict, str]:
    """(dict that holds the field, item kind) for a review item id such as 'S1:room', 'S1:DT-1' or 'site:F3'."""
    if item_id.startswith("site:"):
        fid = item_id[5:]
        for f in (doc.get("site") or {}).get("features") or []:
            if f.get("id") == fid:
                return f, "site_feature"
        raise HTTPException(404, f"no site feature {fid}")
    for sub in doc.get("substations") or []:
        if not item_id.startswith(f"{sub['id']}:"):
            continue
        rest = item_id[len(sub["id"]) + 1:]
        if rest == "room":
            return (sub if name in _ROOM_ON_SUB else sub.setdefault("civil", {})), "room"
        el, ac, ve, ca, ci = (sub.get(k) or {} for k in ("electrical", "access", "ventilation", "cable", "civil"))
        for kind, pool in (("equipment", el.get("equipment")), ("opening", ac.get("openings")), ("opening", ve.get("openings")),
                           ("cutout", ca.get("cutouts")), ("structure", ci.get("structures")), ("route", ca.get("routes"))):
            for o in pool or []:
                if o.get("id") == rest:
                    return o, kind
    raise HTTPException(404, f"no item {item_id}")


def _coerce(name: str, value: Any, old: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    t = "boolean" if isinstance(old, bool) else _ftype(name)[0] if old is None else \
        "integer" if isinstance(old, int) else "number" if isinstance(old, float) else "string"
    try:
        if t == "boolean":
            return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "y")
        if t == "integer":
            return int(round(float(value)))
        if t == "number":
            return float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name}: '{value}' is not a number")
    return str(value).strip()


def apply_edits(doc: dict, edits: list[EditIn], by: str | None = None) -> list[dict]:
    log = []
    for e in edits:
        tgt, kind = _target(doc, e.item_id, e.name)
        if e.name not in EDITABLE.get(kind, []):
            raise HTTPException(400, f"{e.name} cannot be edited on a {kind}")
        old = tgt.get(e.name)
        new = _coerce(e.name, e.value, old)
        if new == old:
            continue
        if new is None:
            tgt.pop(e.name, None)
        else:
            tgt[e.name] = new
        if kind == "equipment" and e.name == "kva" and new is not None:
            tgt["kva_source"] = "reviewer"
        if kind == "room" and e.name in ("length_mm", "width_mm"):
            L, B = tgt.get("length_mm"), tgt.get("width_mm")
            if L and B:
                tgt["area_m2"] = round(L * B / 1e6, 2)
        log.append({"item_id": e.item_id, "name": e.name, "old": old, "new": new, "at": now_iso(), "by": by})
    if log:
        r = doc.setdefault("review", {})
        r.setdefault("edits", []).extend(log)
        r["edited_at"] = now_iso()
    return log


@router.patch("/documents/{doc_id}/fields")
def edit_fields(doc_id: str, body: EditsIn, request: Request):
    with _write_lock:
        doc, path = _load(doc_id)
        log = apply_edits(doc, body.edits, _who(request) or body.by)
        if log:
            dump_json(doc, path)
    return {"doc_id": doc_id, "changed": log}


class UnderstandIn(BaseModel):
    text: str = ""
    structured: Optional[dict[str, Any]] = None


@router.post("/understand")
def understand(body: UnderstandIn):
    """Read a design request without searching: what the parser took from the text and the selections,
    so the person confirms it before matching (design doc, C8: confirm before anything else happens)."""
    from ..retrieval.query import build_query, describe
    q = build_query(body.text, body.structured)
    items = [{k: it.get(k) for k in ("type", "count", "kva", "ways", "subtype", "dt_kind", "text")} for it in q.get("equipment_items") or []]
    return {"asked": describe(q), "equipment": items,
            "fields": {k: v for k, v in q.items() if not k.startswith("_") and k not in ("text", "equipment_items")}}


class SubmitIn(BaseModel):
    edits: list[EditIn] = []
    by: Optional[str] = None


@router.post("/documents/{doc_id}/submit")
def submit(doc_id: str, body: SubmitIn, request: Request):
    """Save any pending edits, then approve: reviewed status, re-index (graph + RAG), new index version."""
    sv = get_services()
    by = _who(request) or body.by
    with _write_lock:
        doc, path = _load(doc_id)
        log = apply_edits(doc, body.edits, by)
        doc.setdefault("review", {}).update(submitted_at=now_iso(), submitted_by=by)
        dump_json(doc, path)
        if path != sv.ingest.doc_path(doc_id):
            dump_json(doc, sv.ingest.doc_path(doc_id))
    try:
        res = sv.ingest.approve(doc_id)
    except Exception as e:
        raise HTTPException(502, f"Edits saved, but indexing failed ({type(e).__name__}: {e}). Is Neo4j running? Submit again once it is.")
    return {**res, "changed": log}
