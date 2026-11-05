"""Convert the extract_layouts.py JSON files into YOLO annotations. Fully offline.

Reads only the per-sheet JSON files and the sheet images. No network, no model, no Ultralytics.
The standard library is enough; OpenCV is needed only if you use --max-side.

    python convert_json_to_yolo.py --json "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out\\bboxes\\json" ^
                                   --images "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out" ^
                                   --dataset "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out\\bboxes\\yolo_dataset" ^
                                   --mode coarse --val-frac 0.15 --test-frac 0.15 --overwrite

Writes, under --dataset
    images/{train,val,test}/<sheet>.jpg          copies of the sheet images (or downscaled with --max-side)
    labels/{train,val,test}/<sheet>.txt          YOLO lines: class cx cy w h, all normalised 0..1
    data.yaml                                    for Ultralytics; its `path` is --yaml-root (default: --dataset)
    classes.json                                 class id -> name
    split.json                                   sheet -> train / val / test
    report.txt                                   counts per class and split, every skipped item, and the checks

Classes (two only)
    0  block   every drawing panel (plan, section, elevation, site_plan, detail, legend, notes, table,
               earthing_detail, door_schedule, other) and the sheet's title block
    1  title   the heading box of a block (title_bbox)

Coordinates
    The JSON stores boxes as [x0, y0, x1, y1] on a 0..1000 scale of the original sheet, top-left origin.
    YOLO wants the centre and size as fractions of the image. The conversion is linear, so it is exact.
    --max-side resizes the image but keeps the boxes, because they are fractions of the image.

Split
    By sheet, so the pages of one drawing never appear in two splits. Seeded, so it repeats.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

NAMES = ["block", "title"]
BLOCK, TITLE = 0, 1
SPLITS = ("train", "val", "test")
MIN_SIDE = 0.002          # boxes thinner or shorter than 0.2 % of the sheet are not written (they are noise)


def to_yolo(box1000, cls_id: int) -> str | None:
    """[x0,y0,x1,y1] on 0..1000 -> 'cls cx cy w h' on 0..1, or None if the box is unusable."""
    if not (isinstance(box1000, list) and len(box1000) == 4):
        return None
    try:
        x0, y0, x1, y1 = [min(max(float(v), 0.0), 1000.0) / 1000.0 for v in box1000]
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    w, h = x1 - x0, y1 - y0
    if w < MIN_SIDE or h < MIN_SIDE:
        return None
    return f"{cls_id} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {w:.6f} {h:.6f}"


def load_sheets(json_dir: Path, images_dir: Path, report: list[str]) -> list[dict]:
    sheets = []
    files = sorted(json_dir.glob("*.json"))
    if not files:
        sys.exit(f"no JSON files in {json_dir}")
    for jp in files:
        d = json.loads(jp.read_text(encoding="utf-8"))
        img = images_dir / d.get("file", "")
        if not d.get("file") or not img.exists():
            report.append(f"SKIPPED sheet {jp.name}: image {d.get('file')!r} not found in {images_dir}")
            continue
        lines, skipped = [], []
        for L in d.get("layouts", []):
            label = L.get("name") or L.get("type") or "?"
            if L.get("bbox") is not None:
                line = to_yolo(L["bbox"], BLOCK)
                (lines.append((BLOCK, line)) if line else skipped.append(f"block '{label}' bbox {L['bbox']}"))
            if L.get("title_bbox") is not None:
                line = to_yolo(L["title_bbox"], TITLE)
                (lines.append((TITLE, line)) if line else skipped.append(f"title of '{label}' bbox {L['title_bbox']}"))
        for s in skipped:
            report.append(f"SKIPPED box in {jp.name}: {s} (degenerate or malformed)")
        sheets.append({"id": jp.stem, "img": img, "lines": lines, "json": jp.name})
    return sheets


def split_sheets(ids: list[str], val_frac: float, test_frac: float, seed: int) -> dict[str, str]:
    if len(ids) < 3:
        sys.exit("need at least 3 sheets to make train, val and test")
    order = list(ids)
    random.Random(seed).shuffle(order)
    n_test = max(1, round(len(order) * test_frac))
    n_val = max(1, round(len(order) * val_frac))
    out = {}
    for i in order[:n_test]:
        out[i] = "test"
    for i in order[n_test:n_test + n_val]:
        out[i] = "val"
    for i in order[n_test + n_val:]:
        out[i] = "train"
    return out


def write_images(src: Path, dst: Path, max_side: int) -> None:
    if not max_side:
        shutil.copy2(src, dst)
        return
    import cv2                                    # only needed for resizing
    im = cv2.imread(str(src))
    if im is None:
        raise ValueError(f"cannot read {src}")
    k = max_side / max(im.shape[:2])
    if k < 1:
        im = cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dst), im, [cv2.IMWRITE_JPEG_QUALITY, 92])


def verify(out: Path, names: list[str], report: list[str]) -> bool:
    """Read every label file back and check it. Returns True when all checks pass."""
    ok = True
    n_lines = 0
    for sp in SPLITS:
        for lab in sorted((out / "labels" / sp).glob("*.txt")):
            img = out / "images" / sp / f"{lab.stem}.jpg"
            if not img.exists():
                ok = False
                report.append(f"CHECK FAILED {lab.name}: no image in {sp}")
            for n, line in enumerate(lab.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) != 5:
                    ok = False
                    report.append(f"CHECK FAILED {lab.name} line {n}: expected 5 fields, got {len(parts)}")
                    continue
                c = int(parts[0])
                vals = [float(v) for v in parts[1:]]
                n_lines += 1
                if not (0 <= c < len(names)):
                    ok = False
                    report.append(f"CHECK FAILED {lab.name} line {n}: class id {c} not in 0..{len(names) - 1}")
                if any(v < 0 or v > 1 for v in vals):
                    ok = False
                    report.append(f"CHECK FAILED {lab.name} line {n}: value outside 0..1 {vals}")
                cx, cy, w, h = vals
                if cx - w / 2 < -1e-6 or cx + w / 2 > 1 + 1e-6 or cy - h / 2 < -1e-6 or cy + h / 2 > 1 + 1e-6:
                    ok = False
                    report.append(f"CHECK FAILED {lab.name} line {n}: box runs outside the image")
    report.append(f"checks: {n_lines} label lines read back, {'all passed' if ok else 'FAILURES, see above'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="folder of per-sheet JSON files (extract_layouts.py output)")
    ap.add_argument("--images", required=True, help="folder with the sheet images named in the JSON")
    ap.add_argument("--dataset", required=True, help="output folder for the YOLO dataset")
    ap.add_argument("--yaml-root", default=None,
                    help="path written into data.yaml (use the Windows path when converting on another machine)")
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--test-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-side", type=int, default=0, help="downscale the copied images to this long side (0 = copy)")
    ap.add_argument("--overwrite", action="store_true", help="replace the dataset folder if it exists")
    a = ap.parse_args()

    out = Path(a.dataset)
    if out.exists():
        if not a.overwrite:
            sys.exit(f"{out} exists; add --overwrite to replace it")
        shutil.rmtree(out)
    for sp in SPLITS:
        (out / "images" / sp).mkdir(parents=True)
        (out / "labels" / sp).mkdir(parents=True)

    report: list[str] = [f"classes: {NAMES}", f"json: {a.json}", f"images: {a.images}"]
    names = NAMES
    sheets = load_sheets(Path(a.json), Path(a.images), report)
    if not sheets:
        sys.exit("no sheets could be converted; see report.txt in the dataset folder after a run")
    split_of = split_sheets([s["id"] for s in sheets], a.val_frac, a.test_frac, a.seed)

    counts = defaultdict(Counter)
    sheet_counts = Counter()
    split_record = {}
    for s in sheets:
        sp = split_of[s["id"]]
        split_record[s["id"]] = sp
        sheet_counts[sp] += 1
        write_images(s["img"], out / "images" / sp / f"{s['id']}.jpg", a.max_side)
        body = []
        for cid, line in s["lines"]:
            if line:
                body.append(line)
                counts[names[cid]][sp] += 1
        (out / "labels" / sp / f"{s['id']}.txt").write_text("\n".join(body) + ("\n" if body else ""), encoding="utf-8")

    yaml_root = Path(a.yaml_root) if a.yaml_root else out.resolve()
    data = [f"path: {yaml_root.as_posix()}", "train: images/train", "val: images/val", "test: images/test",
            f"nc: {len(names)}", "names:"] + [f"  {i}: {n}" for i, n in enumerate(names)]
    (out / "data.yaml").write_text("\n".join(data) + "\n", encoding="utf-8")
    (out / "classes.json").write_text(json.dumps(names, indent=1), encoding="utf-8")
    (out / "split.json").write_text(json.dumps(split_record, indent=1, ensure_ascii=False), encoding="utf-8")

    report.append("")
    report.append(f"sheets: {len(sheets)} (train {sheet_counts['train']}, val {sheet_counts['val']}, test {sheet_counts['test']})")
    report.append(f"{'class':14s} {'train':>7s} {'val':>7s} {'test':>7s}")
    for n in names:
        report.append(f"{n:14s} " + " ".join(f"{counts[n][sp]:7d}" for sp in SPLITS))
    report.append("")
    ok = verify(out, names, report)
    (out / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")

    print("\n".join(report))
    print(f"dataset: {out}")
    print(f"data.yaml path: {yaml_root.as_posix()}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
