"""Drawing images for the UI: original pages, layout crops, and extracted masks drawn over the page.

Page images come from the v1 folder (css_parsed/<stem>/pageN.jpg) for imported drawings or from
var/artifacts/<doc_id>/pageN.jpg for v2 extractions; polygons are in that page's pixel frame (`size_px`),
rescaled to the stored image. Rendered results are cached under var/cache/img/.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

CLASS_COLOURS = {  # BGR for OpenCV
    "room": (60, 160, 40), "site_boundary": (40, 40, 220), "equipment": (235, 99, 37), "door": (12, 138, 234),
    "rolling_shutter": (0, 110, 230), "fire_exit": (60, 60, 200), "ventilation": (14, 165, 183), "louvre": (14, 165, 183),
    "cutout": (93, 29, 225), "beam": (237, 58, 124), "column": (180, 40, 110), "trench": (117, 158, 29), "cable_tray": (117, 180, 120),
    "access_path": (0, 200, 230), "wayleave": (0, 200, 230), "nalla": (200, 120, 20), "dp_road": (120, 120, 120),
    "internal_road": (150, 150, 150), "tree": (40, 140, 40), "compound_wall": (60, 60, 60), "structure": (90, 90, 160),
    "staircase": (160, 100, 60), "platform": (130, 130, 200),
}


def page_image_path(settings, doc: dict, page: int = 1) -> tuple[Path | None, list | None]:
    for pi in (doc.get("source") or {}).get("page_images") or []:
        if int(pi.get("page", 1)) != int(page):
            continue
        if pi.get("base") == "v1":
            p = settings.path("paths.v1_parsed_dir") / pi["dir"] / pi["file"]
        else:
            p = settings.var("artifacts", doc["doc_id"], mkdir=False) / pi["file"]
        if p.exists():
            return p, pi.get("size_px")
    p = settings.var("artifacts", doc["doc_id"], mkdir=False) / f"page{page}.jpg"
    return (p, None) if p.exists() else (None, None)


def _read(p: Path):
    import cv2
    data = np.fromfile(str(p), np.uint8)  # works with non-ASCII Windows paths
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _encode(img, ext=".jpg", q=85) -> bytes:
    import cv2
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, q] if ext == ".jpg" else [])
    return buf.tobytes()


def _fit(img, max_side):
    import cv2
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    return (cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), s) if s < 1 else (img, 1.0)


def page_jpeg(settings, doc: dict, page: int = 1, max_side: int = 2400) -> bytes | None:
    p, _ = page_image_path(settings, doc, page)
    if p is None:
        return None
    img, _ = _fit(_read(p), max_side)
    return _encode(img)


def layout_crop(settings, doc: dict, layout_id: str, max_side: int = 1600) -> bytes | None:
    L = next((x for x in doc.get("layouts", []) if x["id"] == layout_id), None)
    if L is None:
        return None
    if L.get("crop_image"):
        base = (settings.path("paths.v1_parsed_dir") / next((pi["dir"] for pi in doc["source"].get("page_images") or []
                                                              if pi.get("base") == "v1"), "")) if L.get("crop_base") == "v1" \
            else settings.var("artifacts", doc["doc_id"], mkdir=False)
        p = base / L["crop_image"]
        if p.exists():
            img, _ = _fit(_read(p), max_side)
            return _encode(img)
    p, size = page_image_path(settings, doc, L.get("page", 1))
    if p is None or not L.get("bbox_px"):
        return None
    img = _read(p)
    sx = img.shape[1] / size[0] if size else 1.0
    sy = img.shape[0] / size[1] if size else 1.0
    x0, y0, x1, y1 = L["bbox_px"]
    crop = img[max(0, int(y0 * sy)):int(y1 * sy), max(0, int(x0 * sx)):int(x1 * sx)]
    if crop.size == 0:
        return None
    crop, _ = _fit(crop, max_side)
    return _encode(crop)


def mask_overlay(settings, doc: dict, page: int = 1, classes: set[str] | None = None, max_side: int = 2400,
                 layout_id: str | None = None) -> bytes | None:
    """Extracted segments drawn over the original page (filled, semi-transparent, outlined, labelled)."""
    import cv2
    p, size = page_image_path(settings, doc, page)
    if p is None:
        return None
    img = _read(p)
    sx = img.shape[1] / size[0] if size else 1.0
    sy = img.shape[0] / size[1] if size else 1.0
    fill = img.copy()
    t = max(2, img.shape[1] // 900)
    drawn = []
    for sg in doc.get("segments", []):
        if int(sg.get("page") or 1) != int(page):
            continue
        cls = sg.get("cls") or "other"
        if classes and cls not in classes:
            continue
        col = CLASS_COLOURS.get(cls, (128, 128, 128))
        pts = sg.get("polygon_px") or sg.get("polyline_px")
        if not pts or len(pts) < 2:
            continue
        arr = np.array([[x * sx, y * sy] for x, y in pts], np.int32)
        if sg.get("polygon_px") and len(pts) >= 3:
            cv2.fillPoly(fill, [arr], col)
            cv2.polylines(img, [arr], True, col, t)
        else:
            cv2.polylines(img, [arr], False, col, t * 2)
        drawn.append(cls)
    img = cv2.addWeighted(fill, 0.35, img, 0.65, 0)
    if layout_id:
        L = next((x for x in doc.get("layouts", []) if x["id"] == layout_id and x.get("bbox_px")), None)
        if L:
            x0, y0, x1, y1 = L["bbox_px"]
            img = img[max(0, int(y0 * sy)):int(y1 * sy), max(0, int(x0 * sx)):int(x1 * sx)]
    img, _ = _fit(img, max_side)
    return _encode(img)


def mask_classes(doc: dict, page: int | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for sg in doc.get("segments", []):
        if page and int(sg.get("page") or 1) != int(page):
            continue
        if sg.get("polygon_px") or sg.get("polyline_px"):
            out[sg.get("cls") or "other"] = out.get(sg.get("cls") or "other", 0) + 1
    return out


def binary_mask(settings, doc: dict, page: int, cls: str) -> bytes | None:
    """One class as a binary mask PNG in the stored page-image resolution."""
    import cv2
    p, size = page_image_path(settings, doc, page)
    if p is None:
        return None
    img = _read(p)
    sx = img.shape[1] / size[0] if size else 1.0
    sy = img.shape[0] / size[1] if size else 1.0
    m = np.zeros(img.shape[:2], np.uint8)
    for sg in doc.get("segments", []):
        if int(sg.get("page") or 1) == int(page) and sg.get("cls") == cls and sg.get("polygon_px") and len(sg["polygon_px"]) >= 3:
            cv2.fillPoly(m, [np.array([[x * sx, y * sy] for x, y in sg["polygon_px"]], np.int32)], 255)
    return _encode(m, ".png")
