"""Import v1 extractions (css_parsed/<stem>/<stem>.json) into schema 2.1 without any LLM calls.

The v1 parser already holds reviewed layouts, room geometry, equipment boxes, openings, heights and the
site-plan marks (boundary, access path, trenches). This converts them into the v2 groups and frames so
the 50 historic drawings can be indexed immediately; new drawings go through the v2 pipeline.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.schema import validate_doc
from ..core.util import doc_id_for, load_json, now_iso, num, sha256_file, sha256_text
from ..geometry import polygons as P
from .normalise import enrich, metadata_from_classification, substation_from_px

SITE_CLASS = {"substation_boundaries": "site_boundary", "access_paths": "access_path", "trenches": "trench"}


def find_v1_files(v1_dir: str | Path) -> list[Path]:
    root = Path(v1_dir)
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        f = d / f"{d.name}.json"
        if f.exists():
            out.append(f)
    return out


def _layouts(v1: dict) -> list[dict]:
    out = []
    for p in v1.get("pages", []):
        for L in p.get("layouts", []):
            out.append({"id": L["id"], "page": p.get("page", 1), "type": L.get("type") or "unknown", "title": L.get("title"),
                        "bbox_px": L.get("bbox"), "dpi": p.get("dpi"), "mm_per_px": num(L.get("mm_per_px")),
                        "scale_text": L.get("scale_text"), "scale_source": "v1" if L.get("mm_per_px") else "none",
                        "crop_image": L.get("crop_image")})
    return out


def _site(v1: dict, layouts: list[dict]) -> dict:
    site_layouts = [L for L in layouts if L["type"] in ("site_plan", "key_plan", "wayleave") and L.get("bbox_px")]
    feats, sub_polys = [], []
    scaled = next((L for L in site_layouts if L.get("mm_per_px")), None)
    n = 0
    for key, cls in SITE_CLASS.items():
        for item in (v1.get("site") or {}).get(key, []):
            for g in item.get("geometry", []):
                pts = g.get("points") or []
                if not pts:
                    continue
                n += 1
                f = {"id": f"F{n}", "cls": cls, "label": item.get("label"), "layout_id": None, "polygon_px": None}
                lay = next((L for L in site_layouts if _inside(pts, L["bbox_px"])), None)
                f["layout_id"] = lay["id"] if lay else None
                lab = (item.get("label") or "").lower()
                if cls == "access_path" and ("wayleave" in lab or "24" in lab):
                    f["cls"] = "wayleave"
                if g.get("kind") == "polygon":
                    f["polygon_px"] = pts
                else:
                    f["polyline_px"] = pts
                s = (lay or {}).get("mm_per_px")
                if s:
                    org = lay["bbox_px"][:2]
                    if g.get("kind") == "polygon":
                        f["polygon_mm"] = P.px_to_mm(pts, org, s)
                    else:
                        f["polyline_mm"] = P.px_to_mm(pts, org, s)
                    if cls == "site_boundary" and f.get("polygon_mm"):
                        sub_polys.append(f["polygon_mm"])
                feats.append(f)
                if "dp road" in lab or "d p road" in lab or "d.p. road" in lab:
                    f.setdefault("mentions", []).append("dp_road")
    return {"layout_id": scaled["id"] if scaled else (site_layouts[0]["id"] if site_layouts else None),
            "mm_per_px": scaled["mm_per_px"] if scaled else None,
            "substation_polygons_mm": sub_polys, "features": feats, "clearance_checks": []}


def _inside(pts, bbox) -> bool:
    if not bbox:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def _text(v1: dict) -> str:
    parts = [v1.get("stem", ""), v1.get("notes", "")]
    c = v1.get("classification") or {}
    parts += [str(c.get(k, "")) for k in ("location_in_building", "evidence", "css_type_detail", "installation_detail", "project")]
    for p in v1.get("pages", []):
        parts += [L.get("title") or "" for L in p.get("layouts", [])]
        parts += [g.get("text") or "" for g in p.get("legend", [])]
    for s in v1.get("substations", []):
        parts.append(s.get("name") or "")
        parts += [f'{e.get("id")} {e.get("type")} {e.get("subtype") or ""} {e.get("kva") or ""}' for e in s.get("equipment", [])]
        parts += [o.get("label") or "" for o in s.get("openings", [])]
        parts.append(str((s.get("room") or {}).get("height_source") or ""))
    for k, v in (v1.get("site") or {}).items():
        parts += [x.get("label") or "" for x in v]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def convert(v1_path: str | Path, pdf_dir: str | Path | None = None, rules: dict | None = None, gate: dict | None = None) -> dict:
    v1_path = Path(v1_path)
    v1 = load_json(v1_path)
    src_name = v1.get("source_pdf") or (v1.get("stem", v1_path.stem) + ".pdf")
    pdf = Path(pdf_dir) / src_name if pdf_dir else None
    if pdf is not None and pdf.exists():
        sha = sha256_file(pdf)
    else:
        sha = sha256_text("v1:" + src_name)
    layouts = _layouts(v1)
    text = _text(v1)
    md = metadata_from_classification(v1.get("classification") or {}, text)
    md["legend"] = [{"text": g.get("text"), "semantic": g.get("semantic"), "color": g.get("color_name")}
                    for p in v1.get("pages", []) for g in p.get("legend", [])]
    subs = [substation_from_px(s, i) for i, s in enumerate(v1.get("substations", []))]
    doc = {
        "schema_version": "2.1",
        "doc_id": doc_id_for(sha),
        "source": {"file_name": src_name, "sha256": sha, "kind": "pdf", "pages": len(v1.get("pages", [])) or 1,
                   "path": str(pdf) if pdf is not None and pdf.exists() else None},
        "provenance": {"method": "v1_import", "created_at": now_iso(), "imported_from": str(v1_path),
                       "notes": [v1.get("pipeline", {}).get("stage_a", ""), v1.get("pipeline", {}).get("stage_b", "")]},
        "metadata": md,
        "layouts": layouts,
        "segments": [],
        "associations": [{"substation_id": s["id"], "plan_layout": s.get("equipment_layout_id"),
                          "section_layout": s.get("section_layout_id"), "method": "v1_review",
                          "reason": s.get("section_reason")} for s in subs],
        "substations": subs,
        "site": _site(v1, layouts),
        "quality": {"status": "reviewed" if v1.get("status") == "reviewed" else "provisional", "issues": []},
        "text": text,
    }
    rules = rules or {}
    enrich(doc, rules, gate)
    return validate_doc(doc)
