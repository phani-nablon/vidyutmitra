"""Train a YOLO26 detector for CSS sheets: drawing blocks, block titles and the sheet title block.

Input
    --json    the per-sheet JSON files written by extract_layouts.py   (...\\pdf\\out\\bboxes\\json)
    --images  the sheet images those JSON files name                   (...\\pdf\\out)

Classes
    --classes coarse (default)   block        every drawing panel: plan, section, elevation, site_plan, detail,
                                              legend, notes, table, earthing_detail, door_schedule, other
                                 block_title  the heading of a block (title_bbox), read on its own
                                 title_block  the sheet's title block
    --classes fine               each block type as its own class (plan, section, legend, ...), plus block_title
                                 and title_block

Split
    The split is by sheet, so the pages of one drawing never appear in both train and test.
    --val-frac and --test-frac set the shares (default 0.15 and 0.15); --seed makes it repeatable.
    The split is saved to dataset\\split.json.

Steps
    1. build the YOLO dataset: images/{train,val,test}, labels/{train,val,test}, data.yaml, split.json
    2. train YOLO26 on train, validate on val (best weights are chosen on val)
    3. evaluate the best weights on test and print per-class AP50 for the test split
    The best weights are copied to models/<name>.pt.

Usage
    pip install ultralytics opencv-python-headless
    python train_yolo26_blocks.py --json "...\\pdf\\out\\bboxes\\json" --images "...\\pdf\\out" ^
                                  --dataset "...\\pdf\\out\\bboxes\\yolo_dataset" --epochs 150
    python train_yolo26_blocks.py ... --build-only        # only build the dataset and draw a few previews
    python train_yolo26_blocks.py ... --skip-build        # reuse an existing dataset folder

Notes
    - The model name is --model (default yolo26s.pt). If your Ultralytics version does not have that weight,
      pass another YOLO26 size (yolo26n.pt, yolo26m.pt) or a YOLO11 weight.
    - Drawings are large and the titles are small, so the default --imgsz is 1600. Lower it only if you run out of memory.
    - Flips and rotations are off, because the sheets are upright and text must not be mirrored.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

import cv2

FINE_TYPES = ["plan", "section", "elevation", "site_plan", "detail", "legend", "notes", "table",
              "earthing_detail", "door_schedule", "other", "title_block"]
COARSE_NAMES = ["block", "block_title", "title_block"]
FINE_NAMES = FINE_TYPES + ["block_title"]


def class_names(mode: str) -> list[str]:
    return COARSE_NAMES if mode == "coarse" else FINE_NAMES


def class_of(layout_type: str, mode: str) -> str:
    t = (layout_type or "other").lower()
    if t == "title_block":
        return "title_block"
    if mode == "coarse":
        return "block"
    return t if t in FINE_TYPES else "other"


# ---------------------------------------------------------------- dataset
def load_sheets(json_dir: Path, images_dir: Path, mode: str) -> list[dict]:
    sheets, missing = [], []
    for jp in sorted(json_dir.glob("*.json")):
        d = json.loads(jp.read_text(encoding="utf-8"))
        img = images_dir / d["file"]
        if not img.exists():
            missing.append(d["file"])
            continue
        boxes = []                                       # (class name, [x0, y0, x1, y1] in 0..1000)
        for L in d.get("layouts", []):
            if L.get("bbox"):
                boxes.append((class_of(L.get("type"), mode), L["bbox"]))
            if L.get("title_bbox"):
                boxes.append(("block_title", L["title_bbox"]))
        sheets.append({"id": jp.stem, "img": img, "boxes": boxes})
    if missing:
        print(f"warning: {len(missing)} sheet(s) have JSON but no image, skipped: {missing[:3]}")
    if not sheets:
        sys.exit("no sheets with both JSON and image; check --json and --images")
    return sheets


def split_sheets(n: int, val_frac: float, test_frac: float, seed: int) -> dict[int, str]:
    if n < 3:
        sys.exit("need at least 3 sheets to make train, val and test")
    ids = list(range(n))
    random.Random(seed).shuffle(ids)
    n_test = max(1, round(n * test_frac))
    n_val = max(1, round(n * val_frac))
    out = {}
    for i in ids[:n_test]:
        out[i] = "test"
    for i in ids[n_test:n_test + n_val]:
        out[i] = "val"
    for i in ids[n_test + n_val:]:
        out[i] = "train"
    return out


def build_dataset(sheets, names, out: Path, split_map, max_side: int) -> dict:
    if out.exists():
        shutil.rmtree(out)
    for sp in ("train", "val", "test"):
        (out / "images" / sp).mkdir(parents=True)
        (out / "labels" / sp).mkdir(parents=True)
    counts = {sp: Counter() for sp in ("train", "val", "test")}
    split_record = {}
    for i, s in enumerate(sheets):
        sp = split_map[i]
        dst_img = out / "images" / sp / f"{s['id']}.jpg"
        if max_side:
            im = cv2.imread(str(s["img"]))
            k = max_side / max(im.shape[:2])
            if k < 1:
                im = cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(dst_img), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
        else:
            shutil.copy2(s["img"], dst_img)
        lines = []
        for cname, b in s["boxes"]:
            x0, y0, x1, y1 = [min(max(float(v), 0.0), 1000.0) / 1000.0 for v in b]
            if x1 - x0 < 0.002 or y1 - y0 < 0.002:       # degenerate after clipping
                continue
            cid = names.index(cname)
            lines.append(f"{cid} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {x1 - x0:.6f} {y1 - y0:.6f}")
            counts[sp][cname] += 1
        (out / "labels" / sp / f"{s['id']}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        split_record[s["id"]] = sp
    data = [f"path: {out.resolve().as_posix()}", "train: images/train", "val: images/val", "test: images/test",
            f"nc: {len(names)}", "names:"]
    data += [f"  {i}: {n}" for i, n in enumerate(names)]
    (out / "data.yaml").write_text("\n".join(data) + "\n", encoding="utf-8")
    (out / "classes.json").write_text(json.dumps(names), encoding="utf-8")
    (out / "split.json").write_text(json.dumps(split_record, indent=1), encoding="utf-8")
    return {sp: dict(c) for sp, c in counts.items()}


def preview(out: Path, names: list[str], n: int) -> None:
    """Draw the ground-truth boxes on a few val sheets so the labels can be checked by eye."""
    colours = [(0, 160, 0), (255, 80, 0), (200, 0, 200), (0, 200, 255), (0, 0, 220), (120, 120, 0)]
    dst = out / "preview"
    dst.mkdir(exist_ok=True)
    imgs = sorted((out / "images" / "val").glob("*.jpg"))[:n]
    for p in imgs:
        im = cv2.imread(str(p))
        H, W = im.shape[:2]
        t = max(2, W // 900)
        lab = out / "labels" / "val" / f"{p.stem}.txt"
        for line in lab.read_text().splitlines():
            if not line.strip():
                continue
            c, cx, cy, w, h = line.split()
            cid = int(c)
            cx, cy, w, h = float(cx) * W, float(cy) * H, float(w) * W, float(h) * H
            col = colours[cid % len(colours)]
            cv2.rectangle(im, (int(cx - w / 2), int(cy - h / 2)), (int(cx + w / 2), int(cy + h / 2)), col, t)
            cv2.putText(im, names[cid], (int(cx - w / 2) + 4, int(cy - h / 2) + 22), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.6, W / 3000), col, max(2, t))
        k = 1600 / max(H, W)
        cv2.imwrite(str(dst / f"{p.stem}.jpg"), cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_AREA))
    print(f"previews: {dst} ({len(imgs)} val sheets)")


# ---------------------------------------------------------------- training
def train(args, data_yaml: Path, names: list[str]) -> None:
    from ultralytics import YOLO
    model = YOLO(args.model)
    model.train(
        data=str(data_yaml), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, patience=args.patience,
        device=args.device, project=args.project, name=args.name, exist_ok=True,
        fliplr=0.0, flipud=0.0, degrees=0.0, shear=0.0, perspective=0.0,
        scale=0.3, translate=0.1, hsv_h=0.0, hsv_s=0.3, hsv_v=0.3, mosaic=0.5, close_mosaic=10,
        cos_lr=True, plots=True,
    )
    best = Path(args.project) / args.name / "weights" / "best.pt"
    if not best.exists():
        sys.exit(f"training finished but {best} is missing")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, args.out)
    print("best weights ->", args.out)

    # evaluate on the held-out test sheets, which training never saw
    m = YOLO(str(args.out)).val(data=str(data_yaml), split="test", imgsz=args.imgsz, device=args.device)
    print(f"TEST  mAP50 {float(m.box.map50):.3f}   mAP50-95 {float(m.box.map):.3f}")
    for i, ap in enumerate(m.box.ap50):
        name = names[i] if i < len(names) else str(i)
        print(f"  {name:20s} AP50 {float(ap):.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="folder of per-sheet JSON files (extract_layouts.py output)")
    ap.add_argument("--images", required=True, help="folder with the sheet images named in the JSON")
    ap.add_argument("--dataset", required=True, help="where the YOLO dataset is written")
    ap.add_argument("--classes", choices=["coarse", "fine"], default="coarse")
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--test-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-side", type=int, default=0, help="downscale copied images to this long side (0 = keep)")
    ap.add_argument("--build-only", action="store_true", help="build the dataset and previews, then stop")
    ap.add_argument("--skip-build", action="store_true", help="reuse the dataset already in --dataset")
    ap.add_argument("--preview", type=int, default=4, help="number of val sheets to draw for checking")
    ap.add_argument("--model", default="yolo26s.pt", help="starting weights (YOLO26 size, or a YOLO11 weight)")
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--imgsz", type=int, default=1600)
    ap.add_argument("--batch", type=int, default=-1, help="-1 = automatic on GPU; 2 to 4 on CPU")
    ap.add_argument("--patience", type=int, default=40)
    ap.add_argument("--device", default=None, help="0 for the first GPU, cpu for CPU; default = automatic")
    ap.add_argument("--project", default="runs/blocks")
    ap.add_argument("--name", default="yolo26_blocks")
    ap.add_argument("--out", default="models/css_blocks_yolo26.pt")
    args = ap.parse_args()

    out = Path(args.dataset)
    names = class_names(args.classes)
    if args.skip_build and (out / "classes.json").exists():
        names = json.loads((out / "classes.json").read_text(encoding="utf-8"))   # names from the converted dataset
    data_yaml = out / "data.yaml"

    if not args.skip_build:
        sheets = load_sheets(Path(args.json), Path(args.images), args.classes)
        split_map = split_sheets(len(sheets), args.val_frac, args.test_frac, args.seed)
        counts = build_dataset(sheets, names, out, split_map, args.max_side)
        sizes = Counter(split_map.values())
        print(f"{len(sheets)} sheets: train {sizes['train']}, val {sizes['val']}, test {sizes['test']}")
        print(f"{'class':14s} {'train':>6s} {'val':>6s} {'test':>6s}")
        for n in names:
            print(f"{n:14s} " + " ".join(f"{counts[sp].get(n, 0):6d}" for sp in ("train", "val", "test")))
        print("wrote", data_yaml)
        if args.preview:
            preview(out, names, args.preview)
    if args.build_only:
        return
    if not data_yaml.exists():
        sys.exit(f"no dataset at {out}; run without --skip-build first")
    train(args, data_yaml, names)


if __name__ == "__main__":
    main()
