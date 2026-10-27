"""Pixel-space extraction -> schema 2.1 (shared by the LLM pipeline, the v1 importer and DXF).

`substation_from_px()` takes a raw substation description in page pixels (room bbox/polygon, equipment
and opening boxes, footprints and positions in mm when read from dimension text) plus the layout's
mm-per-pixel scale, and produces a schema-2.1 substation in the room mm frame.
`enrich()` then adds what every pipeline needs: catalogue rating inference, opening walls, counts,
document facets, the historic plot-clearance self check, segments and the quality gate.
"""
from __future__ import annotations

import re
from typing import Any

from ..core import catalogue
from ..core.ontology import base_equipment_type, equipment_class, opening_class
from ..core.util import derive_context, derive_floor, num
from ..geometry import polygons as P
from ..geometry.compat import site_clearances

ACCESS_TYPES = {"door", "rolling_shutter", "fire_exit", "gate", "staircase", "flood_gate"}
VENT_TYPES = {"ventilation", "louvre", "louver", "window", "exhaust"}


def _wall_of(poly_mm, L, B) -> str | None:
    if not poly_mm or not L or not B:
        return None
    g = P.poly(poly_mm)
    if g is None:
        return None
    c = g.centroid
    d = {"N": abs(c.y), "S": abs(B - c.y), "W": abs(c.x), "E": abs(L - c.x)}
    w = min(d, key=d.get)
    return w if d[w] < 0.2 * max(L, B) else None


def _equipment_polygon(e: dict, origin, s) -> tuple[list | None, list | None]:
    """Prefer dimensioned footprint + position (mm); fall back to the scaled pixel box."""
    fp = e.get("footprint_mm")
    pos = e.get("position_in_room_mm") or {}
    b = e.get("bbox")
    if fp and len(fp) == 2 and pos.get("x_from_left") is not None and pos.get("y_from_top") is not None:
        a, c = float(fp[0]), float(fp[1])
        if b and len(b) == 4:
            wide = (b[2] - b[0]) >= (b[3] - b[1])
            xext, yext = (max(a, c), min(a, c)) if wide else (min(a, c), max(a, c))
        else:
            xext, yext = a, c
        x0, y0 = float(pos["x_from_left"]), float(pos["y_from_top"])
        poly = P.rect(x0, y0, x0 + xext, y0 + yext)
        return poly, [x0 + xext / 2, y0 + yext / 2]
    if b and len(b) == 4 and s:
        poly = P.bbox_px_to_mm(b, origin, s)
        g = P.poly(poly)
        return poly, ([round(g.centroid.x, 1), round(g.centroid.y, 1)] if g else None)
    return None, None


def substation_from_px(raw: dict, idx: int, default_scale: float | None = None) -> dict:
    """raw: v1-style substation dict (room/equipment/openings in page px)."""
    room = raw.get("room") or {}
    s = num(raw.get("mm_per_px")) or default_scale
    rb = room.get("bbox")
    origin = (rb[0], rb[1]) if rb else (0.0, 0.0)
    civ: dict[str, Any] = {"structures": []}
    if room.get("polygon") and s:
        civ["polygon_mm"] = P.px_to_mm(room["polygon"], origin, s)
    elif room.get("polygon_mm"):
        civ["polygon_mm"] = room["polygon_mm"]
    elif rb and s:
        civ["polygon_mm"] = P.bbox_px_to_mm(rb, origin, s)
    L = num(room.get("length_mm"))
    B = num(room.get("width_mm"))
    g = P.poly(civ.get("polygon_mm"))
    if g is not None:
        d = P.dims(g)
        x0, y0, x1, y1 = g.bounds
        # trust dimension text over the scaled box for the overall size, keep the scaled shape
        if L and B and (x1 - x0) and (y1 - y0):
            sx = (L if (x1 - x0) >= (y1 - y0) else B) / (x1 - x0)
            sy = (B if (x1 - x0) >= (y1 - y0) else L) / (y1 - y0)
            if 0.8 < sx < 1.25 and 0.8 < sy < 1.25:
                civ["polygon_mm"] = [[round(p[0] * sx, 1), round(p[1] * sy, 1)] for p in civ["polygon_mm"]]
                g = P.poly(civ["polygon_mm"])
        if not (L and B) and d:
            L, B = d
    if g is None and L and B:
        civ["polygon_mm"] = P.rect(0, 0, L, B)
        g = P.poly(civ["polygon_mm"])
    if rb:
        civ["bbox_px"] = list(rb)
    if room.get("polygon"):
        civ["polygon_px"] = room["polygon"]
    civ.update({
        "length_mm": L, "width_mm": B,
        "area_m2": num(room.get("area_m2")) or (round(g.area / 1e6, 2) if g is not None else None),
        "shape": room.get("shape") if room.get("shape") and len(str(room.get("shape"))) < 40 else P.shape_class(g),
        "clear_height_mm": num(room.get("clear_height_mm")),
        "slab_to_soffit_mm": num(room.get("slab_to_soffit_mm")),
        "beam_bottom_mm": num(room.get("beam_bottom_mm")),
        "plinth_mm": num(room.get("plinth_mm")),
        "height_source": room.get("height_source"),
    })
    if room.get("plinth_type") or civ.get("plinth_mm"):
        civ["platform"] = {"type": room.get("plinth_type"), "height_mm": civ.get("plinth_mm")}
    Lx = (g.bounds[2] - g.bounds[0]) if g is not None else L
    By = (g.bounds[3] - g.bounds[1]) if g is not None else B

    equipment = []
    for i, e in enumerate(raw.get("equipment") or []):
        poly, centre = _equipment_polygon(e, origin, s)
        fp = e.get("footprint_mm") or e.get("dims_text_mm")
        Lm = Bm = None
        if fp and len(fp) >= 2:
            Lm, Bm = max(num(fp[0]) or 0, num(fp[1]) or 0) or None, min(num(fp[0]) or 0, num(fp[1]) or 0) or None
        elif poly:
            d = P.dims(P.poly(poly))
            Lm, Bm = (round(d[0]), round(d[1])) if d else (None, None)
        equipment.append({
            "id": str(e.get("id") or f"E{i + 1}"), "label": e.get("label") or e.get("id"),
            "equipment_type": (e.get("type") or e.get("equipment_type") or "aux").lower(),
            "subtype": e.get("subtype"), "kva": int(num(e.get("kva"))) if num(e.get("kva")) else None,
            "kva_source": "drawing_text" if num(e.get("kva")) else None,
            "L_mm": Lm, "B_mm": Bm, "H_mm": num(e.get("height_mm") or e.get("H_mm")),
            "weight_kg": catalogue.parse_weight_kg(e.get("weight") if e.get("weight") is not None else e.get("weight_kg")),
            "centre_mm": centre, "polygon_mm": poly, "ceiling_clearance_mm": num(e.get("ceiling_clearance_mm")),
            "note": e.get("note"), "contains": e.get("contains"),
            "bbox_px": e.get("bbox") if e.get("bbox") and len(e.get("bbox")) == 4 else None,
        })

    access, vent, routes, cutouts, entries = [], [], [], [], []
    for i, o in enumerate(raw.get("openings") or []):
        t = (o.get("type") or "").lower()
        b = o.get("bbox")
        poly = o.get("polygon_mm") or (P.bbox_px_to_mm(b, origin, s) if (b and len(b) == 4 and s) else None)
        meas = o.get("measured_mm") or []
        width = num(o.get("width_mm")) or (max(meas) if meas else None)
        rec = {"id": f"O{i + 1}", "type": t, "label": o.get("label"), "width_mm": width, "bbox_px": b if (b and len(b) == 4) else None,
               "height_mm": num(o.get("height_mm")), "polygon_mm": poly, "wall": o.get("wall") or _wall_of(poly, Lx, By)}
        if poly:
            gg = P.poly(poly)
            rec["centre_mm"] = [round(gg.centroid.x, 1), round(gg.centroid.y, 1)] if gg else None
        if t in ACCESS_TYPES or t == "shutter":
            rec["type"] = "rolling_shutter" if t == "shutter" else t
            access.append(rec)
        elif t in VENT_TYPES:
            rec["type"] = "louvre" if t in ("ventilation", "louver", "louvre", "exhaust") else t
            vent.append(rec)
        elif t == "cutout":
            cutouts.append(rec)
        elif t == "trench":
            routes.append({"id": f"R{i + 1}", "type": "trench", "kind": _trench_kind(o.get("label")), "bbox_px": rec["bbox_px"],
                           "polygon_mm": poly, "label": o.get("label"), "width_mm": width,
                           "depth_mm": num(o.get("depth_mm"))})
        elif t == "beam":
            civ["structures"].append({"id": f"S{i + 1}", "type": "beam", "polygon_mm": poly, "label": o.get("label"), "bbox_px": rec["bbox_px"],
                                      "bottom_mm": num(o.get("bottom_mm")) or civ.get("beam_bottom_mm")})
        elif t == "column":
            civ["structures"].append({"id": f"S{i + 1}", "type": "column", "polygon_mm": poly, "label": o.get("label"), "bbox_px": rec["bbox_px"]})
        elif t == "platform":
            civ.setdefault("platform", {}).update({"label": o.get("label")})
        elif t in ("shaft", "cable_riser"):
            entries.append(rec)
        elif t in ("fence",):
            pass
    for t in raw.get("trenches") or []:
        routes.append(t)
    clearances = [{**c, "source": c.get("source", "dimension")} for c in (raw.get("clearances_dimensioned") or raw.get("dimension_clearances") or [])]
    clearances += [{"a": c.get("a"), "b": c.get("b"), "mm": c.get("gap_mm"), "relation": c.get("relation"), "source": "geometry"}
                   for c in (raw.get("clearances_geometric") or [])]
    return {
        "id": f"S{idx + 1}", "name": raw.get("name") or f"CSS-{idx + 1}", "page": raw.get("page") or 1,
        "floor": raw.get("floor"),
        "equipment_layout_id": raw.get("equipment_layout_id"),
        "section_layout_id": raw.get("section_used") or raw.get("section_layout_id"),
        "section_reason": raw.get("section_reason"),
        "mm_per_px": s,
        "civil": civ,
        "electrical": {"equipment": equipment},
        "access": {"openings": access},
        "ventilation": {"openings": vent},
        "cable": {"routes": routes, "cutouts": cutouts, "entries": entries},
        "clearances": clearances,
        "counts": {},
    }


def _trench_kind(label: str | None) -> str | None:
    t = (label or "").lower()
    if "ht" in t and "lt" in t:
        return "HT/LT"
    if re.search(r"\bht\b", t):
        return "HT"
    if re.search(r"\blt\b", t):
        return "LT"
    return None


# ---------------------------------------------------------------- enrichment
def enrich(doc: dict, rules: dict, gate: dict | None = None) -> dict:
    tol = rules.get("catalogue_tol")
    totals: dict[str, int] = {}
    for sub in doc.get("substations", []):
        counts: dict[str, int] = {}
        for e in sub["electrical"]["equipment"]:
            cls = equipment_class(e.get("equipment_type"), e.get("label"), e.get("subtype"))
            e["ontology_class"] = cls
            if e.get("L_mm") or e.get("H_mm") or e.get("weight_kg"):
                inf = catalogue.infer_rating(cls, e.get("L_mm"), e.get("B_mm"), e.get("H_mm"), e.get("weight_kg"), tol)
                e["catalogue"] = inf
                if not e.get("kva") and base_equipment_type(cls) in ("transformer", "packaged") and inf["kva"]:
                    e["kva"] = inf["kva"]
                    e["kva_source"] = "catalogue_unique"
            if base_equipment_type(cls) == "transformer" and not e.get("kva"):
                e["kva_source"] = "none"
            bt = base_equipment_type(cls)
            counts[bt] = counts.get(bt, 0) + 1
        sub["counts"] = counts
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        for o in sub["access"]["openings"] + sub["ventilation"]["openings"]:
            o["ontology_class"] = opening_class(o.get("type")) or o.get("type")
        civ = sub["civil"]
        if not civ.get("trench_depth_mm"):
            ds = [r.get("depth_mm") for r in sub["cable"]["routes"] if r.get("depth_mm")]
            civ["trench_depth_mm"] = max(ds) if ds else None
        if not sub.get("floor"):
            sub["floor"] = doc["metadata"].get("floor")
    md = doc["metadata"]
    md["equipment_totals"] = totals
    if not md.get("kind"):
        if totals.get("packaged"):
            md["kind"] = "package"
        elif totals.get("transformer"):
            md["kind"] = "dt_css"
        elif totals.get("ht_switchgear"):
            md["kind"] = "ht_switching"
        elif totals.get("rmu") or totals.get("metering"):
            md["kind"] = "metering_only"
    md["substation_count"] = max(int(md.get("substation_count") or 1), len(doc.get("substations", [])) or 1)
    md["multiple_substations"] = bool(md.get("multiple_substations")) or md["substation_count"] > 1
    md["kva_all"] = sorted([e["kva"] for s in doc.get("substations", []) for e in s["electrical"]["equipment"]
                            if e.get("kva") and base_equipment_type(e.get("ontology_class", "")) in ("transformer", "packaged")])

    # historic plot-clearance self check (site mm frame, when the site plan is scaled)
    site = doc.get("site") or {}
    feats = site.get("features") or []
    polys = [P.poly(p) for p in site.get("substation_polygons_mm") or []]
    polys = [p for p in polys if p is not None]
    if polys and feats:
        checks = []
        for p in polys:
            checks += site_clearances(p, feats, rules["plot_clearance_mm"])
        site["clearance_checks"] = [c for c in site.get("clearance_checks") or [] if c.get("source") == "stated"] + checks
    doc["site"] = site
    _segments(doc)
    _quality(doc, gate or {})
    return doc


def _segments(doc: dict) -> None:
    segs = [s for s in doc.get("segments", []) if s.get("source") not in ("derived",)]
    n = len(segs)
    for sub in doc.get("substations", []):
        def px(item):
            b = item.get("bbox_px")
            return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]] if b else None

        def add(cls, poly=None, line=None, label=None, poly_px=None):
            nonlocal n
            if poly or line or poly_px:
                n += 1
                segs.append({"id": f"G{n}", "cls": cls, "substation_id": sub["id"], "layout_id": sub.get("equipment_layout_id"),
                             "page": sub.get("page") or 1, "polygon_mm": poly, "polyline_mm": line, "polygon_px": poly_px,
                             "label": label, "source": "derived"})
        civ = sub["civil"]
        add("room", civ.get("polygon_mm"), label=sub.get("name"), poly_px=civ.get("polygon_px") or px(civ))
        for e in sub["electrical"]["equipment"]:
            add("equipment", e.get("polygon_mm"), label=e.get("label"), poly_px=px(e))
        for o in sub["access"]["openings"]:
            cls = {"rolling_shutter": "rolling_shutter", "fire_exit": "fire_exit", "staircase": "staircase"}.get(o["type"], "door")
            add(cls, o.get("polygon_mm"), label=o.get("label"), poly_px=px(o))
        for o in sub["ventilation"]["openings"]:
            add("ventilation", o.get("polygon_mm"), label=o.get("label"), poly_px=px(o))
        for c in sub["cable"]["cutouts"]:
            add("cutout", c.get("polygon_mm"), label=c.get("label"), poly_px=px(c))
        for r in sub["cable"]["routes"]:
            add("cable_tray" if r.get("type") == "cable_tray" else "trench", r.get("polygon_mm"), r.get("polyline_mm"), r.get("label"), px(r))
        for st in civ.get("structures", []):
            add(st["type"], st.get("polygon_mm"), label=st.get("label"), poly_px=px(st))
    for f in (doc.get("site") or {}).get("features", []):
        n += 1
        segs.append({"id": f"G{n}", "cls": f.get("cls"), "layout_id": f.get("layout_id"), "page": f.get("page") or 1,
                     "polygon_px": f.get("polygon_px"), "polyline_px": f.get("polyline_px"),
                     "polygon_mm": f.get("polygon_mm"), "polyline_mm": f.get("polyline_mm"), "label": f.get("label"), "source": "derived"})
    doc["segments"] = segs


def _quality(doc: dict, gate: dict) -> None:
    subs = doc.get("substations", [])
    cov = {
        "layouts": bool(doc.get("layouts")),
        "substation": bool(subs),
        "room_outline": any(s["civil"].get("polygon_mm") for s in subs),
        "scale": any(s.get("mm_per_px") for s in subs),
        "equipment": any(s["electrical"]["equipment"] for s in subs),
        "equipment_positions": any(e.get("polygon_mm") for s in subs for e in s["electrical"]["equipment"]),
        "height": any(s["civil"].get("clear_height_mm") for s in subs),
        "section": any(s.get("section_layout_id") for s in subs),
        "openings": any(s["access"]["openings"] for s in subs),
        "site": bool((doc.get("site") or {}).get("features")),
        "metadata": bool(doc["metadata"].get("installation")),
    }
    w = {"substation": 2, "room_outline": 2, "scale": 2, "equipment": 2, "equipment_positions": 1.5, "height": 1.5,
         "section": 1, "openings": 1, "site": 1, "metadata": 1, "layouts": 0.5}
    score = sum(w[k] for k, v in cov.items() if v) / sum(w.values())
    issues = list(doc.get("quality", {}).get("issues", []))
    for s in subs:
        h = s["civil"].get("clear_height_mm")
        if h and h < 2000:
            issues.append(f"{s['name']}: clear height {h} mm looks wrong")
        if not s["electrical"]["equipment"]:
            issues.append(f"{s['name']}: no equipment found")
    q = doc.setdefault("quality", {})
    q["coverage"] = cov
    q["score"] = round(score, 3)
    q["issues"] = issues
    if q.get("status") != "reviewed":
        mode = gate.get("review_gate", "auto")
        q["status"] = "auto" if (mode == "auto" and score >= gate.get("min_quality_auto", 0.65)) else "provisional"


def metadata_from_classification(c: dict, text_blob: str = "") -> dict:
    loc = c.get("location_in_building") or c.get("installation_detail") or ""
    inst = c.get("installation")
    floor = derive_floor(loc) or derive_floor(c.get("evidence"))
    if not floor and inst == "open_to_sky":
        floor = "ground"
    return {
        "installation": inst,
        "arrangement": c.get("css_arrangement") or c.get("arrangement"),
        "context": derive_context(f"{loc} {c.get('css_type_detail') or ''} {c.get('evidence') or ''}", inst),
        "floor": floor,
        "location_in_building": loc or None,
        "voltage_kv": num(c.get("voltage_kv")) or (33.0 if re.search(r"\b33\s*kv", text_blob.lower()) else 11.0),
        "substation_count": int(c.get("substation_count") or 1),
        "multiple_substations": bool(c.get("multiple_substations")),
        "project": c.get("project"),
    }
