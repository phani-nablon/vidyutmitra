"""Render the first page of every PDF in a folder to a JPG. All JPGs go into one output folder.

    pip install pymupdf
    python render_pdfs.py --src "$DATA/css/pdf" \
                          --out "$DATA/css/images" --dpi 300

Output (flat, one folder):
    <out>/<pdf name without extension>.jpg      e.g. Trustworth Realtors CSS Layout.pdf -> Trustworth Realtors CSS Layout.jpg
    <out>/index.json                            pdf, page, file, width_px, height_px, dpi, skipped

Options
    --pages first   (default) only page 1 of each PDF
    --pages all     every page; pages after the first get a suffix: <name>_p2.jpg, <name>_p3.jpg ...
    --collect DIR   copy every PDF found under --src (use with --recursive) into DIR first, then render from DIR.
                    If two PDFs share a file name, the second is copied as <parent folder>__<name>.pdf so nothing is lost.
    --recursive     also look in sub-folders of --src

Notes
    - Line weight: thin CAD lines (about 0.1 to 0.25 pt) only stay visible at about 300 dpi. That is the default.
      A1 sheets come out near 9900 px on the long side and A0 near 14000 px. Only sheets longer than about 53 inches
      hit --max-side (default 16000 px); those are rendered at a lower dpi and the script reports it.
    - Every JPG's longer side is at least --min-side pixels (default 4000). The dpi is raised per page when needed.
    - Existing JPGs are skipped if they meet --min-side, so use --overwrite to re-render at the new dpi.
    - The source PDFs are only read, never changed.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3
except ImportError:
    import fitz


def pick_dpi(long_in: float, dpi: int, min_side: int, max_side: int) -> int:
    """Lowest dpi that gives a long side of at least min_side pixels, never below dpi, never above max_side."""
    use = max(dpi, math.ceil(min_side / long_in))
    if long_in * use > max_side:                                         # cap for very large sheets
        use = int(max_side / long_in)
    return use


def render_pdf(pdf: Path, out_dir: Path, dpi: int, min_side: int, max_side: int, overwrite: bool, first_only: bool):
    """Render one PDF into out_dir. Returns one index row per rendered page."""
    rows = []
    doc = fitz.open(pdf)
    try:
        for i, page in enumerate(doc, start=1):
            if first_only and i > 1:
                break
            w_in, h_in = page.rect.width / 72, page.rect.height / 72     # PDF units are 1/72 inch
            long_in = max(w_in, h_in)
            use_dpi = pick_dpi(long_in, dpi, min_side, max_side)
            if use_dpi < dpi:
                print(f"  note: {pdf.name} is capped at {use_dpi} dpi (--max-side {max_side}); thin lines may thin out")
            name = pdf.stem if i == 1 else f"{pdf.stem}_p{i}"
            dst = out_dir / f"{name}.jpg"
            if dst.exists() and not overwrite:
                pm = fitz.Pixmap(str(dst))
                if max(pm.width, pm.height) >= min_side:                 # an earlier, smaller render is redone
                    rows.append({"pdf": pdf.name, "page": i, "file": dst.name, "width_px": pm.width,
                                 "height_px": pm.height, "dpi": None, "skipped": True})
                    continue
            pix = page.get_pixmap(matrix=fitz.Matrix(use_dpi / 72, use_dpi / 72), colorspace=fitz.csRGB, alpha=False)
            if max(pix.width, pix.height) < min_side:
                print(f"  warning: {dst.name} is {pix.width}x{pix.height}, below the {min_side} px minimum")
            pix.save(str(dst), jpg_quality=95)
            rows.append({"pdf": pdf.name, "page": i, "file": dst.name, "width_px": pix.width,
                         "height_px": pix.height, "dpi": use_dpi, "skipped": False})
    finally:
        doc.close()
    return rows


def collect_pdfs(src: Path, col: Path) -> list[Path]:
    """Copy every PDF under src into col (flat). Name clashes get the parent folder name as a prefix."""
    col.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(src.rglob("*")):
        if not (p.is_file() and p.suffix.lower() == ".pdf"):
            continue
        d = col / p.name
        if d.exists() and d.resolve() != p.resolve():
            d = col / f"{p.parent.name}__{p.name}"
        if not d.exists():
            shutil.copy2(p, d)
        out.append(d)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="folder with the PDFs")
    ap.add_argument("--out", required=True, help="the one folder all JPGs are written to")
    ap.add_argument("--dpi", type=int, default=300,
                    help="base dpi. 300 keeps 0.25 pt CAD lines at about 1 px; lower values drop thin lines")
    ap.add_argument("--min-side", type=int, default=4000, help="every JPG's longer side is at least this many pixels")
    ap.add_argument("--max-side", type=int, default=16000,
                    help="cap on the longer side in pixels; a page hitting the cap is rendered below --dpi and is reported")
    ap.add_argument("--pages", choices=["first", "all"], default="first")
    ap.add_argument("--collect", default=None, help="copy all PDFs under --src into this folder first")
    ap.add_argument("--recursive", action="store_true", help="also look in sub-folders of --src")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()

    src, out = Path(a.src), Path(a.out)
    if a.collect:
        pdfs = collect_pdfs(src, Path(a.collect))
        print(f"{len(pdfs)} PDFs collected in {a.collect}")
    else:
        it = src.rglob("*") if a.recursive else src.glob("*")
        pdfs = sorted(p for p in it if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        sys.exit(f"no PDFs found in {src}")

    out.mkdir(parents=True, exist_ok=True)
    index = []
    for n, pdf in enumerate(pdfs, start=1):
        try:
            rows = render_pdf(pdf, out, a.dpi, a.min_side, a.max_side, a.overwrite, a.pages == "first")
        except Exception as e:                                           # one bad PDF should not stop the batch
            print(f"[{n}/{len(pdfs)}] FAILED {pdf.name}: {e}")
            continue
        index += rows
        r = rows[0] if rows else {}
        print(f"[{n}/{len(pdfs)}] {r.get('file', pdf.name)}: {r.get('width_px')}x{r.get('height_px')} px @ {r.get('dpi')} dpi")
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"done: {len(index)} JPGs in {out}")


if __name__ == "__main__":
    main()
