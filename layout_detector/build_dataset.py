"""Build a YOLO layout-detection dataset from the extractions you already have.

Where the labels come from
    Each var/extractions/<doc_id>/doc.json holds `layouts[]`: the layout boxes Astra returned in pass 1 (they sit in
    var/cache/llm/<sha>__x2.1L__p<n>_pass1.json), already rescaled and validated by the pipeline (pixel-vs-0..1000
    mix-ups fixed, empty boxes dropped), with the layout type and the 200 dpi page size. This script reads those, so
    the cache replies are used without re-parsing them. The page images come from var/artifacts/<doc_id>/page<n>.jpg.

These are pseudo-labels: model output, not hand-drawn boxes. Use preview.py to look at them before training, and fix
or delete bad ones (the YOLO .txt files are plain text) before the next training run.

Usage
    python build_dataset.py --var ../var --out ../var/layout_dataset [--only-reviewed] [--val-frac 0.2]
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import Counter
from pathlib import Path

import cv2

# fixed order: a trained model's class ids depend on it, so never reorder, only append
CLASSES = ["equipment_layout", "section", "site_plan", "css_layout", "beam_layout", "cutout_layout",
           "cable_tray_layout", "trench_layout", "ms_plate_layout", "wayleave", "detail", "legend",
           "title_block", "notes", "table"]
CID = {c: i for i, c in enumerate(CLASSES)}

# optional coarser label set for a first run with few examples per class
COARSE = {"beam_layout": "civil_layout", "cutout_layout": "civil_layout", "cable_tray_layout": "civil_layout",
          "trench_layout": "civil_layout", "ms_plate_layout": "civil_layout", "css_layout": "civil_layout",
          "wayleave": "site_plan"}


def yolo_line(cid: int, box, W: float, H: float):
    x0, y0, x1, y1 = box
    x0, x1 = sorted((min(max(x0, 0.0), W), min(max(x1, 0.0), W)))
    y0, y1 = sorted((min(max(y0, 0.0), H), min(max(y1, 0.0), H)))
    w, h = (x1 - x0) / W, (y1 - y0) / H
    if w < 0.01 or h < 0.01:              # thinner than 1% of the page: not a real panel
        return None
    return f"{cid} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {w:.6f} {h:.6f}"


def collect(var: Path, only_reviewed: bool, min_quality: float, classes: list[str], merge: dict):
    """-> {doc_id: [(page, image_path, [label lines])]}"""
    cid = {c: i for i, c in enumerate(classes)}
    out, skipped = {}, Counter()
    for jp in sorted((var / "extractions").glob("*/doc.json")):
        d = json.loads(jp.read_text(encoding="utf-8"))
        q = d.get("quality") or {}
        if only_reviewed and q.get("status") != "reviewed":
            skipped["not reviewed"] += 1
            continue
        if (q.get("score") or 0) < min_quality:
            skipped["low quality"] += 1
            continue
        doc_id = d["doc_id"]
        pages = {p["page"]: p for p in d["source"].get("page_images") or []}
        by_page: dict[int, list[str]] = {}
        for L in d["layouts"]:
            cls = merge.get(L["type"], L["type"])
            if cls not in cid:
                skipped[f"class {cls}"] += 1
                continue
            p = pages.get(L["page"])
            if not p:
                skipped["no page image"] += 1
                continue
            W, H = p["size_px"]
            line = yolo_line(cid[cls], L["bbox_px"], W, H)
            if line is None:
                skipped["tiny box"] += 1
                continue
            by_page.setdefault(L["page"], []).append(line)
        rows = []
        for pg, lines in by_page.items():
            img = var / "artifacts" / doc_id / pages[pg]["file"]
            if img.exists():
                rows.append((pg, img, lines))
            else:
                skipped["image missing"] += 1
        if rows:
            out[doc_id] = rows
    return out, skipped


def split_docs(data: dict, classes: list[str], val_frac: float, seed: int):
    """Split by document (pages of one drawing never straddle train and val). Try many shuffles and keep the one whose
    validation share is closest to val_frac for every class, so rare classes still appear in val."""
    ids = sorted(data)
    per_doc = {i: Counter(int(l.split()[0]) for _, _, ls in data[i] for l in ls) for i in ids}
    total = Counter()
    for c in per_doc.values():
        total.update(c)
    n_val = max(1, round(val_frac * len(ids)))
    best, best_score = None, 1e9
    for s in range(300):
        rnd = random.Random(seed * 1000 + s)
        shuffled = ids[:]
        rnd.shuffle(shuffled)
        val = set(shuffled[:n_val])
        vc = Counter()
        for i in val:
            vc.update(per_doc[i])
        score = sum(abs(vc[c] / total[c] - val_frac) for c in total) + 5 * sum(1 for c in total if vc[c] == 0 and total[c] > 1)
        if score < best_score:
            best, best_score = val, score
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", default="var")
    ap.add_argument("--out", default="var/layout_dataset")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only-reviewed", action="store_true", help="use only drawings whose status is 'reviewed'")
    ap.add_argument("--min-quality", type=float, default=0.0)
    ap.add_argument("--coarse", action="store_true", help="merge the civil layout types and wayleave (fewer classes)")
    ap.add_argument("--max-side", type=int, default=0, help="downscale training images to this long side (0 = keep)")
    a = ap.parse_args()

    var, out = Path(a.var), Path(a.out)
    merge = COARSE if a.coarse else {}
    classes = list(dict.fromkeys(merge.get(c, c) for c in CLASSES))
    data, skipped = collect(var, a.only_reviewed, a.min_quality, classes, merge)
    if not data:
        raise SystemExit("no labelled pages found; check --var")
    val = split_docs(data, classes, a.val_frac, a.seed)

    if out.exists():
        shutil.rmtree(out)
    counts = {"train": Counter(), "val": Counter()}
    n_img = Counter()
    for doc_id, rows in data.items():
        sp = "val" if doc_id in val else "train"
        for pg, img, lines in rows:
            name = f"{doc_id}_p{pg}"
            (out / "images" / sp).mkdir(parents=True, exist_ok=True)
            (out / "labels" / sp).mkdir(parents=True, exist_ok=True)
            dst = out / "images" / sp / f"{name}.jpg"
            if a.max_side:
                im = cv2.imread(str(img))
                s = a.max_side / max(im.shape[:2])
                if s < 1:
                    im = cv2.resize(im, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(dst), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
            else:
                shutil.copy2(img, dst)
            (out / "labels" / sp / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            n_img[sp] += 1
            counts[sp].update(classes[int(l.split()[0])] for l in lines)

    yaml = [f"path: {out.resolve().as_posix()}", "train: images/train", "val: images/val", f"nc: {len(classes)}", "names:"]
    yaml += [f"  {i}: {c}" for i, c in enumerate(classes)]
    (out / "data.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    (out / "classes.json").write_text(json.dumps(classes), encoding="utf-8")

    print(f"{len(data)} drawings -> {n_img['train']} train pages, {n_img['val']} val pages ({len(val)} val drawings)")
    print(f"{'class':20s} {'train':>6s} {'val':>6s}")
    for c in classes:
        print(f"{c:20s} {counts['train'][c]:6d} {counts['val'][c]:6d}")
    if skipped:
        print("skipped:", dict(skipped))
    print("wrote", out / "data.yaml")


if __name__ == "__main__":
    main()
