"""Draw boxes on pages so you can check the pseudo-labels (and later the model).

    python preview.py --dataset ../var/layout_dataset --split val --n 12                 # labels only
    python preview.py --dataset ../var/layout_dataset --split val --n 12 --weights models/layout_detector.pt
Output: <dataset>/preview/<name>.jpg  (labels in green, predictions in red when --weights is given)
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2


def draw(img, boxes, colour, names, tag=""):
    H, W = img.shape[:2]
    t = max(2, W // 700)
    for cid, cx, cy, w, h, *conf in boxes:
        x0, y0, x1, y1 = int((cx - w / 2) * W), int((cy - h / 2) * H), int((cx + w / 2) * W), int((cy + h / 2) * H)
        cv2.rectangle(img, (x0, y0), (x1, y1), colour, t)
        label = names[int(cid)] + (f" {conf[0]:.2f}" if conf else "")
        cv2.putText(img, tag + label, (x0 + 4, y0 + 8 + t * 6), cv2.FONT_HERSHEY_SIMPLEX, W / 2400 * 1.0, colour, max(2, t - 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="var/layout_dataset")
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--conf", type=float, default=0.25)
    a = ap.parse_args()
    ds = Path(a.dataset)
    names = json.loads((ds / "classes.json").read_text())
    imgs = sorted((ds / "images" / a.split).glob("*.jpg"))
    random.Random(0).shuffle(imgs)
    model = None
    if a.weights:
        from ultralytics import YOLO
        model = YOLO(a.weights)
    out = ds / "preview"
    out.mkdir(exist_ok=True)
    for p in imgs[: a.n]:
        img = cv2.imread(str(p))
        lab = ds / "labels" / a.split / f"{p.stem}.txt"
        gt = [[float(v) for v in l.split()] for l in lab.read_text().splitlines() if l.strip()] if lab.exists() else []
        draw(img, gt, (0, 160, 0), names)
        if model:
            r = model.predict(img, conf=a.conf, imgsz=1280, verbose=False)[0]
            pr = [[int(c), *b, float(s)] for c, b, s in zip(r.boxes.cls.tolist(), r.boxes.xywhn.tolist(), r.boxes.conf.tolist())]
            draw(img, pr, (0, 0, 220), names, tag="pred ")
        cv2.imwrite(str(out / f"{p.stem}.jpg"), cv2.resize(img, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA))
    print("wrote", min(a.n, len(imgs)), "previews to", out)


if __name__ == "__main__":
    main()
