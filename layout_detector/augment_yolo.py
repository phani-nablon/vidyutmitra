"""Augment the TRAIN split of a YOLO dataset, offline. Val and test are copied unchanged.

    python augment_yolo.py --src "C:\\...\\pdf\\out\\bboxes\\yolo_dataset_2class" ^
                           --dst "C:\\...\\pdf\\out\\bboxes\\yolo_dataset_2class_aug" ^
                           --yaml-root "C:\\...\\pdf\\out\\bboxes\\yolo_dataset_2class_aug"

Default operations, applied to every train sheet (each one gives one new image; the original is kept):
    rot90     rotate 90 degrees clockwise
    fliplr    mirror left to right
    flipud    mirror top to bottom
    noise     slight Gaussian noise (sigma 5 on the 0..255 scale)
    blur      slight Gaussian blur (3 x 3 kernel)
So each train image becomes 6 images. --ops picks a subset, and --rot-all adds rot180 and rot270.

Labels follow the image
    flips and rotations move the boxes with the pixels; noise and blur leave them as they are.
    A rot90 swaps the box width and height. Boxes are clipped to the image after the move.

Only the train split is augmented. The output folder is a new dataset, so the source is never changed.
The output folder gets its own data.yaml, classes.json, split.json, and augment_report.txt.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

GEOM = {"rot90", "rot180", "rot270", "fliplr", "flipud"}
PHOTO = {"noise", "blur"}
DEFAULT_OPS = ["rot90", "fliplr", "flipud", "noise", "blur"]


# ---------------------------------------------------------------- image operations
def apply_image(img: np.ndarray, op: str, rng: random.Random, noise_sigma: float, blur_k: int) -> np.ndarray:
    if op == "rot90":
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if op == "rot180":
        return cv2.rotate(img, cv2.ROTATE_180)
    if op == "rot270":
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if op == "fliplr":
        return cv2.flip(img, 1)
    if op == "flipud":
        return cv2.flip(img, 0)
    if op == "noise":
        # the seed comes from the shared RNG, so reruns give the same noise
        gen = np.random.default_rng(rng.randrange(2**31))
        noisy = img.astype(np.float32) + gen.normal(0.0, noise_sigma, img.shape).astype(np.float32)
        return np.clip(noisy, 0, 255).astype(np.uint8)
    if op == "blur":
        return cv2.GaussianBlur(img, (blur_k, blur_k), 0)
    raise ValueError(f"unknown op {op}")


# ---------------------------------------------------------------- label operations (YOLO: cls cx cy w h, 0..1)
def apply_label(line: str, op: str) -> str:
    parts = line.split()
    c = parts[0]
    cx, cy, w, h = [float(v) for v in parts[1:5]]
    if op == "rot90":            # clockwise: (cx, cy) -> (1 - cy, cx), width and height swap
        cx, cy, w, h = 1 - cy, cx, h, w
    elif op == "rot180":
        cx, cy = 1 - cx, 1 - cy
    elif op == "rot270":         # counter-clockwise: (cx, cy) -> (cy, 1 - cx), width and height swap
        cx, cy, w, h = cy, 1 - cx, h, w
    elif op == "fliplr":
        cx = 1 - cx
    elif op == "flipud":
        cy = 1 - cy
    # clip so the box stays inside the image
    x0, x1 = max(0.0, cx - w / 2), min(1.0, cx + w / 2)
    y0, y1 = max(0.0, cy - h / 2), min(1.0, cy + h / 2)
    return f"{c} {(x0 + x1) / 2:.6f} {(y0 + y1) / 2:.6f} {x1 - x0:.6f} {y1 - y0:.6f}"


def apply_labels(text: str, op: str) -> str:
    out = [apply_label(l, op) for l in text.splitlines() if l.strip()]
    return ("\n".join(out) + "\n") if out else ""


# ---------------------------------------------------------------- dataset
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="converted YOLO dataset (convert_json_to_yolo.py output)")
    ap.add_argument("--dst", required=True, help="new folder for the augmented dataset")
    ap.add_argument("--yaml-root", default=None, help="path written into data.yaml (default: --dst)")
    ap.add_argument("--ops", default=",".join(DEFAULT_OPS), help="comma list from: " + ",".join(sorted(GEOM | PHOTO)))
    ap.add_argument("--rot-all", action="store_true", help="also add rot180 and rot270")
    ap.add_argument("--noise-sigma", type=float, default=5.0, help="noise standard deviation on the 0..255 scale")
    ap.add_argument("--blur-k", type=int, default=3, help="Gaussian kernel size (odd number)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()

    src, dst = Path(a.src), Path(a.dst)
    ops = [o.strip() for o in a.ops.split(",") if o.strip()]
    if a.rot_all:
        ops += ["rot180", "rot270"]
    bad = [o for o in ops if o not in GEOM | PHOTO]
    if bad:
        sys.exit(f"unknown ops: {bad}")
    if a.blur_k % 2 == 0 or a.blur_k < 1:
        sys.exit("--blur-k must be an odd number")
    if not (src / "data.yaml").exists():
        sys.exit(f"no data.yaml in {src}; run convert_json_to_yolo.py first")
    if dst.exists():
        if not a.overwrite:
            sys.exit(f"{dst} exists; add --overwrite to replace it")
        shutil.rmtree(dst)
    for sp in ("train", "val", "test"):
        (dst / "images" / sp).mkdir(parents=True)
        (dst / "labels" / sp).mkdir(parents=True)

    rng = random.Random(a.seed)
    report = [f"source: {src}", f"ops on train: {ops}", f"noise sigma {a.noise_sigma}, blur kernel {a.blur_k}"]
    totals = {"orig_images": 0, "new_images": 0, "orig_boxes": 0, "new_boxes": 0}

    # val and test: copied unchanged
    for sp in ("val", "test"):
        for img in sorted((src / "images" / sp).glob("*.jpg")):
            shutil.copy2(img, dst / "images" / sp / img.name)
            lab = src / "labels" / sp / f"{img.stem}.txt"
            shutil.copy2(lab, dst / "labels" / sp / lab.name)

    # train: original + one image per op
    for img_path in sorted((src / "images" / "train").glob("*.jpg")):
        stem = img_path.stem
        lab_text = (src / "labels" / "train" / f"{stem}.txt").read_text(encoding="utf-8")
        n_boxes = len([l for l in lab_text.splitlines() if l.strip()])
        totals["orig_images"] += 1
        totals["orig_boxes"] += n_boxes

        img = cv2.imread(str(img_path))
        if img is None:
            report.append(f"SKIPPED {img_path.name}: cannot read")
            continue
        shutil.copy2(img_path, dst / "images" / "train" / img_path.name)
        (dst / "labels" / "train" / f"{stem}.txt").write_text(lab_text, encoding="utf-8")
        totals["new_images"] += 1
        totals["new_boxes"] += n_boxes

        for op in ops:
            out_img = apply_image(img, op, rng, a.noise_sigma, a.blur_k)
            name = f"{stem}__{op}"
            cv2.imwrite(str(dst / "images" / "train" / f"{name}.jpg"), out_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            new_lab = apply_labels(lab_text, op) if op in GEOM else lab_text
            (dst / "labels" / "train" / f"{name}.txt").write_text(new_lab, encoding="utf-8")
            totals["new_images"] += 1
            totals["new_boxes"] += len([l for l in new_lab.splitlines() if l.strip()])

    # the rest of the dataset description
    root = Path(a.yaml_root) if a.yaml_root else dst.resolve()
    src_yaml = (src / "data.yaml").read_text(encoding="utf-8").splitlines()
    names = [l for l in src_yaml if l.startswith("  ") or l.startswith("names:") or l.startswith("nc:")]
    (dst / "data.yaml").write_text("\n".join([f"path: {root.as_posix()}", "train: images/train", "val: images/val",
                                             "test: images/test"] + names) + "\n", encoding="utf-8")
    for f in ("classes.json", "split.json"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)

    report.append("")
    report.append(f"train sheets: {totals['orig_images']}  ->  train images after augmentation: {totals['new_images']} "
                  f"(original + {len(ops)} per sheet)")
    report.append(f"train boxes: {totals['orig_boxes']}  ->  {totals['new_boxes']}")
    report.append(f"val and test copied unchanged")
    (dst / "augment_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"dataset: {dst}")
    print(f"data.yaml path: {root.as_posix()}")


if __name__ == "__main__":
    main()
