"""Builder site-plan encoder (query side).

Input : image (png/jpg) or PDF of the developer's plan with the proposed substation area annotated
        (default red outline / fill, as in AEML location-approval drawings).
Output: dict merged into the query
    annotated_area   polygon_px, shape, aspect, rectangularity, length/width/area (when scaled)
    query_fields     site_polygon_mm (site mm frame), length_mm, width_mm, area_m2, shape,
                     site_context.features (wayleave = yellow, nalla = blue, trench = green; site mm frame)
Scale: mm_per_px, or a known dimension of the annotated area (known_length_mm -> long side), or a known area.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

COLOR_RANGES = {  # HSV (OpenCV: H 0-179)
    "red": [((0, 90, 90), (10, 255, 255)), ((170, 90, 90), (179, 255, 255))],
    "magenta": [((140, 80, 80), (169, 255, 255))],
    "yellow": [((20, 90, 120), (35, 255, 255))],
    "green": [((40, 80, 60), (85, 255, 255))],
    "blue": [((95, 80, 60), (130, 255, 255))],
    "orange": [((10, 100, 120), (20, 255, 255))],
}


def render(path: str, max_side: int = 3000) -> np.ndarray | None:
    if path.lower().endswith(".pdf"):
        import pymupdf
        doc = pymupdf.open(path)
        pg = doc[0]
        z = max_side / max(pg.rect.width, pg.rect.height)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    s = max_side / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return img[:, :, ::-1].copy()


def _mask(hsv, color):
    m = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in COLOR_RANGES[color]:
        m |= cv2.inRange(hsv, np.array(lo), np.array(hi))
    return m


def _regions(mask, min_frac, W, H):
    k = max(3, int(min(H, W) / 300))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        x, y, w, h = cv2.boundingRect(c)
        if a < min_frac * H * W or (w > 0.9 * W and h > 0.9 * H):
            continue
        out.append((a, c))
    return sorted(out, key=lambda t: -t[0])


def analyze(path: str, color: str = "red", mm_per_px: float | None = None, known_length_mm: float | None = None,
            known_area_m2: float | None = None, out_dir: str | None = None) -> dict:
    if cv2 is None:
        return {"error": "opencv is required for site-plan analysis"}
    rgb = render(path)
    if rgb is None:
        return {"error": f"cannot read {os.path.basename(path)}"}
    H, W = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    out: dict = {"file": os.path.basename(path), "image_size_px": [W, H], "color": color}
    cands = _regions(_mask(hsv, color if color in COLOR_RANGES else "red"), 0.0005, W, H)
    if not cands:
        out.update({"annotated_area": None, "query_fields": {},
                    "note": f"no {color} annotated area found - mark the substation area in red or pass color=..."})
        return out
    a, c = cands[0]
    poly = cv2.approxPolyDP(c, 0.01 * cv2.arcLength(c, True), True)[:, 0, :].astype(float)
    (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
    long_px, short_px = max(rw, rh), min(rw, rh)
    rect = a / max(rw * rh, 1)
    nv = len(poly)
    shape = "rectangular" if rect > 0.9 and nv <= 6 else ("L_shape" if nv in (6, 7) and rect > 0.6 else
                                                         "chamfered" if rect > 0.75 else "irregular")
    if mm_per_px is None and known_length_mm:
        mm_per_px = float(known_length_mm) / long_px
    if mm_per_px is None and known_area_m2:
        mm_per_px = math.sqrt(float(known_area_m2) * 1e6 / a)
    ann = {"polygon_px": poly.round(1).tolist(), "area_px": round(a), "min_rect_px": [round(long_px, 1), round(short_px, 1)],
           "aspect": round(long_px / max(short_px, 1), 3), "rectangularity": round(rect, 3), "n_vertices": nv, "shape": shape,
           "n_other_regions": len(cands) - 1}
    qf: dict = {"shape": shape}
    feats = []
    if mm_per_px:
        s = float(mm_per_px)
        ann.update({"length_mm": round(long_px * s), "width_mm": round(short_px * s), "area_m2": round(a * s * s / 1e6, 1)})
        qf.update({"length_mm": ann["length_mm"], "width_mm": ann["width_mm"], "area_m2": ann["area_m2"],
                   "site_polygon_mm": [[round(float(x) * s, 1), round(float(y) * s, 1)] for x, y in poly]})
        for col, cls in (("yellow", "wayleave"), ("blue", "nalla"), ("green", "trench")):
            for i, (ar, cc) in enumerate(_regions(_mask(hsv, col), 0.0003, W, H)[:4]):
                pp = cv2.approxPolyDP(cc, 0.01 * cv2.arcLength(cc, True), True)[:, 0, :].astype(float)
                coords = [[round(float(x) * s, 1), round(float(y) * s, 1)] for x, y in pp]
                key = "polygon_mm" if len(coords) >= 3 else "polyline_mm"
                feats.append({"id": f"{cls}{i + 1}", "cls": cls, "label": f"{cls} ({col} mark)", key: coords})
    else:
        qf["site"] = {"shape": {"aspect": ann["aspect"], "rectangularity": ann["rectangularity"]}}
    if feats:
        qf["site_context"] = {"features": feats}
        if any(f["cls"] == "wayleave" for f in feats):
            qf.setdefault("site", {})["access"] = True
        if any(f["cls"] == "trench" for f in feats):
            qf.setdefault("site", {})["trench"] = True
    out.update({"annotated_area": ann, "scale_mm_per_px": mm_per_px, "query_fields": qf, "features": feats})
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        prev = rgb[:, :, ::-1].copy()
        cv2.drawContours(prev, [c], -1, (0, 200, 0), max(2, int(W / 500)))
        cv2.polylines(prev, [poly.astype(np.int32)], True, (255, 0, 0), max(2, int(W / 700)))
        sc = 1400 / max(H, W)
        if sc < 1:
            prev = cv2.resize(prev, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        pp = os.path.join(out_dir, Path(path).stem + "_detected.jpg")
        cv2.imwrite(pp, prev, [cv2.IMWRITE_JPEG_QUALITY, 85])
        out["preview"] = pp
    return out
