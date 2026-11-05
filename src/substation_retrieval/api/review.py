"""Review payload: every extracted item with its value types and where on the original drawing it was read.

Locations are in the pixel frame of the stored page image (`pages[].w/h`), so the UI can draw them as an SVG
overlay on the page. `precision` says how trustworthy the location is:

  exact     the item's own box or outline from the extraction
  segment   the single mask of that class in the substation (the item had no box of its own)
  layout    only the layout it was read from is known (the whole plan, section or beam layout)
  none      no location was recorded
"""
from __future__ import annotations

import json
from typing import Any

from .clearances import clearance_table
from ..core.glossary import FIELDS, TYPES, vtype
from ..core.ontology import index as onto_index

SCALAR_TYPES = {"string", "integer", "number", "boolean", "enum"}
KVA_NOTE = {"drawing_text": "printed on the drawing", "catalogue_unique": "inferred: only one catalogue rating fits the footprint"}

ROOM_KEYS = ["name", "floor", "length_mm", "width_mm", "area_m2", "shape", "clear_height_mm", "height_source", "slab_to_soffit_mm",
             "beam_bottom_mm", "plinth_mm", "trench_depth_mm", "platform", "mm_per_px", "polygon_mm", "polygon_px", "bbox_px"]
EQUIP_KEYS = ["label", "equipment_type", "subtype", "kva", "kva_source", "L_mm", "B_mm", "H_mm", "weight_kg", "centre_mm",
              "ceiling_clearance_mm", "ontology_class", "note", "catalogue", "polygon_mm", "bbox_px"]
OPEN_KEYS = ["type", "label", "width_mm", "height_mm", "wall", "centre_mm", "ontology_class", "polygon_mm", "bbox_px"]
STRUCT_KEYS = ["type", "label", "bottom_mm", "depth_mm", "polygon_mm", "bbox_px"]
ROUTE_KEYS = ["type", "kind", "label", "width_mm", "depth_mm", "polygon_mm", "polyline_mm", "bbox_px"]
FEATURE_KEYS = ["cls", "label", "width_m", "polygon_px", "polygon_mm", "polyline_mm"]

SEG_FOR_STRUCT = {"beam": "beam", "column": "column", "platform": "platform"}
LAYOUT_FOR = {"cutout": ("cutout",), "beam": ("beam",), "column": ("beam",), "route": ("equipment", "plan"), "structure": ("equipment", "plan")}


def _bounds(poly) -> list[float] | None:
    pts = [p for p in (poly or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _loc(page, box=None, poly=None, precision="exact", layout_id=None, why=None) -> dict:
    if box is None and poly:
        box = _bounds(poly)
    return {"page": int(page or 1), "box": [round(float(v), 1) for v in box] if box else None,
            "polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in poly] if poly else None,
            "precision": precision if box else "none", "layout_id": layout_id, "why": why}


def _summary(t: str, v: Any) -> str:
    if t in ("polygon", "polyline"):
        return f"{len(v)} points"
    if t in ("point", "bbox"):
        return "[" + ", ".join(str(round(x, 1)) if isinstance(x, float) else str(x) for x in v) + "]"
    if t in ("object", "list"):
        s = json.dumps(v, ensure_ascii=False)
        return s if len(s) <= 140 else s[:137] + "..."
    if isinstance(v, float):
        return str(round(v, 2))
    return str(v)


def values(d: dict, keys: list[str]) -> list[dict]:
    out = []
    for k in keys:
        v = d.get(k)
        if v in (None, "", [], {}):
            continue
        t, u = vtype(k, v)
        e = {"name": k, "type": t, "unit": u, "summary": _summary(t, v), "help": FIELDS.get(k, ("", "", ""))[2]}
        if t in SCALAR_TYPES:
            e["value"] = v
        if k == "kva" and d.get("kva_source") in KVA_NOTE:
            e["note"] = KVA_NOTE[d["kva_source"]]
        out.append(e)
    return out


def _mask_value(sg: dict) -> dict:
    n = len(sg.get("polygon_px") or sg.get("polyline_px") or [])
    conf = f", confidence {sg['confidence']}" if sg.get("confidence") is not None else ""
    return {"name": "mask", "type": "mask", "unit": "px", "summary": f"class {sg.get('cls')}, {n} points, source {sg.get('source')}{conf}",
            "help": TYPES["mask"], "segment_id": sg.get("id")}


def _class_label(cid: str | None) -> str | None:
    node = onto_index().get(cid or "")
    return node[0].label if node else None


def _page_sizes(settings, doc: dict) -> list[dict]:
    from .images import _read, page_image_path
    pages = sorted({int(p.get("page", 1)) for p in (doc.get("source") or {}).get("page_images") or []}
                   | {int(L.get("page") or 1) for L in doc.get("layouts") or []} | {1})
    out = []
    for pg in pages:
        path, size = page_image_path(settings, doc, pg)
        if path is not None and not size:
            im = _read(path)
            size = [im.shape[1], im.shape[0]] if im is not None else None
        out.append({"page": pg, "w": size[0] if size else None, "h": size[1] if size else None, "has_image": path is not None})
    return out


def _layout(doc: dict, page: int, words: tuple[str, ...]) -> dict | None:
    for L in doc.get("layouts") or []:
        if int(L.get("page") or 1) == int(page) and L.get("bbox_px") and any(w in (L.get("type") or "") for w in words):
            return L
    return None


def _item(id_, kind, label, cls, vals, loc, **extra) -> dict:
    return {"id": id_, "kind": kind, "label": label, "class": cls, "class_label": _class_label(cls), "values": vals, "loc": loc, **extra}


def build_review(settings, doc: dict, rules: list | None = None) -> dict:
    segs = [dict(s, id=s.get("id") or f"M{i + 1}") for i, s in enumerate(doc.get("segments") or [])]
    by_sub: dict[str, list[dict]] = {}
    for sg in segs:
        by_sub.setdefault(sg.get("substation_id") or "", []).append(sg)
    groups: list[dict] = []
    clearances: list[dict] = []
    linked: dict[str, str] = {}                       # segment id -> item id
    multi = len(doc.get("substations") or []) > 1

    def locate(sub, page, d, cls_key, kind_for_layout):
        """exact box/outline -> the single mask of that class -> the layout it came from -> none."""
        if d.get("polygon_px"):
            return _loc(page, poly=d["polygon_px"], layout_id=sub.get("equipment_layout_id")), None
        b = d.get("bbox_px")
        if isinstance(b, (list, tuple)) and len(b) == 4:
            return _loc(page, box=b, layout_id=sub.get("equipment_layout_id")), None
        same = [s for s in by_sub.get(sub["id"], []) if s.get("cls") == cls_key and s.get("polygon_px")]
        if len(same) == 1:
            return _loc(page, poly=same[0]["polygon_px"], precision="segment", layout_id=same[0].get("layout_id"),
                        why="No box of its own: the only mask of this class in the substation."), same[0]
        L = _layout(doc, page, LAYOUT_FOR.get(kind_for_layout, ("equipment",)))
        if L:
            return _loc(page, box=L["bbox_px"], precision="layout", layout_id=L["id"],
                        why="Exact position not recorded: showing the layout it was read from."), None
        return _loc(page), None

    for sub in doc.get("substations") or []:
        sid, page = sub["id"], int(sub.get("page") or 1)
        pre = f"{sub.get('name') or sid}: " if multi else ""
        civ = sub.get("civil") or {}
        room = {**civ, "name": sub.get("name"), "floor": sub.get("floor"), "mm_per_px": sub.get("mm_per_px")}
        rloc = _loc(page, poly=civ.get("polygon_px"), box=civ.get("bbox_px"), layout_id=sub.get("equipment_layout_id")) \
            if (civ.get("polygon_px") or civ.get("bbox_px")) else _loc(page)
        room_seg = next((s for s in by_sub.get(sid, []) if s.get("cls") == "room"), None)
        rv = values(room, ROOM_KEYS)
        if room_seg:
            rv.append(_mask_value(room_seg))
            linked[room_seg["id"]] = f"{sid}:room"
        groups.append({"id": f"{sid}:room", "label": f"{pre}Room", "items": [
            _item(f"{sid}:room", "room", sub.get("name") or "Substation room", "substation", rv, rloc)]})

        eq_items, eq_loc = [], {}
        for e in (sub.get("electrical") or {}).get("equipment") or []:
            loc, seg = locate(sub, page, e, "equipment", "equipment")
            if seg is None:
                seg = next((s for s in by_sub.get(sid, []) if s.get("cls") == "equipment" and s.get("label") in (e.get("label"), e.get("id"))), None)
            vals = values(e, EQUIP_KEYS)
            if seg:
                vals.append(_mask_value(seg))
                linked[seg["id"]] = f"{sid}:{e['id']}"
            it = _item(f"{sid}:{e['id']}", "equipment", e.get("label") or e["id"], e.get("ontology_class"), vals, loc)
            eq_items.append(it)
            for k in (e.get("id"), e.get("label")):
                if k:
                    eq_loc[k] = loc
        if eq_items:
            groups.append({"id": f"{sid}:eq", "label": f"{pre}Equipment ({len(eq_items)})", "items": eq_items})
            for e in (sub.get("electrical") or {}).get("equipment") or []:
                if e.get("ceiling_clearance_mm") is not None and eq_loc.get(e.get("id")):
                    clearances.append({"id": f"{sid}:{e['id']}:ceil", "kind": "ceiling_clearance", "status": "info",
                                       "label": f"{pre}{e.get('label') or e['id']} to ceiling or beam",
                                       "values": [{"name": "ceiling_clearance_mm", "type": "number", "unit": "mm",
                                                   "summary": str(round(e["ceiling_clearance_mm"])), "value": e["ceiling_clearance_mm"],
                                                   "help": FIELDS["ceiling_clearance_mm"][2]}],
                                       "shapes": [dict(eq_loc[e["id"]], role="a")]})

        op_items = []
        for src, cls_key in (((sub.get("access") or {}).get("openings"), None), ((sub.get("ventilation") or {}).get("openings"), None)):
            for o in src or []:
                ckey = o.get("ontology_class") or o.get("type")
                loc, seg = locate(sub, page, o, ckey, "opening")
                vals = values(o, OPEN_KEYS)
                if seg:
                    vals.append(_mask_value(seg))
                    linked[seg["id"]] = f"{sid}:{o['id']}"
                op_items.append(_item(f"{sid}:{o['id']}", "opening", o.get("label") or o["id"], o.get("ontology_class"), vals, loc))
        if op_items:
            groups.append({"id": f"{sid}:op", "label": f"{pre}Doors and louvres ({len(op_items)})", "items": op_items})

        cut_items = []
        for o in (sub.get("cable") or {}).get("cutouts") or []:
            loc, seg = locate(sub, page, o, "cutout", "cutout")
            cut_items.append(_item(f"{sid}:{o['id']}", "cutout", o.get("label") or o["id"], "cutout", values(o, OPEN_KEYS), loc))
        if cut_items:
            groups.append({"id": f"{sid}:cut", "label": f"{pre}Cutouts ({len(cut_items)})", "items": cut_items})

        st_items = []
        for o in civ.get("structures") or []:
            t = o.get("type") or "structure"
            loc, seg = locate(sub, page, o, SEG_FOR_STRUCT.get(t, t), t if t in LAYOUT_FOR else "structure")
            st_items.append(_item(f"{sid}:{o['id']}", "structure", o.get("label") or o["id"], t if t in ("beam", "column", "platform") else None,
                                  values(o, STRUCT_KEYS), loc))
        if st_items:
            groups.append({"id": f"{sid}:st", "label": f"{pre}Structure ({len(st_items)})", "items": st_items})

        rt_items = []
        for o in (sub.get("cable") or {}).get("routes") or []:
            t = o.get("type") or "trench"
            loc, seg = locate(sub, page, o, t, "route")
            rt_items.append(_item(f"{sid}:{o['id']}", "route", o.get("label") or o["id"], t if t in ("trench", "cable_tray", "wayleave", "hdpe") else None,
                                  values(o, ROUTE_KEYS), loc))
        if rt_items:
            groups.append({"id": f"{sid}:rt", "label": f"{pre}Cable routes ({len(rt_items)})", "items": rt_items})

        # distances between equipment (and to the walls): drawn dimension + measured gap, against the operating-clearance rule
        walls = rloc
        for c in clearance_table(sub, rules or []):
            ea, eb = eq_loc.get(c.get("a_id") or c["a"]), eq_loc.get(c.get("b_id") or c["b"])
            if str(c["b"]).startswith("wall") or str(c["a"]).startswith("wall"):
                ea, eb = (ea or eb), walls
            shapes = [dict(x, role=r) for x, r in ((ea, "a"), (eb, "b")) if x and x.get("box")]

            def num(name, v, help_):
                return {"name": name, "type": "number", "unit": "mm", "summary": str(round(v)), "value": v, "help": help_}
            vals = [num(n, c[k], h) for n, k, h in (("drawn_mm", "drawn_mm", "Dimension printed on the drawing between the two items."),
                                                    ("measured_mm", "measured_mm", "Gap between the two extracted footprints."),
                                                    ("required_mm", "required_mm", "Larger of the two items' smallest side clearance in the rule table (indicative)."),
                                                    ("margin_mm", "margin_mm", "Available gap minus required.")) if c.get(k) is not None]
            rl = " · ".join(r["title"] for r in (c.get("rule_a"), c.get("rule_b")) if r)
            if rl:
                vals.append({"name": "rule", "type": "string", "unit": None, "summary": rl, "value": rl, "help": "Operating-clearance rule matched to the equipment."})
            clearances.append({"id": f"{sid}:cl:{c['a']}:{c['b']}", "kind": "equipment_gap" if c["kind"] != "wall" else "wall_gap",
                               "status": {"pass": "pass", "fail": "fail"}.get(c["status"], "info"),
                               "label": f"{pre}{c['a']} to {c['b']}", "values": vals, "shapes": shapes})

    # site features and the plot clearance checks against the clearance norms
    site = doc.get("site") or {}
    feats = site.get("features") or []
    spage = lambda f: int(f.get("page") or next((L.get("page") for L in doc.get("layouts") or [] if L.get("id") == f.get("layout_id")), 1) or 1)
    f_items = []
    for f in feats:
        px = f.get("polygon_px") or f.get("polyline_px")
        loc = _loc(spage(f), poly=px, layout_id=f.get("layout_id")) if px else _loc(spage(f))
        seg = next((s for s in segs if s.get("cls") == f.get("cls") and s.get("polygon_px") == px), None) if px else None
        vals = values(f, FEATURE_KEYS)
        if seg:
            vals.append(_mask_value(seg))
            linked[seg["id"]] = f"site:{f['id']}"
        f_items.append(_item(f"site:{f['id']}", "site_feature", f.get("label") or f["id"], f.get("cls"), vals, loc))
    if f_items:
        groups.append({"id": "site", "label": f"Site features ({len(f_items)})", "items": f_items})
    bounds = [i for i in f_items if i["class"] == "site_boundary" and i["loc"]["box"]]
    for n, c in enumerate(site.get("clearance_checks") or []):
        feat = next((i for i in f_items if i["label"] == c.get("feature") and i["loc"]["box"]), None) \
            or next((i for i in f_items if i["class"] == c.get("cls") and i["loc"]["box"]), None)
        shapes = ([dict(feat["loc"], role="feature")] if feat else []) + [dict(b["loc"], role="boundary") for b in bounds[:2]]
        vals = values({"distance_mm": c.get("distance_mm"), "norm_mm": c.get("norm_mm"), "meets_norm": c.get("meets_norm")},
                      ["distance_mm", "norm_mm", "meets_norm"])
        clearances.append({"id": f"site:cl{n}", "kind": "plot_clearance", "status": "pass" if c.get("meets_norm") else "fail",
                           "label": f"{c.get('feature') or c.get('cls')} to substation" + (" (at door)" if c.get("at") == "door" else ""),
                           "source": c.get("source") or "computed", "values": vals, "shapes": shapes})

    for c in clearances:
        boxes = [s["box"] for s in c["shapes"] if s.get("box")]
        c["loc"] = _loc(c["shapes"][0]["page"], box=[min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)],
                        precision="exact") if boxes else _loc(1)

    seg_out = [{"id": s["id"], "cls": s.get("cls") or "other", "page": int(s.get("page") or 1), "label": s.get("label"), "source": s.get("source"),
                "confidence": s.get("confidence"), "legend": s.get("legend"), "layout_id": s.get("layout_id"), "substation_id": s.get("substation_id"),
                "polygon": s.get("polygon_px"), "polyline": s.get("polyline_px"), "item_id": linked.get(s["id"])}
               for s in segs if s.get("polygon_px") or s.get("polyline_px")]
    layouts = [{"id": L["id"], "type": L.get("type"), "title": L.get("title"), "page": int(L.get("page") or 1), "box": L.get("bbox_px")}
               for L in doc.get("layouts") or [] if L.get("bbox_px")]
    return {"doc_id": doc["doc_id"], "file_name": doc["source"]["file_name"], "pages": _page_sizes(settings, doc), "groups": groups,
            "clearances": clearances, "segments": seg_out, "layouts": layouts, "types": TYPES}
