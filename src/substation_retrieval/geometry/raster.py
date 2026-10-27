"""Canonical mask raster + shape descriptor (RAG image features, shortlist only).

The room is rasterised at 100 mm/cell into a 128 x 128 grid (12.8 m; larger rooms are scaled down
and the scale is recorded), one channel per class group. Comparison takes the best of the 8 poses so
mirrored / rotated drawings of the same design still match. An optional learned embedding (DINOv2)
of the clean raster can be plugged in through `image_embedder()`; it is skipped when torch is absent.
"""
from __future__ import annotations

import math

import numpy as np

from .als import RoomGeom

CHANNELS = ("room", "equipment", "door", "louvre", "cutout", "structure", "trench")
GRID = 128
CELL_MM = 100.0


def _fill(mask: np.ndarray, geom, ox: float, oy: float, s: float) -> None:
    import cv2
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        pts = np.array([[(x - ox) / s, (y - oy) / s] for x, y in geom.coords], np.int32)
        cv2.polylines(mask, [pts], False, 1, thickness=max(1, int(round(400 / s))))
        return
    gs = getattr(geom, "geoms", [geom])
    for g in gs:
        pts = np.array([[(x - ox) / s, (y - oy) / s] for x, y in g.exterior.coords], np.int32)
        cv2.fillPoly(mask, [pts], 1)


def rasterise(rg: RoomGeom) -> tuple[np.ndarray, float] | tuple[None, None]:
    if rg.outline is None:
        return None, None
    x0, y0, x1, y1 = rg.outline.bounds
    span = max(x1 - x0, y1 - y0, 1.0)
    s = max(CELL_MM, span / (GRID - 2))
    ox, oy = x0 - s, y0 - s
    R = np.zeros((len(CHANNELS), GRID, GRID), np.uint8)
    _fill(R[0], rg.outline, ox, oy, s)
    groups = {"equipment": ["equipment"], "door": ["door"], "louvre": ["louvre"], "cutout": ["cutout"],
              "structure": ["beam", "column"], "trench": ["trench", "cable_tray"]}
    for ci, ch in enumerate(CHANNELS[1:], start=1):
        for g in groups[ch]:
            for it in rg.groups.get(g, []):
                _fill(R[ci], it.geom, ox, oy, s)
    return R, s


def _poses(R: np.ndarray):
    for m in (False, True):
        A = R[:, :, ::-1] if m else R
        for k in range(4):
            yield np.rot90(A, k, axes=(1, 2))


def _centre(R: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(R[0])
    if not len(xs):
        return R
    dy, dx = GRID // 2 - int(ys.mean()), GRID // 2 - int(xs.mean())
    return np.roll(np.roll(R, dy, axis=1), dx, axis=2)


def raster_similarity(a: np.ndarray | None, b: np.ndarray | None, channel_w=None) -> float:
    """Best-pose weighted IoU across channels present in the query raster `a`."""
    if a is None or b is None:
        return 0.0
    channel_w = channel_w or {"room": 2.0, "equipment": 2.0, "door": 1.0, "louvre": 0.5, "cutout": 1.0, "structure": 1.0, "trench": 0.5}
    a = _centre(a)
    best = 0.0
    for bp in _poses(b):
        bp = _centre(bp)
        num = den = 0.0
        for ci, ch in enumerate(CHANNELS):
            if not a[ci].any():
                continue
            w = channel_w[ch]
            inter = np.logical_and(a[ci], bp[ci]).sum()
            uni = np.logical_or(a[ci], bp[ci]).sum()
            num += w * (inter / uni if uni else 0.0)
            den += w
        if den:
            best = max(best, num / den)
    return float(best)


def pack(R: np.ndarray) -> bytes:
    return np.packbits(R.astype(bool)).tobytes()


def unpack(b: bytes | None) -> np.ndarray | None:
    if not b:
        return None
    bits = np.unpackbits(np.frombuffer(b, np.uint8))[: len(CHANNELS) * GRID * GRID]
    return bits.reshape(len(CHANNELS), GRID, GRID)


def to_png(R: np.ndarray, path) -> None:
    """Debug image of a raster: one colour per channel."""
    import cv2
    colours = [(220, 220, 220), (235, 99, 37), (12, 138, 234), (183, 165, 14), (93, 29, 225), (237, 58, 124), (29, 158, 117)]
    img = np.full((GRID, GRID, 3), 255, np.uint8)
    for ci in range(len(CHANNELS)):
        img[R[ci] > 0] = colours[ci]
    cv2.imwrite(str(path), cv2.resize(img, (GRID * 4, GRID * 4), interpolation=cv2.INTER_NEAREST))


_EMBEDDER = None


def image_embedder():
    """Optional DINOv2 embedder; returns None when torch/transformers are unavailable."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER or None
    try:  # pragma: no cover - heavy optional dependency
        import torch
        from transformers import AutoImageProcessor, AutoModel
        proc = AutoImageProcessor.from_pretrained("facebook/dinov2-small")
        model = AutoModel.from_pretrained("facebook/dinov2-small").eval()

        def emb(R: np.ndarray) -> np.ndarray:
            img = np.stack([R[0] * 200, R[1] * 255, (R[2] | R[4]) * 255], -1).astype(np.uint8)
            with torch.no_grad():
                out = model(**proc(images=img, return_tensors="pt")).pooler_output[0].numpy()
            return out / (np.linalg.norm(out) + 1e-9)
        _EMBEDDER = emb
    except Exception:
        _EMBEDDER = False
    return _EMBEDDER or None
