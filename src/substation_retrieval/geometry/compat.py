"""Compatibility checks between a query (new site) and a posed historic substation.

Every check returns {id, status: pass|warn|fail|na, detail}. `fail` checks become CONFLICTS_WITH
edges in the result graph and block a "reuse" verdict; `pass` checks become CLEARS edges.
Checks only run when the query supplies what they need (presence rule).
"""
from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon

from ..core import catalogue
from ..core.ontology import base_equipment_type, equipment_class
from .als import Item, Pose, RoomGeom
from . import polygons as P

FEATURE_NORM_KEY = {"nalla": "nalla", "dp_road": "dp_road", "internal_road": "internal_road", "tree": "tree",
                    "compound_wall": "compound_wall", "structure": "structure"}


def _chk(id_, status, detail, **kw) -> dict:
    return {"id": id_, "status": status, "detail": detail, **kw}


def site_clearances(room: Polygon, features: list[dict], norms: dict, doors: list[Polygon] | None = None) -> list[dict]:
    """Distances from a substation polygon to plot features (site mm frame) against the norms."""
    out = []
    for f in features:
        key = FEATURE_NORM_KEY.get(f.get("cls"))
        if not key:
            continue
        g = P.poly(f.get("polygon_mm")) or P.line(f.get("polyline_mm")) or P.line(f.get("polygon_mm"))
        if g is None:
            continue
        norm = float(norms.get(key, 0))
        d = room.distance(g)
        if key == "tree" and doors:
            dd = min(dp.distance(g) for dp in doors)
            nd = float(norms.get("tree_at_door", norm))
            if dd < nd:
                out.append({"feature": f.get("label") or key, "cls": key, "distance_mm": round(dd), "norm_mm": nd,
                            "meets_norm": False, "at": "door"})
                continue
        out.append({"feature": f.get("label") or key, "cls": key, "distance_mm": round(d), "norm_mm": norm,
                    "meets_norm": d >= norm})
    return out


def check_all(query: dict, q: RoomGeom, r: RoomGeom, pose: Pose, sub: dict, doc: dict, rules: dict) -> tuple[list[dict], dict]:
    """-> (checks, extra ALS components {height, site})."""
    checks: list[dict] = []
    extra: dict[str, float | None] = {}
    posed_eq = [Item(i.cls, pose.apply(i.geom), i.attrs) for i in r.groups.get("equipment", [])]

    # 1. does the historic room fit the new site outline?
    if q.outline is not None and r.outline is not None:
        c = pose.containment
        st = "pass" if c >= 0.97 else "warn" if c >= 0.90 else "fail"
        checks.append(_chk("footprint_fit", st, f"{c:.0%} of the historic room lies inside the new site outline"))
        outside = [i.attrs.get("id") or i.cls for i in posed_eq if not q.outline.buffer(50).contains(i.geom)]
        if outside:
            checks.append(_chk("equipment_inside", "fail", "equipment outside the new outline after alignment: " + ", ".join(map(str, outside[:6]))))
        elif posed_eq:
            checks.append(_chk("equipment_inside", "pass", f"all {len(posed_eq)} equipment footprints fit inside the new outline"))

    # 2a. requested equipment counts vs the historic design
    have = sub.get("counts") or {}
    if not have:
        for i in r.groups.get("equipment", []):
            have[base_equipment_type(i.cls)] = have.get(base_equipment_type(i.cls), 0) + 1
    for t, n in (query.get("counts") or {}).items():
        m = int(have.get(t, 0))
        if m < n:
            checks.append(_chk(f"count_{t}", "fail", f"historic design has {m} {t}, {n} requested"))
        elif m > n:
            checks.append(_chk(f"count_{t}", "warn", f"historic design has {m} {t}, {n} requested (extra space / equipment)"))
        else:
            checks.append(_chk(f"count_{t}", "pass", f"{n} {t} as requested"))

    # 2b. requested equipment vs historic slots (catalogue dimensions)
    items = query.get("equipment_items") or []
    for it in items:
        if not (it.get("kva") or it.get("ways") or it.get("subtype")):
            continue  # a generic "1 RMU" implies no specific size; nothing to check
        cls = equipment_class(it.get("type"), None, it.get("dt_kind"))
        row = catalogue.lookup(cls, it.get("kva"), it.get("ways"), it.get("dt_kind"), it.get("subtype"))
        if not row or not row.get("L_mm"):
            continue
        slots = [i for i in r.groups.get("equipment", []) if base_equipment_type(i.cls) == base_equipment_type(cls)]
        if not r.groups.get("equipment"):
            checks.append(_chk(f"slot_{cls}", "na", "historic design has no equipment geometry to check against"))
            continue
        if not slots:
            checks.append(_chk(f"slot_{cls}", "fail", f"historic design has no {base_equipment_type(cls)} slot"))
            continue
        need = sorted((row["L_mm"], row["B_mm"]))
        ratios = []
        for sl in slots:
            d = P.dims(sl.geom)
            ratios.append(min(d[1] / need[0], d[0] / need[1]) if d else 0.0)
        ratios.sort(reverse=True)
        n_req = int(it.get("count") or 1)
        n_ok = sum(1 for r_ in ratios if r_ >= 0.97)
        n_near = sum(1 for r_ in ratios if r_ >= 0.85)
        label = f'{row["name"]} {row.get("type") or ""}'.strip()
        dims_s = f"{row['L_mm']:.0f}x{row['B_mm']:.0f}"
        if n_ok >= n_req:
            checks.append(_chk(f"slot_{cls}", "pass", f"{n_req} x {label} ({dims_s}) fit the historic slots"))
        elif n_near >= n_req:
            checks.append(_chk(f"slot_{cls}", "warn", f"{label} ({dims_s}) is up to 15% larger than the historic {base_equipment_type(cls)} slots"))
        else:
            checks.append(_chk(f"slot_{cls}", "fail", f"only {n_near}/{n_req} historic {base_equipment_type(cls)} slots can take {label} ({dims_s})"))

    # 3. height: clear height vs equipment + tray rule, CSS minimum
    hq = query.get("clear_height_mm") or q.clear_height_mm
    if hq:
        heights = [i.attrs.get("H_mm") for i in r.groups.get("equipment", []) if i.attrs.get("H_mm")]
        for it in items:
            row = catalogue.lookup(equipment_class(it.get("type")), it.get("kva"), it.get("ways"), it.get("dt_kind"), it.get("subtype"))
            if row and row.get("H_mm"):
                heights.append(row["H_mm"])
        need = (max(heights) + rules["tray_below_beam_mm"]) if heights else None
        if need and hq < need:
            checks.append(_chk("height", "fail", f"clear height {hq:.0f} < tallest equipment + tray rule {need:.0f} mm"))
            extra["height"] = 0.0
        elif hq < rules["css_min_height_mm"]:
            checks.append(_chk("height", "warn", f"clear height {hq:.0f} below CSS minimum {rules['css_min_height_mm']} mm"))
            extra["height"] = 0.5
        else:
            checks.append(_chk("height", "pass", f"clear height {hq:.0f} mm meets equipment + tray rule" + (f" ({need:.0f})" if need else "")))
            extra["height"] = 1.0
        if r.clear_height_mm and hq < r.clear_height_mm - 300:
            checks.append(_chk("height_vs_historic", "warn", f"new site {hq:.0f} mm is lower than the historic {r.clear_height_mm:.0f} mm"))
            extra["height"] = min(extra.get("height", 1.0), 0.7)

    # 4. beams / columns / cutouts of the new site vs posed historic equipment
    for b in q.groups.get("beam", []):
        bottom = b.attrs.get("bottom_mm") or query.get("beam_bottom_mm")
        for e in posed_eq:
            if e.geom.intersects(b.geom) and bottom and (e.attrs.get("H_mm") or 0) + rules["tray_below_beam_mm"] > bottom:
                checks.append(_chk("beam", "fail", f"{e.attrs.get('id') or e.cls} (H {e.attrs.get('H_mm')}) under beam with bottom {bottom:.0f} mm"))
    for c in q.groups.get("column", []):
        hit = [e.attrs.get("id") or e.cls for e in posed_eq if e.geom.intersects(c.geom)]
        if hit:
            checks.append(_chk("column", "fail", "column clashes with " + ", ".join(map(str, hit[:4]))))
    q_struct = q.groups.get("column", []) + q.groups.get("beam", [])
    if q_struct:
        clashing = sum(1 for c in q_struct if any(e.geom.intersects(c.geom) for e in posed_eq))
        extra["structure_clear"] = 1.0 - clashing / len(q_struct)
    if q.groups.get("cutout"):
        clashing = sum(1 for c in q.groups["cutout"] if any(e.geom.intersects(c.geom) for e in posed_eq
                                                             if base_equipment_type(e.cls) in ("transformer", "packaged")))
        extra["cutouts_clear"] = 1.0 - clashing / len(q.groups["cutout"])
    if q.has("column") and not any(c["id"] == "column" for c in checks):
        checks.append(_chk("column", "pass", f"no clash with {len(q.groups['column'])} column(s)"))
    for c in q.groups.get("cutout", []):
        for e in posed_eq:
            if e.geom.intersects(c.geom):
                sev = "fail" if base_equipment_type(e.cls) in ("transformer", "packaged") else "warn"
                checks.append(_chk("cutout", sev, f"{e.attrs.get('id') or e.cls} sits on a slab cutout"))

    # 5. doors: transformer route needs >= 2750 mm
    widest_needed = max([catalogue.door_min_width(i.cls) for i in r.groups.get("equipment", [])] or [0])
    qdoors = [d for d in q.groups.get("door", []) if d.attrs.get("width_mm")]
    qdw = query.get("door_width_mm") or (max(d.attrs["width_mm"] for d in qdoors) if qdoors else None)
    if qdw and widest_needed:
        st = "pass" if qdw >= widest_needed else "fail"
        checks.append(_chk("door_width", st, f"widest door {qdw:.0f} mm vs {widest_needed:.0f} mm needed to move the largest equipment"))
    hdoors = [d.attrs.get("width_mm") for d in r.groups.get("door", []) if d.attrs.get("width_mm")]
    if any(base_equipment_type(i.cls) == "transformer" for i in r.groups.get("equipment", [])) and hdoors:
        if max(hdoors) < rules["transformer_door_mm"]:
            checks.append(_chk("historic_door", "warn", f"historic design's widest door {max(hdoors):.0f} mm < {rules['transformer_door_mm']} mm transformer rule"))

    # 6. trench / cable entry
    if (query.get("site") or {}).get("trench") or q.has("trench"):
        has_tr = r.has("trench") or bool((sub.get("cable") or {}).get("routes"))
        checks.append(_chk("trench", "pass" if has_tr else "warn", "historic design has a cable trench / entry" if has_tr else "historic design shows no trench"))

    # 7. plot clearances
    norms = rules["plot_clearance_mm"]
    own = (doc.get("site") or {}).get("clearance_checks") or []
    bad_own = [c for c in own if c.get("meets_norm") is False]
    if bad_own:
        checks.append(_chk("historic_plot_clearance", "warn", "historic design violated its own plot clearance: " +
                            "; ".join(f"{c['feature']} {c['distance_mm']/1000:.1f} m < {c['norm_mm']/1000:.1f} m" for c in bad_own[:3])))
    site_feats = (query.get("site_context") or {}).get("features") or []
    if site_feats and r.outline is not None and q.outline is not None:
        placed = pose.apply(r.outline)
        doors = [pose.apply(d.geom) for d in r.groups.get("door", [])]
        res = site_clearances(placed, site_feats, norms, doors)
        bad = [c for c in res if not c["meets_norm"]]
        for c in bad:
            checks.append(_chk("plot_clearance", "fail", f"placed at the new site, {c['feature']} is {c['distance_mm']/1000:.1f} m away (norm {c['norm_mm']/1000:.1f} m)"))
        if res and not bad:
            checks.append(_chk("plot_clearance", "pass", "placed at the new site, all plot clearances are met"))
        extra["site"] = 1.0 - len(bad) / len(res) if res else None
    return checks, extra


def query_feasibility(query: dict, area_m2: float | None, rules: dict | None = None) -> list[dict]:
    """Requirement-only checks (same for every candidate): equipment + clearances vs area, height."""
    out = []
    items = query.get("equipment_items") or []
    need = 0.0
    for it in items:
        row = catalogue.lookup(equipment_class(it.get("type"), None, it.get("dt_kind")), it.get("kva"), it.get("ways"), it.get("dt_kind"), it.get("subtype"))
        if row and row.get("L_mm"):
            L = row["L_mm"] + (row.get("clr_left_mm") or 0) + (row.get("clr_right_mm") or 0)
            B = row["B_mm"] + (row.get("clr_front_mm") or 0) + (row.get("clr_back_mm") or 0)
            need += L * B / 1e6 * int(it.get("count") or 1)
    norms = (rules or {}).get("plot_clearance_mm") or {}
    for k, mm in (query.get("site_distances") or {}).items():
        if k in norms:
            ok = mm >= norms[k]
            out.append(_chk(f"site_{k}", "pass" if ok else "fail",
                            f"new site is {mm / 1000:.1f} m from the {k.replace('_', ' ')} (norm {norms[k] / 1000:.1f} m)"
                            + ("" if ok else ": the substation must sit at least the norm away; only historic layouts that fit the remaining area can be reused")))
    if area_m2 and need:
        st = "pass" if area_m2 >= need else "fail"
        out.append(_chk("area_feasibility", st, f"site {area_m2:.1f} m2 vs {need:.1f} m2 for equipment + catalogue clearances"))
    return out
