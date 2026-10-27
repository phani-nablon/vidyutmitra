"""Segmentation refine: LLM boxes -> mask polygons.

Seeds are the class boxes from pass 2. Each seed is refined with
  1. legend colour: pixels matching the legend swatch colour of that class (HSV), connected components
     overlapping the seed become the mask;
  2. PDF vector paths: closed, filled paths of that colour inside the seed are taken verbatim (exact);
  3. otherwise the seed box itself (confidence 0.5).
Classes without seeds but with a legend colour (trench, wayleave, site boundary, ...) are taken from the
colour mask directly when the components are large enough. Returns polygons in page px + debug masks.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

HSV = {
    "red": [((0, 90, 90), (10, 255, 255)), ((170, 90, 90), (179, 255, 255))],
    "magenta": [((140, 80, 80), (169, 255, 255))], "pink": [((140, 40, 120), (175, 255, 255))],
    "yellow": [((20, 90, 120), (35, 255, 255))], "green": [((40, 80, 60), (85, 255, 255))],
    "cyan": [((85, 80, 80), (100, 255, 255))], "blue": [((100, 80, 60), (130, 255, 255))],
    "orange": [((10, 100, 120), (20, 255, 255))], "brown": [((5, 80, 40), (20, 255, 160))],
}
SEMANTIC_TO_CLASS = {"site_boundary": "site_boundary", "substation_boundary": "site_boundary", "access_path": "access_path",
                     "wayleave": "wayleave", "trench": "trench", "cable_tray": "cable_tray", "cutout": "cutout", "beam": "beam",
                     "column": "column", "ms_plate": "platform", "nalla": "nalla", "road": "dp_road", "tree": "tree"}
NAMED_RGB = {"red": (230, 30, 30), "magenta": (220, 0, 220), "yellow": (240, 220, 0), "green": (0, 170, 0), "blue": (0, 60, 230),
             "cyan": (0, 200, 220), "orange": (250, 140, 0), "brown": (150, 80, 20), "pink": (240, 120, 200)}


def colour_mask(rgb: np.ndarray, colour: str) -> np.ndarray | None:
    if cv2 is None or colour not in HSV:
        return None
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    m = np.zeros(rgb.shape[:2], np.uint8)
    for lo, hi in HSV[colour]:
        m |= cv2.inRange(hsv, np.array(lo), np.array(hi))
    return m


def _polys(mask: np.ndarray, min_area: float) -> list[np.ndarray]:
    k = np.ones((3, 3), np.uint8)
    m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        if cv2.contourArea(c) < min_area:
            continue
        out.append(cv2.approxPolyDP(c, 0.004 * cv2.arcLength(c, True), True)[:, 0, :].astype(float))
    return out


def _overlaps(poly: np.ndarray, box, pad: float) -> bool:
    x0, y0 = poly.min(0)
    x1, y1 = poly.max(0)
    return not (x1 < box[0] - pad or x0 > box[2] + pad or y1 < box[1] - pad or y0 > box[3] + pad)


def _colour_close(rgb, target, tol=60) -> bool:
    return rgb is not None and sum(abs(a - b) for a, b in zip(rgb, target)) <= tol * 3


def refine(crop: np.ndarray, crop_origin: tuple[float, float], seeds: dict[str, list[list[float]]],
           legend: list[dict], paths: list[dict] | None = None, min_area_frac: float = 0.0002) -> tuple[list[dict], dict]:
    """seeds: cls -> boxes in page px. Returns (segments [{cls, polygon_px, source, confidence}], masks {cls: uint8})."""
    if cv2 is None:
        return [{"cls": c, "polygon_px": _box(b), "source": "llm", "confidence": 0.5} for c, bs in seeds.items() for b in bs], {}
    H, W = crop.shape[:2]
    ox, oy = crop_origin
    min_area = min_area_frac * H * W
    colour_of = {}
    for g in legend or []:
        cls = SEMANTIC_TO_CLASS.get((g.get("semantic") or "").lower())
        col = (g.get("color_name") or g.get("color") or "").lower()
        if cls and col in HSV and cls not in colour_of:
            colour_of[cls] = col
    segs, masks = [], {}
    for cls in set(seeds) | set(colour_of):
        boxes = seeds.get(cls, [])
        col = colour_of.get(cls)
        used = [False] * len(boxes)
        if col:
            m = colour_mask(crop, col)
            if m is not None and m.any():
                masks[cls] = m
                for p in _polys(m, min_area):
                    pg = p + [ox, oy]
                    hit = [i for i, b in enumerate(boxes) if _overlaps(pg, b, 0.01 * max(H, W))]
                    if boxes and not hit:
                        continue
                    for i in hit:
                        used[i] = True
                    segs.append({"cls": cls, "polygon_px": pg.round(1).tolist(), "source": "legend_colour", "confidence": 0.8})
        if paths and col:
            target = NAMED_RGB.get(col)
            for pth in paths:
                if not pth.get("closed") or not _colour_close(pth.get("fill"), target):
                    continue
                pts = np.array(pth["pts"])
                if len(pts) < 3:
                    continue
                if not (ox <= pts[:, 0].min() and pts[:, 0].max() <= ox + W and oy <= pts[:, 1].min() and pts[:, 1].max() <= oy + H):
                    continue
                hit = [i for i, b in enumerate(boxes) if _overlaps(pts, b, 2)]
                if boxes and not hit:
                    continue
                for i in hit:
                    used[i] = True
                segs.append({"cls": cls, "polygon_px": pts.round(1).tolist(), "source": "vector", "confidence": 0.95})
        for i, b in enumerate(boxes):
            if not used[i]:
                segs.append({"cls": cls, "polygon_px": _box(b), "source": "llm", "confidence": 0.5})
    return segs, masks


def _box(b) -> list[list[float]]:
    x0, y0, x1, y1 = b
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
