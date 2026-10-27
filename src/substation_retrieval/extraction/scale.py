"""Scale calibration per layout.

Primary: dimension lines read in pass 2 (value_mm and both end points). Each gives mm/px; the scale is
accepted when at least `min_fits` dimensions agree within `tol` (3 %) of their median. End points are
snapped to the nearest vector-line end point first, which removes most LLM pointing error.
Fallback: printed scale text ("1:100") with the render dpi (marked lower confidence; plans are often
not to scale). Each layout gets {mm_per_px, source, confidence, fits}.
"""
from __future__ import annotations

import math
import re

import numpy as np


def snap(pt, ends: np.ndarray | None, radius: float) -> list[float]:
    if ends is None or not len(ends):
        return list(pt)
    d = np.hypot(ends[:, 0] - pt[0], ends[:, 1] - pt[1])
    i = int(np.argmin(d))
    return list(ends[i]) if d[i] <= radius else list(pt)


def calibrate(dims: list[dict], tol: float = 0.03, min_fits: int = 2, ends: np.ndarray | None = None,
              snap_radius: float = 12.0) -> dict | None:
    """dims: [{value_mm, p1, p2}] in page px."""
    cands = []
    for d in dims or []:
        try:
            v = float(d["value_mm"])
            p1 = snap(d["p1"], ends, snap_radius)
            p2 = snap(d["p2"], ends, snap_radius)
        except (KeyError, TypeError, ValueError):
            continue
        L = math.dist(p1, p2)
        if v <= 0 or L < 15:
            continue
        cands.append((v / L, d.get("text")))
    if len(cands) < min_fits:
        return None
    vals = np.array([c[0] for c in cands])
    best = None
    for s in vals:
        inl = vals[np.abs(vals - s) / s <= tol]
        if best is None or len(inl) > len(best):
            best = inl
    if best is None or len(best) < min_fits:
        return None
    s = float(np.median(best))
    return {"mm_per_px": s, "source": "dimension_fit", "fits": int(len(best)), "of": len(cands),
            "confidence": round(min(1.0, len(best) / max(3, len(cands))), 2)}


def from_scale_text(text: str | None, dpi: float) -> dict | None:
    m = re.search(r"1\s*:\s*(\d{2,4})", text or "")
    if not m:
        return None
    n = float(m.group(1))
    return {"mm_per_px": n * 25.4 / dpi, "source": "scale_text", "fits": 0, "confidence": 0.3}


def vector_ends(paths: list[dict], bbox=None) -> np.ndarray | None:
    pts = []
    for p in paths:
        for q in (p["pts"][0], p["pts"][-1]):
            if bbox is None or (bbox[0] <= q[0] <= bbox[2] and bbox[1] <= q[1] <= bbox[3]):
                pts.append(q)
    return np.array(pts) if pts else None
