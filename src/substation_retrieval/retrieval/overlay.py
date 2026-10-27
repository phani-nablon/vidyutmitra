"""Overlay payload for the UI: the query outline plus the historic substation posed onto it."""
from __future__ import annotations

from ..geometry import polygons as P
from ..geometry.als import Pose, align, room_geom_from_substation
from .query import room_geom


def overlay(q: dict, doc: dict, sid: str, refine_deg: float = 10.0) -> dict | None:
    sub = next((s for s in doc.get("substations", []) if f'{doc["doc_id"]}:{s["id"]}' == sid or s["id"] == sid), None)
    if sub is None:
        return None
    qg = room_geom(q)
    rg = room_geom_from_substation(sub)
    pose = align(qg, rg, refine_deg) if (qg.outline is not None and rg.outline is not None) else Pose()
    items = []
    for grp, lst in rg.groups.items():
        for it in lst:
            items.append({"group": grp, "cls": it.cls, "label": it.attrs.get("id"), "coords": P.coords(pose.apply(it.geom))})
    qitems = []
    for grp, lst in qg.groups.items():
        for it in lst:
            qitems.append({"group": grp, "cls": it.cls, "coords": P.coords(it.geom)})
    feats = [{"cls": f.get("cls"), "label": f.get("label"), "coords": f.get("polygon_mm") or f.get("polyline_mm"),
              "line": not f.get("polygon_mm")} for f in (q.get("site_context") or {}).get("features") or []]
    return {"query_outline": P.coords(qg.outline) if qg.outline is not None else None,
            "room": P.coords(pose.apply(rg.outline)) if rg.outline is not None else None,
            "items": items, "query_items": qitems, "site_features": feats, "pose": pose.as_dict(),
            "name": sub.get("name"), "clear_height_mm": sub["civil"].get("clear_height_mm"),
            "equipment_layout_id": sub.get("equipment_layout_id"), "section_layout_id": sub.get("section_layout_id"),
            "site_layout_id": (doc.get("site") or {}).get("layout_id"), "page": sub.get("page") or 1}


def substation_geometry(doc: dict) -> list[dict]:
    """Un-posed geometry of every substation in a document (library viewer)."""
    out = []
    for sub in doc.get("substations", []):
        rg = room_geom_from_substation(sub)
        out.append({"id": sub["id"], "name": sub.get("name"), "room": P.coords(rg.outline) if rg.outline is not None else None,
                    "items": [{"group": g, "cls": it.cls, "label": it.attrs.get("id"), "coords": P.coords(it.geom)}
                              for g, lst in rg.groups.items() for it in lst],
                    "civil": {k: sub["civil"].get(k) for k in ("length_mm", "width_mm", "area_m2", "clear_height_mm", "slab_to_soffit_mm",
                                                                 "beam_bottom_mm", "plinth_mm", "shape", "trench_depth_mm")},
                    "equipment": [{k: e.get(k) for k in ("id", "label", "equipment_type", "kva", "kva_source", "L_mm", "B_mm", "H_mm", "weight_kg")}
                                  for e in sub["electrical"]["equipment"]],
                    "openings": [{k: o.get(k) for k in ("type", "label", "width_mm", "wall")} for o in sub["access"]["openings"] + sub["ventilation"]["openings"]],
                    "section": sub.get("section_layout_id"), "section_reason": sub.get("section_reason"), "floor": sub.get("floor")})
    return out
