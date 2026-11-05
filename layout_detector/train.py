"""Train the layout detector (Ultralytics YOLO).

    pip install ultralytics
    python train.py --data ../var/layout_dataset/data.yaml --model yolo11s.pt --epochs 100

Settings are chosen for engineering sheets: panels are large objects, so a moderate image size is enough; no flips
(mirrored text is not what you will see); mosaic is kept but switched off for the last epochs.
The best weights are copied to models/layout_detector.pt, the file layouts.Detector loads.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="var/layout_dataset/data.yaml")
    ap.add_argument("--model", default="yolo11s.pt", help="starting weights; any Ultralytics detection model")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--batch", type=int, default=-1, help="-1 = automatic on GPU; use 2 to 4 on CPU")
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--device", default=None, help="0 for the first GPU, cpu for CPU; default = automatic")
    ap.add_argument("--project", default="runs/layout")
    ap.add_argument("--name", default="v1")
    ap.add_argument("--out", default="models/layout_detector.pt")
    a = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(a.model)
    model.train(
        data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, patience=a.patience, device=a.device,
        project=a.project, name=a.name, exist_ok=True,
        fliplr=0.0, flipud=0.0, degrees=0.0, shear=0.0, perspective=0.0,    # sheets are upright and unmirrored
        scale=0.3, translate=0.1, hsv_h=0.0, hsv_s=0.3, hsv_v=0.3, mosaic=0.5, close_mosaic=10,
        cos_lr=True, plots=True,
    )
    best = Path(a.project) / a.name / "weights" / "best.pt"
    if best.exists():
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, a.out)
        print("best weights ->", a.out)
    m = YOLO(a.out).val(data=a.data, imgsz=a.imgsz, device=a.device)
    print("val mAP50:", round(float(m.box.map50), 3), " mAP50-95:", round(float(m.box.map), 3))
    for i, c in enumerate(m.names.values()):
        if i < len(m.box.ap50):
            print(f"  {c:20s} AP50 {m.box.ap50[i]:.3f}")


if __name__ == "__main__":
    main()
