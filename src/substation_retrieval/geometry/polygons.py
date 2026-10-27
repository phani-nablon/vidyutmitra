"""Polygon helpers in the millimetre frame (shapely 2)."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

Pt = Sequence[float]


def poly(points: Iterable[Pt] | None) -> Polygon | None:
    if not points:
        return None
    pts = [tuple(map(float, p[:2])) for p in points]
    if len(pts) < 3:
        return None
    try:
        p = Polygon(pts)
        if not p.is_valid:
            p = p.buffer(0)
        if isinstance(p, MultiPolygon):
            p = max(p.geoms, key=lambda g: g.area)
        return p if (p and not p.is_empty and p.area > 0) else None
    except Exception:
        return None


def line(points: Iterable[Pt] | None) -> LineString | None:
    if not points:
        return None
    pts = [tuple(map(float, p[:2])) for p in points]
    return LineString(pts) if len(pts) >= 2 else None


def rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def rect_centre(cx: float, cy: float, L: float, B: float) -> list[list[float]]:
    return rect(cx - L / 2, cy - B / 2, cx + L / 2, cy + B / 2)


def coords(p) -> list[list[float]]:
    if p is None or p.is_empty:
        return []
    if isinstance(p, Polygon):
        return [[round(x, 1), round(y, 1)] for x, y in list(p.exterior.coords)[:-1]]
    if isinstance(p, LineString):
        return [[round(x, 1), round(y, 1)] for x, y in p.coords]
    if isinstance(p, MultiPolygon):
        return coords(max(p.geoms, key=lambda g: g.area))
    return []


def bbox_px_to_mm(b: Sequence[float], origin_px: Sequence[float], mm_per_px: float) -> list[list[float]]:
    x0, y0, x1, y1 = b[:4]
    ox, oy = origin_px[:2]
    s = mm_per_px
    return rect((x0 - ox) * s, (y0 - oy) * s, (x1 - ox) * s, (y1 - oy) * s)


def px_to_mm(points: Iterable[Pt], origin_px: Sequence[float], mm_per_px: float) -> list[list[float]]:
    ox, oy = origin_px[:2]
    return [[round((p[0] - ox) * mm_per_px, 1), round((p[1] - oy) * mm_per_px, 1)] for p in points]


def shape_class(p: Polygon | None) -> str | None:
    if p is None:
        return None
    mrr = p.minimum_rotated_rectangle
    rectangularity = p.area / mrr.area if mrr.area else 0
    n = len(p.exterior.coords) - 1
    if rectangularity > 0.93:
        return "rectangular"
    if n in (6,) and rectangularity > 0.6:
        return "L_shape"
    if n in (5, 7, 8) and rectangularity > 0.8:
        return "chamfered"
    return "irregular"


def dims(p: Polygon | None) -> tuple[float, float] | None:
    """(long, short) sides of the minimum rotated rectangle."""
    if p is None:
        return None
    mrr = p.minimum_rotated_rectangle
    xs, ys = mrr.exterior.coords.xy
    e = [math.dist((xs[i], ys[i]), (xs[i + 1], ys[i + 1])) for i in range(2)]
    return max(e), min(e)


def union(polys: Iterable[Polygon | None]):
    ps = [p for p in polys if p is not None]
    return unary_union(ps) if ps else None


# ---------------------------------------------------------------- poses
POSES = [(k, m) for m in (False, True) for k in (0, 90, 180, 270)]


def apply_pose(geom, angle: float, mirror: bool, origin, dx: float = 0.0, dy: float = 0.0):
    g = geom
    if mirror:
        g = affinity.scale(g, xfact=-1, yfact=1, origin=origin)
    if angle:
        g = affinity.rotate(g, angle, origin=origin)
    if dx or dy:
        g = affinity.translate(g, dx, dy)
    return g


def iou(a, b) -> float:
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0
    u = a.union(b).area
    return a.intersection(b).area / u if u else 0.0


def radial_profile(p: Polygon, n: int = 36) -> np.ndarray:
    """Distance from centroid to boundary in n directions, normalised by sqrt(area)."""
    c = p.centroid
    R = math.sqrt(p.area) * 4
    out = np.zeros(n)
    for i in range(n):
        a = 2 * math.pi * i / n
        ray = LineString([(c.x, c.y), (c.x + R * math.cos(a), c.y + R * math.sin(a))])
        inter = ray.intersection(p.exterior)
        if inter.is_empty:
            continue
        pts = [inter] if isinstance(inter, Point) else list(getattr(inter, "geoms", [inter]))
        out[i] = max(Point(c.x, c.y).distance(q) for q in pts)
    return out / math.sqrt(p.area)


def shape_similarity(a: Polygon | None, b: Polygon | None) -> float:
    """Rotation/mirror-invariant shape similarity from radial profiles (1 = same shape)."""
    if a is None or b is None:
        return 0.0
    ra, rb = radial_profile(a), radial_profile(b)
    best = 1e9
    for rb2 in (rb, rb[::-1]):
        for s in range(len(rb2)):
            best = min(best, float(np.mean(np.abs(ra - np.roll(rb2, s)))))
    return float(math.exp(-best / 0.08))


def box_of(p) -> list[float]:
    x0, y0, x1, y1 = p.bounds
    return [x0, y0, x1, y1]


__all__ = ["poly", "line", "rect", "rect_centre", "coords", "bbox_px_to_mm", "px_to_mm", "shape_class", "dims",
           "union", "POSES", "apply_pose", "iou", "radial_profile", "shape_similarity", "box", "box_of", "Point"]
