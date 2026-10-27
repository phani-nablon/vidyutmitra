"""PDF adapter: page rendering, text words and vector paths (PyMuPDF).

All coordinates returned are page pixels at the requested dpi (origin top-left)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class PageData:
    index: int
    dpi: float
    image: np.ndarray            # RGB
    words: list[tuple]           # (x0, y0, x1, y1, text) px
    paths: list[dict]            # {"pts": [[x,y],...], "closed": bool, "fill": (r,g,b)|None, "stroke": (r,g,b)|None, "width": px}
    size_px: tuple[int, int]
    size_mm: tuple[float, float]


class PdfDocument:
    kind = "pdf"

    def __init__(self, path: str | Path):
        import pymupdf
        self.path = Path(path)
        self.doc = pymupdf.open(str(self.path))

    def __len__(self):
        return len(self.doc)

    def close(self):
        self.doc.close()

    def render(self, i: int, dpi: float) -> np.ndarray:
        import pymupdf
        pg = self.doc[i]
        z = dpi / 72.0
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()

    def page(self, i: int, dpi: float, with_paths: bool = True, max_paths: int = 60000) -> PageData:
        pg = self.doc[i]
        z = dpi / 72.0
        img = self.render(i, dpi)
        words = [(w[0] * z, w[1] * z, w[2] * z, w[3] * z, w[4]) for w in pg.get_text("words")]
        paths = []
        if with_paths:
            try:
                for d in pg.get_drawings()[:max_paths]:
                    pts = []
                    for it in d.get("items", []):
                        op = it[0]
                        if op == "l":
                            pts += [[it[1].x * z, it[1].y * z], [it[2].x * z, it[2].y * z]]
                        elif op == "re":
                            r = it[1]
                            pts += [[r.x0 * z, r.y0 * z], [r.x1 * z, r.y0 * z], [r.x1 * z, r.y1 * z], [r.x0 * z, r.y1 * z]]
                        elif op == "qu":
                            q = it[1]
                            pts += [[p.x * z, p.y * z] for p in (q.ul, q.ur, q.lr, q.ll)]
                        elif op == "c":
                            pts += [[it[1].x * z, it[1].y * z], [it[4].x * z, it[4].y * z]]
                    if len(pts) < 2:
                        continue
                    fill = tuple(int(c * 255) for c in d["fill"]) if d.get("fill") else None
                    stroke = tuple(int(c * 255) for c in d["color"]) if d.get("color") else None
                    paths.append({"pts": pts, "closed": bool(d.get("closePath")) or d.get("type") in ("f", "fs"),
                                  "fill": fill, "stroke": stroke, "width": (d.get("width") or 0) * z})
            except Exception:
                paths = []
        rect = pg.rect
        return PageData(i, dpi, img, words, paths, (img.shape[1], img.shape[0]),
                        (round(rect.width / 72 * 25.4, 1), round(rect.height / 72 * 25.4, 1)))

    def text(self) -> str:
        return " ".join(p.get_text() for p in self.doc)
