"""Query model: free text and/or structured JSON -> partial requirement (presence rule) + query geometry.

Only what the user states (or the uploaded site plan provides) is filled in. Every scorer checks
`present(query)` and skips the components the query does not provide.

Structured geometry keys (all in one mm frame; the site plan upload fills the first and the last two)
  site_polygon_mm     outline of the proposed substation area
  columns_mm          [[pt...], ...]                       beams_mm   [{polygon_mm, bottom_mm}]
  cutouts_mm          [[pt...], ...]                       doors_mm   [{polygon_mm, width_mm, type}]
  trenches_mm         [[pt...] polylines]
  site_context        {features: [{cls: nalla|dp_road|tree|..., polygon_mm|polyline_mm, label}]}
  site_distances      {nalla: mm, dp_road: mm, ...}  (stated distances without geometry)
"""
from __future__ import annotations

import json
import re

from ..core.ontology import equipment_class
from ..geometry import polygons as P
from ..geometry.als import Item, RoomGeom
from .query_parser import parse as _parse_text

FACET_KEYS = ("installation", "arrangement", "context", "floor", "kind", "voltage_kv", "multiple_substations")

DIST_FEATURES = {"nalla": r"nalla|nala|drain|water\s*body", "dp_road": r"d\.?\s*p\.?\s*road", "internal_road": r"internal\s+road",
                 "tree": r"trees?", "compound_wall": r"compound\s+wall", "structure": r"(adjacent|existing)\s+(structure|building)"}


def _site_distances(text: str) -> dict:
    t = text.lower()
    out = {}
    for k, pat in DIST_FEATURES.items():
        m = re.search(rf"(?:{pat})\D{{0,25}}?(\d+(?:\.\d+)?)\s*(mm|m|mtr|metres?|meters?)\b", t) or \
            re.search(rf"(\d+(?:\.\d+)?)\s*(mm|m|mtr|metres?|meters?)\s+(?:away\s+)?from\s+(?:the\s+)?(?:{pat})", t)
        if m:
            v = float(m.group(1))
            out[k] = v if m.group(2) == "mm" else v * 1000
    return out


def build_query(text: str = "", structured: dict | None = None, siteplan: dict | None = None) -> dict:
    """Merge parsed text, explicit JSON fields and site-plan fields (later sources win)."""
    text = text or ""
    if text.strip().startswith("{"):
        structured = {**json.loads(text), **(structured or {})}
        text = structured.get("text") or ""
    q = _parse_text(text) if text else {"text": ""}
    if structured:
        q.update({k: v for k, v in structured.items() if v not in (None, "", [], {})})
        if isinstance(structured.get("equipment"), dict) and not structured.get("equipment_items"):
            q.pop("equipment_items", None)
            q = _parse_text(json.dumps(q))
    if text:
        d = _site_distances(text)
        if d:
            q["site_distances"] = {**q.get("site_distances", {}), **d}
        m = re.search(r"door\D{0,15}(\d+(?:\.\d+)?)\s*(mm|m)\b", text.lower())
        if m:
            v = float(m.group(1))
            q["door_width_mm"] = v if m.group(2) == "mm" else v * 1000
        m = re.search(r"beam\s+(?:bottom|soffit)\D{0,12}(\d+(?:\.\d+)?)\s*(mm|m)\b", text.lower())
        if m:
            v = float(m.group(1))
            q["beam_bottom_mm"] = v if m.group(2) == "mm" else v * 1000
    if siteplan:
        qf = siteplan.get("query_fields") or {}
        for k, v in qf.items():
            if k == "site":
                q.setdefault("site", {}).update(v)
            elif k not in q or k in ("site_polygon_mm", "site_context"):
                q[k] = v
        q["siteplan_file"] = siteplan.get("file")
    return q


def room_geom(q: dict) -> RoomGeom:
    rg = RoomGeom(clear_height_mm=q.get("clear_height_mm"), beam_bottom_mm=q.get("beam_bottom_mm"), label="query")
    rg.outline = P.poly(q.get("site_polygon_mm"))
    if rg.outline is None and q.get("length_mm") and q.get("width_mm"):
        rg.outline = P.poly(P.rect(0, 0, q["length_mm"], q["width_mm"]))
    g = rg.groups
    for c in q.get("columns_mm") or []:
        p = P.poly(c)
        if p is not None:
            g.setdefault("column", []).append(Item("column", p, {}))
    for b in q.get("beams_mm") or []:
        p = P.poly(b.get("polygon_mm") if isinstance(b, dict) else b)
        if p is not None:
            g.setdefault("beam", []).append(Item("beam", p, {"bottom_mm": (b.get("bottom_mm") if isinstance(b, dict) else None) or q.get("beam_bottom_mm")}))
    for c in q.get("cutouts_mm") or []:
        p = P.poly(c)
        if p is not None:
            g.setdefault("cutout", []).append(Item("cutout", p, {}))
    for d in q.get("doors_mm") or []:
        p = P.poly(d.get("polygon_mm"))
        if p is not None:
            g.setdefault("door", []).append(Item(d.get("type") or "door", p, {"width_mm": d.get("width_mm")}))
    for t in q.get("trenches_mm") or []:
        ln = P.line(t)
        if ln is not None:
            g.setdefault("trench", []).append(Item("trench", ln, {}))
    for e in q.get("equipment_positions_mm") or []:
        p = P.poly(e.get("polygon_mm"))
        if p is not None:
            g.setdefault("equipment", []).append(Item(equipment_class(e.get("type")), p, {"id": e.get("id")}))
    return rg


def facets(q: dict) -> dict:
    out = {}
    for k in FACET_KEYS:
        v = q.get(k)
        if v not in (None, "", False) or (k == "multiple_substations" and v is True):
            out[k] = int(v) if k == "voltage_kv" else v
    if q.get("platform") is not None:
        out["platform"] = bool(q["platform"])
    if q.get("shape") and q.get("site_polygon_mm") is None:
        out["shape"] = q["shape"]
    for k in ("building", "locality"):
        if q.get(k):
            out[k] = q[k]
    for k in ("access", "trench"):
        if (q.get("site") or {}).get(k):
            out[f"site_{k}"] = True
    return out


def present(q: dict) -> dict:
    """Which detail groups the query provides (drives the presence rule and the coverage value)."""
    rg = room_geom(q)
    return {
        "metadata": bool(facets(q)),
        "equipment": bool(q.get("equipment_items")),
        "kva": bool(q.get("kva")),
        "footprint": rg.outline is not None,
        "height": bool(q.get("clear_height_mm")),
        "openings": bool(q.get("civil")),
        "structure": rg.has("beam") or rg.has("column"),
        "cutouts": rg.has("cutout"),
        "trench": bool((q.get("site") or {}).get("trench")) or rg.has("trench"),
        "site": bool((q.get("site_context") or {}).get("features")) or bool(q.get("site_distances")),
    }


def describe(q: dict) -> list[str]:
    """Human-readable list of what the query asks for (shown in the UI and chat)."""
    out = []
    for k in FACET_KEYS:
        if q.get(k) not in (None, "", False):
            out.append(f"{k.replace('_', ' ')}: {q[k]}")
    for it in q.get("equipment_items") or []:
        out.append(f'{it.get("count", 1)} x {it.get("kva") or ""} {it["type"]}'.replace("  ", " "))
    if q.get("length_mm"):
        out.append(f'site {q["length_mm"] / 1000:.2f} x {q["width_mm"] / 1000:.2f} m')
    elif q.get("area_m2"):
        out.append(f'area {q["area_m2"]} m2')
    if q.get("site_polygon_mm"):
        out.append("site outline from plan")
    if q.get("clear_height_mm"):
        out.append(f'clear height {q["clear_height_mm"]:.0f} mm')
    for k, v in (q.get("civil") or {}).items():
        out.append(f"{k.replace('_', ' ')}" + ("" if v is True else f": {v}"))
    for k, v in (q.get("site_distances") or {}).items():
        out.append(f"{k.replace('_', ' ')} at {v / 1000:.1f} m")
    for k in ("building", "locality"):
        if q.get(k):
            out.append(f"{k}: {q[k]}")
    for k, v in (q.get("site") or {}).items():
        if v is True:
            out.append(f"site {k}")
    if (q.get("site_context") or {}).get("features"):
        out.append(f'{len(q["site_context"]["features"])} site feature(s) from plan')
    return out
