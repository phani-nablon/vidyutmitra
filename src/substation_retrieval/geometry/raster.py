"""Canonical mask raster + shape descriptor (RAG image features, shortlist only).

The room is rasterised at 100 mm/cell into a 128 x 128 grid (12.8 m; larger rooms are scaled down
and the scale is recorded), one channel per class group. Comparison takes the best of the 8 poses so
mirrored / rotated drawings of the same design still match. An optional learned embedding (DINOv2)
of the clean raster can be plugged in through `image_embedder()`; it is skipped when torch is absent.
"""
from __future__ import annotations

import math
from pathlib import Path

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


class ImageEmbedder:
    """DINOv2 embedding of the clean mask raster (RAG shortlist feature).

    Default model: facebook/dinov2-small (ViT-S/14, 22M parameters, CPU is fine). Loaded from
    var/models/<name> when `sr setup-embedder` has saved it there, else from the Hugging Face hub.
    Needs `torch` + `transformers` (requirements-ml.txt). Query rasters are embedded in all 8 poses and the
    best cosine is used, so mirrored / rotated designs still match.
    """

    def __init__(self, model_id: str = "facebook/dinov2-small", model_dir=None):
        import torch
        from transformers import AutoImageProcessor, AutoModel
        src = str(model_dir) if model_dir and Path(model_dir).exists() else model_id
        self.torch = torch
        self.proc = AutoImageProcessor.from_pretrained(src)
        self.model = AutoModel.from_pretrained(src).eval()
        self.name = model_id

    @staticmethod
    def to_image(R: np.ndarray) -> np.ndarray:
        """Channels -> RGB: room grey, equipment blue, openings orange, cutouts/structure red, trench green."""
        img = np.full((GRID, GRID, 3), 255, np.uint8)
        img[R[0] > 0] = (210, 210, 210)
        img[R[6] > 0] = (29, 158, 117)
        img[(R[4] > 0) | (R[5] > 0)] = (225, 29, 93)
        img[(R[2] > 0) | (R[3] > 0)] = (234, 138, 12)
        img[R[1] > 0] = (37, 99, 235)
        return img

    def embed_many(self, rasters: list[np.ndarray]) -> np.ndarray:
        imgs = [self.to_image(r) for r in rasters]
        with self.torch.no_grad():
            out = self.model(**self.proc(images=imgs, return_tensors="pt"))
            v = out.pooler_output if getattr(out, "pooler_output", None) is not None else out.last_hidden_state[:, 0]
            v = v.numpy().astype(np.float32)
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)

    def embed(self, R: np.ndarray) -> np.ndarray:
        return self.embed_many([_centre(R)])[0]

    def embed_poses(self, R: np.ndarray) -> np.ndarray:
        return self.embed_many([_centre(p) for p in _poses(R)])


def load_image_embedder(settings, log=print):
    """Returns an ImageEmbedder when engines.rag.image_embedder == 'dinov2' and torch/transformers are installed."""
    if str(settings.get("engines.rag.image_embedder", "none")).lower() != "dinov2":
        return None
    model_id = settings.get("engines.rag.image_model", "facebook/dinov2-small")
    try:
        return ImageEmbedder(model_id, settings.var("models", model_id.split("/")[-1], mkdir=False))
    except Exception as e:
        log(f"image embedder unavailable ({type(e).__name__}: {e}); install requirements-ml.txt and run `sr setup-embedder`")
        return None
