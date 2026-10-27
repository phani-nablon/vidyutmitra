"""Aligned Layout Score (ALS).

1. Both layouts are put in one millimetre frame. The historic room is posed onto the query outline
   with 8 discrete poses (4 rotations x mirror), then refined by +-10 deg rotation and ICP translation.
2. Per mask class, query and historic items are paired with the Hungarian algorithm on
       sim = exp(-d/sigma) * exp(-|ln(a_q/a_r)|/0.3) * IoU_delta
   and summarised as a soft Dice  2*sum(sim) / (n_q + n_r).  Line-like masks (trench, cable tray)
   use a chamfer distance instead.
3. ALS = weighted mean of the components the *query* provides (presence rule). A component the query
   provides but the historic design lacks scores 0; one the query does not provide is skipped and
   lowers coverage instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Polygon

from ..core.ontology import base_equipment_type, equipment_class, opening_class, wu_palmer
from . import polygons as P

LINE_CLASSES = ("trench", "cable_tray")
SET_CLASSES = ("equipment", "door", "louvre", "cutout", "column", "beam")


@dataclass
class Item:
    cls: str                       # ontology class (transformer_dry, door, rolling_shutter, beam, ...)
    geom: Any                      # shapely Polygon or LineString (room mm frame)
    attrs: dict = field(default_factory=dict)


@dataclass
class RoomGeom:
    """Geometry of one substation (historic) or of the new site (query), room mm frame."""
    outline: Polygon | None = None
    groups: dict[str, list[Item]] = field(default_factory=dict)
    clear_height_mm: float | None = None
    beam_bottom_mm: float | None = None
    label: str = ""

    def has(self, group: str) -> bool:
        return bool(self.groups.get(group))

    def all_items(self):
        for g, items in self.groups.items():
            for it in items:
                yield g, it


def _group_of_opening(cls: str) -> str | None:
    if cls in ("door", "rolling_shutter", "fire_exit", "gate"):
        return "door"
    if cls in ("louvre", "window", "ventilation"):
        return "louvre"
    if cls == "cutout":
        return "cutout"
    return None


def room_geom_from_substation(sub: dict) -> RoomGeom:
    civ = sub.get("civil") or {}
    rg = RoomGeom(outline=P.poly(civ.get("polygon_mm")), clear_height_mm=civ.get("clear_height_mm"),
                  beam_bottom_mm=civ.get("beam_bottom_mm"), label=sub.get("name") or sub.get("id", ""))
    if rg.outline is None and civ.get("length_mm") and civ.get("width_mm"):
        rg.outline = P.poly(P.rect(0, 0, civ["length_mm"], civ["width_mm"]))
    g = rg.groups
    for e in (sub.get("electrical") or {}).get("equipment", []):
        geom = P.poly(e.get("polygon_mm"))
        if geom is None and e.get("centre_mm") and e.get("L_mm") and e.get("B_mm"):
            geom = P.poly(P.rect_centre(*e["centre_mm"], e["L_mm"], e["B_mm"]))
        if geom is not None:
            cls = equipment_class(e.get("equipment_type"), e.get("label"), e.get("subtype"))
            g.setdefault("equipment", []).append(Item(cls, geom, {"H_mm": e.get("H_mm"), "id": e.get("id"), "kva": e.get("kva")}))
    for key in ("access", "ventilation"):
        for o in (sub.get(key) or {}).get("openings", []):
            cls = opening_class(o.get("type")) or o.get("type")
            grp = _group_of_opening(cls)
            geom = P.poly(o.get("polygon_mm"))
            if grp and geom is not None:
                g.setdefault(grp, []).append(Item(cls, geom, {"width_mm": o.get("width_mm"), "wall": o.get("wall")}))
    cab = sub.get("cable") or {}
    for c in cab.get("cutouts", []):
        geom = P.poly(c.get("polygon_mm"))
        if geom is not None:
            g.setdefault("cutout", []).append(Item("cutout", geom, {}))
    for r in cab.get("routes", []):
        cls = "cable_tray" if r.get("type") == "cable_tray" else "trench"
        geom = P.line(r.get("polyline_mm")) or P.poly(r.get("polygon_mm"))
        if geom is not None:
            g.setdefault(cls, []).append(Item(cls, geom, {"kind": r.get("kind"), "depth_mm": r.get("depth_mm")}))
    for s in civ.get("structures", []):
        if s.get("type") in ("beam", "column"):
            geom = P.poly(s.get("polygon_mm"))
            if geom is not None:
                g.setdefault(s["type"], []).append(Item(s["type"], geom, {"bottom_mm": s.get("bottom_mm")}))
    return rg


# ---------------------------------------------------------------- alignment
@dataclass
class Pose:
    angle: float = 0.0
    mirror: bool = False
    dx: float = 0.0
    dy: float = 0.0
    origin: tuple = (0.0, 0.0)
    iou: float = 0.0
    containment: float = 0.0       # area(historic ∩ query) / area(historic): does the old room fit the site?

    def apply(self, geom):
        return P.apply_pose(geom, self.angle, self.mirror, self.origin, self.dx, self.dy)

    def as_dict(self):
        return {"angle": round(self.angle, 1), "mirror": self.mirror, "dx": round(self.dx), "dy": round(self.dy),
                "iou": round(self.iou, 3), "containment": round(self.containment, 3)}


def _boundary_pts(p: Polygon, n: int = 120) -> np.ndarray:
    ring = p.exterior
    return np.array([ring.interpolate(i / n, normalized=True).coords[0] for i in range(n)])


def align(q: RoomGeom, r: RoomGeom, refine_deg: float = 10.0) -> Pose:
    if q.outline is None or r.outline is None:
        return Pose()
    qo, ro = q.outline, r.outline
    origin = (ro.centroid.x, ro.centroid.y)
    poses = []
    for ang, mir in P.POSES:
        g = P.apply_pose(ro, ang, mir, origin)
        dx, dy = qo.centroid.x - g.centroid.x, qo.centroid.y - g.centroid.y
        g = P.apply_pose(g, 0, False, origin, dx, dy)
        poses.append(Pose(ang, mir, dx, dy, origin, P.iou(qo, g)))
    top = max(p.iou for p in poses)
    ties = [p for p in poses if p.iou >= top - 0.02]
    best = ties[0]
    if len(ties) > 1 and q.groups:
        # symmetric outlines: break the tie with the items both layouts carry (equipment, doors, ...)
        def item_score(p):
            tot = 0.0
            for grp, qs in q.groups.items():
                rs = [Item(i.cls, p.apply(i.geom), i.attrs) for i in r.groups.get(grp, [])]
                if rs and grp not in LINE_CLASSES:
                    tot += set_match(qs, rs)[0]
            return tot
        best = max(ties, key=item_score)
    # refine: small rotations, each followed by ICP translation on boundary points
    qtree = cKDTree(_boundary_pts(qo))
    cand = best
    for dang in np.arange(-refine_deg, refine_deg + 1e-6, 2.5):
        pose = Pose(best.angle + float(dang), best.mirror, best.dx, best.dy, origin)
        for _ in range(6):
            g = pose.apply(ro)
            pts = _boundary_pts(g, 80)
            _, idx = qtree.query(pts)
            shift = (qtree.data[idx] - pts).mean(axis=0) * 0.5
            pose.dx += float(shift[0])
            pose.dy += float(shift[1])
            if abs(shift).max() < 5:
                break
        pose.iou = P.iou(qo, pose.apply(ro))
        if pose.iou > cand.iou:
            cand = pose
    g = cand.apply(ro)
    cand.containment = g.intersection(qo).area / g.area if g.area else 0.0
    return cand


# ---------------------------------------------------------------- set matching
def _pair_sim(a: Item, b: Item, sigma: float, log_tol: float, delta: float) -> float:
    ga, gb = a.geom, b.geom
    d = ga.centroid.distance(gb.centroid)
    sa, sb = max(ga.area, 1.0), max(gb.area, 1.0)
    s = math.exp(-d / sigma) * math.exp(-abs(math.log(sa / sb)) / log_tol)
    s *= max(P.iou(ga.buffer(delta), gb.buffer(delta)), 0.05)
    return s * max(wu_palmer(a.cls, b.cls), 0.0) if a.cls != b.cls else s


def set_match(qs: list[Item], rs: list[Item], sigma=600.0, log_tol=0.3, delta=150.0) -> tuple[float, list[tuple]]:
    """Soft Dice of the Hungarian pairing; returns (score, [(qi, ri, sim)])."""
    if not qs and not rs:
        return 1.0, []
    if not qs or not rs:
        return 0.0, []
    S = np.zeros((len(qs), len(rs)))
    for i, a in enumerate(qs):
        for j, b in enumerate(rs):
            S[i, j] = _pair_sim(a, b, sigma, log_tol, delta)
    ri, ci = linear_sum_assignment(-S)
    pairs = [(int(i), int(j), float(S[i, j])) for i, j in zip(ri, ci) if S[i, j] > 1e-3]
    return 2.0 * sum(p[2] for p in pairs) / (len(qs) + len(rs)), pairs


def _line_pts(geoms, step=100.0) -> np.ndarray:
    pts = []
    for g in geoms:
        ln = g if isinstance(g, LineString) else g.exterior
        n = max(2, int(ln.length / step))
        pts += [ln.interpolate(i / n, normalized=True).coords[0] for i in range(n + 1)]
    return np.array(pts) if pts else np.zeros((0, 2))


def chamfer_score(qs: list[Item], rs: list[Item], sigma=600.0) -> float:
    if not qs:
        return 1.0
    if not rs:
        return 0.0
    a, b = _line_pts([i.geom for i in qs]), _line_pts([i.geom for i in rs])
    if not len(a) or not len(b):
        return 0.0
    da, _ = cKDTree(b).query(a)
    db, _ = cKDTree(a).query(b)
    ch = 0.5 * (da.mean() + db.mean())
    return float(math.exp(-ch / sigma))


# ---------------------------------------------------------------- ALS
@dataclass
class ALSResult:
    score: float | None
    components: dict[str, float]
    coverage: float
    pose: Pose
    pairs: dict[str, list] = field(default_factory=dict)

    def as_dict(self):
        return {"score": None if self.score is None else round(self.score, 4),
                "components": {k: round(v, 3) for k, v in self.components.items()},
                "coverage": round(self.coverage, 3), "pose": self.pose.as_dict()}


GROUP_TO_WEIGHT = {"equipment": "equipment", "column": "structure", "beam": "structure", "cutout": "cutouts",
                   "door": "doors", "louvre": "louvres", "trench": "trench", "cable_tray": "trench"}


def als(q: RoomGeom, r: RoomGeom, weights: dict, sigma=600.0, log_tol=0.3, delta=150.0, refine_deg=10.0,
        extra: dict[str, float] | None = None, pose: Pose | None = None) -> ALSResult:
    """extra: precomputed components (height, site) from the compatibility checks; pose: reuse an alignment."""
    pose = pose or align(q, r, refine_deg)
    comps: dict[str, float] = {}
    pairs: dict[str, list] = {}
    if q.outline is not None and r.outline is not None:
        # IoU rewards same size+shape; containment rewards "old room fits in the new site"
        comps["footprint"] = 0.6 * pose.iou + 0.4 * pose.containment
    elif q.outline is not None:
        comps["footprint"] = 0.0
    posed = {g: [Item(it.cls, pose.apply(it.geom), it.attrs) for it in items] for g, items in r.groups.items()}
    struct_scores = []
    for grp in SET_CLASSES:
        if not q.has(grp):
            continue
        s, pr = set_match(q.groups[grp], posed.get(grp, []), sigma, log_tol, delta)
        pairs[grp] = pr
        if grp in ("column", "beam"):
            struct_scores.append(s)
        else:
            comps[GROUP_TO_WEIGHT[grp]] = s
    if struct_scores:
        comps["structure"] = float(np.mean(struct_scores))
    lines = [g for g in LINE_CLASSES if q.has(g)]
    if lines:
        comps["trench"] = float(np.mean([chamfer_score(q.groups[g], posed.get(g, []), sigma) for g in lines]))
    extra = dict(extra or {})
    # civil features of the new site (columns, beams, slab cutouts) count as matched when the historic
    # equipment avoids them, even if the historic drawing never showed such members
    for comp, key in (("structure", "structure_clear"), ("cutouts", "cutouts_clear")):
        v = extra.pop(key, None)
        if v is not None:
            comps[comp] = max(comps.get(comp, 0.0), v)
    for k, v in extra.items():
        if v is not None:
            comps[k] = v
    total_w = sum(weights.values())
    used_w = sum(weights.get(k, 0) for k in comps)
    score = sum(weights.get(k, 0) * v for k, v in comps.items()) / used_w if used_w else None
    return ALSResult(score, comps, used_w / total_w if total_w else 0.0, pose, pairs)
