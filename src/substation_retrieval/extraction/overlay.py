"""Draw an extraction on the original drawing so it can be checked by eye.

Writes, per page: the page image with layout frames, every segment polygon (room, equipment, doors, cutouts ...),
each equipment box labelled with its id, and every clearance that involves that item (mm). Also returns a per-item
table (item, class, size, ceiling clearance, clearances to neighbours / walls) for a CSV."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

# RGB (the drawing is RGB). One distinct, saturated colour per class so the polygons stand out on busy linework.
COL = {"room": (220, 30, 30), "equipment": (20, 110, 200), "door": (0, 150, 60), "rolling_shutter": (0, 150, 60),
       "cutout": (200, 0, 200), "beam": (255, 140, 0), "column": (120, 60, 0), "site_boundary": (0, 0, 0),
       "platform": (0, 170, 170), "access_path": (240, 200, 0), "cable_tray": (0, 200, 0), "trench": (0, 0, 255),
       "internal_road": (130, 130, 130), "dp_road": (130, 130, 130), "ventilation": (160, 0, 255), "staircase": (255, 0, 100)}
OTHER = (90, 90, 90)


def _ref(label) -> str:
    return "".join(ch for ch in str(label or "").lower() if ch.isalnum())


def item_clearances(doc: dict) -> list[dict]:
    """One row per equipment item with every clearance that names it (a or b)."""
    rows = []
    for sub in doc.get("substations") or []:
        cl = sub.get("clearances") or []
        for e in (sub.get("electrical") or {}).get("equipment") or []:
            keys = {_ref(e.get("id")), _ref(e.get("label"))} - {""}
            mine = []
            for c in cl:
                a, b = _ref(c.get("a")), _ref(c.get("b"))
                if a in keys or b in keys:
                    other = c.get("b") if a in keys else c.get("a")
                    if c.get("mm") is not None:
                        mine.append((other, c.get("mm")))
            rows.append({"substation": sub.get("name") or sub.get("id"), "item": e.get("label") or e.get("id"),
                         "class": e.get("ontology_class") or e.get("equipment_type"),
                         "L_mm": e.get("L_mm"), "B_mm": e.get("B_mm"), "H_mm": e.get("H_mm"),
                         "ceiling_clearance_mm": e.get("ceiling_clearance_mm"),
                         "clearances": "; ".join(f"{o}: {mm} mm" for o, mm in mine),
                         "n_clearances": len(mine), "bbox_px": e.get("bbox_px"), "page": sub.get("page")})
    return rows


def write_csv(rows: list[dict], path) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def draw_page(doc: dict, page: int, img: np.ndarray) -> np.ndarray:
    import cv2
    out = img.copy()
    t = max(2, out.shape[1] // 900)
    fs = max(0.5, out.shape[1] / 2600)
    for L in doc.get("layouts") or []:
        if L.get("page") == page and L.get("bbox_px"):
            x0, y0, x1, y1 = (int(v) for v in L["bbox_px"])
            cv2.rectangle(out, (x0, y0), (x1, y1), (150, 150, 150), t)
            cv2.putText(out, f'{L["id"]} {L.get("type")}', (x0 + 4, y0 + int(28 * fs * 2)), cv2.FONT_HERSHEY_SIMPLEX, fs, (110, 110, 110), 1)
    shade = out.copy()
    seen: dict = {}
    for sg in doc.get("segments") or []:
        if sg.get("page") != page or not sg.get("polygon_px"):
            continue
        pts = np.array(sg["polygon_px"], np.int32)
        col = COL.get(sg.get("cls"), OTHER)
        cv2.fillPoly(shade, [pts], col)
        cv2.polylines(out, [pts], True, col, t)
        seen[sg.get("cls")] = seen.get(sg.get("cls"), 0) + 1
    out = cv2.addWeighted(shade, 0.25, out, 0.75, 0)
    for k, (cls, n) in enumerate(sorted(seen.items())):                    # key: class colour + how many segments
        y = 30 + k * int(34 * fs * 2)
        c = COL.get(cls, OTHER)
        cv2.rectangle(out, (20, y - int(22 * fs * 2)), (20 + int(30 * fs * 2), y), c, -1)
        cv2.putText(out, f"{cls} ({n})", (30 + int(30 * fs * 2), y), cv2.FONT_HERSHEY_SIMPLEX, fs * 1.2, (0, 0, 0), 2, cv2.LINE_AA)
    rows = {(r["substation"], r["item"]): r for r in item_clearances(doc)}
    for sub in doc.get("substations") or []:
        for e in (sub.get("electrical") or {}).get("equipment") or []:
            if sub.get("page") not in (None, page) or not e.get("bbox_px"):
                continue
            x0, y0, x1, y1 = (int(v) for v in e["bbox_px"])
            r = rows.get((sub.get("name") or sub.get("id"), e.get("label") or e.get("id")), {})
            cv2.rectangle(out, (x0, y0), (x1, y1), COL["equipment"], t + 1)
            txt = f'{e.get("label") or e.get("id")}'
            if e.get("ceiling_clearance_mm") is not None:
                txt += f' ceil {int(e["ceiling_clearance_mm"])}'
            cv2.putText(out, txt, (x0, max(12, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, fs, COL["equipment"], 1, cv2.LINE_AA)
            for k, part in enumerate((r.get("clearances") or "").split("; ")[:4]):
                if part:
                    cv2.putText(out, part, (x0, y1 + int(18 * fs * 2) * (k + 1)), cv2.FONT_HERSHEY_SIMPLEX, fs * 0.9, (0, 0, 180), 1, cv2.LINE_AA)
    return out


def write_overlays(doc: dict, pdf, dpi: float, out_dir) -> list[str]:
    import cv2
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for pg in sorted({L.get("page") for L in doc.get("layouts") or [] if L.get("page")}):
        img = pdf.render(pg - 1, dpi)
        p = out_dir / f"overlay_p{pg}.png"
        cv2.imwrite(str(p), cv2.cvtColor(draw_page(doc, pg, img), cv2.COLOR_RGB2BGR))   # render() is RGB, imwrite wants BGR
        files.append(str(p))
    rows = item_clearances(doc)
    write_csv(rows, out_dir / "items_clearances.csv")
    return files
