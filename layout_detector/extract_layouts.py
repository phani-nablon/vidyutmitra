"""Ask Astra for every named drawing panel (layout) on each CSS sheet, with its bounding box.

    pip install openai numpy opencv-python-headless
    set ASTRA_API_KEY=...            (or OPENAI_API_KEY)
    set ASTRA_BASE_URL=...           (only if your gateway needs a base URL)
    python extract_layouts.py --src "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out" ^
                              --out "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\layouts_astra" --limit 3

Per sheet
  1. Orientation call on a small copy: how far the sheet must turn clockwise so its text reads upright.
     Several sheets in the set are sideways, so this matters.
  2. Layout call on the upright sheet (about 3000 px on the long side): every panel with its name, type and box.
     Panels are found from whitespace and titles as well as lines, and legend, title block, notes, tables and
     details are returned too.
  The boxes are mapped back to the original image.

Output (under --out)
    json/<sheet>.json       per layout: name (the heading as written), bbox (drawing only) and title_bbox (heading only,
                            which may sit above, below or beside the drawing),
                            both in 0..1000 of the original sheet and in pixels; flags; token usage
    overlays/<sheet>.jpg    panel boxes in green (red when flagged) and heading boxes in blue, for checking by eye
    summary.csv             one row per sheet
    cache/                  raw Astra replies; a rerun does not call Astra again for a sheet that is already done

Coordinates
    Astra returns boxes as [x0, y0, x1, y1] on a 0..1000 scale, x to the right and y down, top-left origin.
    They are converted to the original sheet's pixels for the overlay and the JSON.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

MODEL = os.environ.get("ASTRA_MODEL", "gpt-6-astra")
PROMPT_VERSION = "layouts-v1"
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

TYPES = ["plan", "section", "elevation", "site_plan", "legend", "title_block", "notes", "table",
         "detail", "door_schedule", "earthing_detail", "other"]

SYSTEM = (
    "You read AEML consumer-substation (CSS) drawing sheets for Adani Electricity Mumbai. "
    "You return strict JSON only. Copy titles exactly as written on the sheet. Never invent a title: use null when a "
    "drawing has none. Boxes are estimates of where each drawing sits; enclose the whole drawing, including its title."
)

ORIENT_PROMPT = (
    "This is an engineering drawing sheet. It may be sideways. "
    "Return JSON {\"rotation_deg\": 0 | 90 | 180 | 270}, where the value is the clockwise rotation that makes the sheet's "
    "titles and text read left to right. 0 means the sheet is already upright. Nothing else."
)

LAYOUT_PROMPT = """Find every separate drawing area on this sheet and give its name as written.

A panel is one drawing: a plan, section, elevation, site plan, detail, a legend box, the title block, a notes block,
a table or door schedule, or an earthing detail. Many sheets have no lines between panels, so separate them this way:
- A drawing's heading (for example "SUBSTATION LAYOUT", "EQUIPMENT LAYOUT", "TRENCH LAYOUT", "SITE PLAN", "SECTION B-B",
  "FRONT VIEW", "DOOR DETAILS", "EARTHING DETAILS") names the drawing it belongs to. The heading can sit above, below or
  beside the drawing. The panel box covers the drawing with its dimension strings, leader lines and labels, and it does
  NOT include the heading. Return the heading separately as title_bbox.
- A gap of empty paper between clusters of linework is a panel boundary even when no line is drawn.
- A legend is a block of colour or hatch swatches with text beside them. Notes are numbered text lines. The title block
  is the bordered table with drawing number, project, scale, date and signatures.
- Do not split one drawing into several boxes. Do not merge two drawings side by side. Do not box the sheet border.
- Each panel box must enclose all of its linework and labels, but not its heading. Boxes may overlap only where the
  drawings really overlap.
- If the sheet holds one drawing only, return one box for it.
- Sketches of rotated text or labels such as "PODIUM PARKING" or "DRIVEWAY" are part of the drawing they sit in, not separate panels.

Return JSON:
{"layouts": [ {"name": "title exactly as written, or null",
               "type": one of """ + str(TYPES) + """,
               "scale_text": "e.g. 1:100, NTS, or null",
               "bbox": [x0, y0, x1, y1],
               "title_bbox": [x0, y0, x1, y1] or null,
               "separated_by": "lines | whitespace | title_only | mixed",
               "confidence": 0.0 to 1.0} ]}

bbox is normalised 0..1000 to THIS image, x to the right, y down, top-left origin.
bbox encloses the drawing only (linework, dimensions, labels), not its heading.
title_bbox encloses only the heading text as written (for example "SUBSTATION LAYOUT", including its underline if it has one).
It can sit above, below or beside bbox, and it is required for every drawing that has a heading. Use null only when the
drawing has no heading at all.
List panels reading left to right, then top to bottom."""


# ---------------------------------------------------------------- image helpers
def load_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"cannot read image {path}")
    return bgr[:, :, ::-1].copy()


def rot_cw(img: np.ndarray, k: int) -> np.ndarray:
    """Rotate an image clockwise by k quarter turns."""
    return np.rot90(img, -(k % 4)).copy()


def resize_long(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


def jpeg_bytes(img_rgb: np.ndarray, quality: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", img_rgb[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("jpeg encode failed")
    return buf.tobytes()


# ---------------------------------------------------------------- geometry: upright <-> original
# Normalised (u, v) in 0..1, origin top-left. k = quarter turns clockwise applied to the ORIGINAL to make it upright.
def to_upright(u: float, v: float, k: int) -> tuple[float, float]:
    k %= 4
    if k == 0:
        return u, v
    if k == 1:
        return 1 - v, u
    if k == 2:
        return 1 - u, 1 - v
    return v, 1 - u


def from_upright(u: float, v: float, k: int) -> tuple[float, float]:
    k %= 4
    if k == 0:
        return u, v
    if k == 1:
        return v, 1 - u
    if k == 2:
        return 1 - u, 1 - v
    return 1 - v, u


def box_from_upright(b01: list[float], k: int) -> list[float]:
    """Upright box [x0,y0,x1,y1] in 0..1 -> box in the original sheet's 0..1 frame."""
    p = from_upright(b01[0], b01[1], k)
    q = from_upright(b01[2], b01[3], k)
    return [min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])]


# ---------------------------------------------------------------- Astra client (Responses API, JSON output)
def parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(text[i:j + 1])


class Astra:
    def __init__(self, cache_dir: Path, timeout: float = 300.0):
        from openai import OpenAI
        key = os.environ.get("ASTRA_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            sys.exit("set ASTRA_API_KEY (or OPENAI_API_KEY) in the environment first")
        kw = {"api_key": key, "timeout": timeout}
        if os.environ.get("ASTRA_BASE_URL"):
            kw["base_url"] = os.environ["ASTRA_BASE_URL"]
        self.client = OpenAI(**kw)
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)

    def ask(self, key: str, user: str, jpg: bytes, max_tokens: int = 8000, retries: int = 2) -> dict:
        """Returns {"output": parsed JSON, "usage": {...}}. Cached on disk under `key`."""
        p = self.cache / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        b64 = base64.b64encode(jpg).decode()
        last = None
        for attempt in range(retries + 1):
            try:
                r = self.client.responses.create(
                    model=MODEL,
                    instructions=SYSTEM,
                    input=[{"role": "user", "content": [
                        {"type": "input_text", "text": user},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                    ]}],
                    text={"format": {"type": "json_object"}},
                    max_output_tokens=max_tokens,
                )
                out = parse_json(getattr(r, "output_text", "") or "")
                usage = {}
                if getattr(r, "usage", None):
                    usage = {"input_tokens": getattr(r.usage, "input_tokens", None),
                             "output_tokens": getattr(r.usage, "output_tokens", None)}
                res = {"output": out, "usage": usage, "model": MODEL}
                p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                return res
            except Exception as e:                       # network, rate limit, or unparseable reply
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Astra failed for {key}: {last}")


# ---------------------------------------------------------------- validation of Astra's boxes
def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def clean_layouts(raw: list[dict], k: int, W: int, H: int) -> tuple[list[dict], list[str]]:
    """Turn Astra's layouts into records with boxes on the original sheet. Problems are flagged, not dropped,
    except for boxes that cannot be a drawing at all."""
    issues = []
    recs = []
    for i, L in enumerate(raw or []):
        b = L.get("bbox")
        if not (isinstance(b, list) and len(b) == 4 and all(isinstance(x, (int, float)) for x in b)):
            issues.append(f"layout {i + 1}: bbox missing or malformed, dropped")
            continue
        x0, y0, x1, y1 = [float(x) for x in b]
        x0, x1 = sorted((min(max(x0, 0), 1000), min(max(x1, 0), 1000)))
        y0, y1 = sorted((min(max(y0, 0), 1000), min(max(y1, 0), 1000)))
        if (x1 - x0) < 15 or (y1 - y0) < 15:
            issues.append(f"layout {i + 1} ({L.get('name')}): box under 1.5% of the sheet, dropped")
            continue
        up = [x0 / 1000, y0 / 1000, x1 / 1000, y1 / 1000]
        orig = box_from_upright(up, k)
        t = str(L.get("type") or "other").lower()
        if t not in TYPES:
            t = "other"
        flags = []
        if (x1 - x0) * (y1 - y0) > 900 * 900 * 0.6:
            flags.append("covers most of the sheet")
        name = L.get("name")
        if (name is None or str(name).strip() == "") and t not in ("legend", "title_block", "notes"):
            flags.append("no title read")

        # title box: the heading text only, mapped to the original sheet like the panel box
        title_orig, title_px = None, None
        tb = L.get("title_bbox")
        if isinstance(tb, list) and len(tb) == 4 and all(isinstance(v, (int, float)) for v in tb):
            tx0, ty0, tx1, ty1 = [float(v) for v in tb]
            tx0, tx1 = sorted((min(max(tx0, 0), 1000), min(max(tx1, 0), 1000)))
            ty0, ty1 = sorted((min(max(ty0, 0), 1000), min(max(ty1, 0), 1000)))
            if (tx1 - tx0) >= 5 and (ty1 - ty0) >= 5:
                t_orig = box_from_upright([tx0 / 1000, ty0 / 1000, tx1 / 1000, ty1 / 1000], k)
                title_orig = [round(v * 1000, 1) for v in t_orig]
                title_px = [round(t_orig[0] * W), round(t_orig[1] * H), round(t_orig[2] * W), round(t_orig[3] * H)]
            else:
                flags.append("title box too small, ignored")
        elif t not in ("legend", "title_block", "notes"):
            flags.append("no title box")

        recs.append({
            "name": (str(name).strip() if name else None),
            "type": t,
            "scale_text": L.get("scale_text"),
            "separated_by": L.get("separated_by"),
            "confidence": L.get("confidence"),
            "bbox": [round(v * 1000, 1) for v in orig],               # 0..1000 of the original sheet
            "bbox_px": [round(orig[0] * W), round(orig[1] * H), round(orig[2] * W), round(orig[3] * H)],
            "title_bbox": title_orig,                                  # 0..1000 of the original sheet, or None
            "title_bbox_px": title_px,
            "flags": flags,
        })
    # overlapping boxes: report, they may be real overlaps of drawings
    for a in range(len(recs)):
        for b in range(a + 1, len(recs)):
            if _iou(recs[a]["bbox"], recs[b]["bbox"]) > 0.3:
                recs[a]["flags"].append(f"overlaps P{b + 1}")
                recs[b]["flags"].append(f"overlaps P{a + 1}")
    for n, r in enumerate(recs, start=1):
        r["id"] = f"P{n}"
    return recs, issues


# ---------------------------------------------------------------- per-sheet pipeline
def process_sheet(path: Path, out: Path, astra: Astra, max_side: int) -> dict:
    raw_bytes = path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    img = load_rgb(path)
    H, W = img.shape[:2]

    # 1. orientation on a small copy
    small = resize_long(img, 1200)
    o = astra.ask(f"{sha[:16]}_orient_{PROMPT_VERSION}", ORIENT_PROMPT, jpeg_bytes(small, 85), max_tokens=200)
    try:
        deg = int(o["output"].get("rotation_deg", 0))
    except (TypeError, ValueError):
        deg = 0
    if deg not in (0, 90, 180, 270):
        deg = 0
    k = deg // 90

    # 2. layouts on the upright sheet
    upright = resize_long(rot_cw(img, k), max_side)
    lay = astra.ask(f"{sha[:16]}_layouts_k{k}_s{max_side}_{PROMPT_VERSION}", LAYOUT_PROMPT,
                    jpeg_bytes(upright, 90), max_tokens=8000)
    raw = lay["output"].get("layouts") or []
    layouts, issues = clean_layouts(raw, k, W, H)
    if not layouts:
        issues.append("no layouts returned")

    usage = {"orient": o.get("usage", {}), "layouts": lay.get("usage", {})}
    doc = {"file": path.name, "sha256": sha, "image_px": [W, H], "rotation_deg": deg, "model": MODEL,
           "prompt_version": PROMPT_VERSION, "layouts": layouts, "issues": issues, "usage": usage}
    (out / "json").mkdir(parents=True, exist_ok=True)
    (out / "json" / f"{path.stem}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")

    # overlay on the original orientation, so it lines up with the sheet as it was scanned
    ov = resize_long(img, 1800).copy()
    s = ov.shape[1] / W
    th = max(2, ov.shape[1] // 600)
    for r in layouts:
        x0, y0, x1, y1 = [int(v * s) for v in r["bbox_px"]]
        colour = (0, 160, 0) if not r["flags"] else (220, 0, 0)
        cv2.rectangle(ov, (x0, y0), (x1, y1), colour, th)
        label = f"{r['id']} {r['name'] or r['type']}"
        cv2.putText(ov, label, (x0 + 4, max(14, y0 + 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        if r["title_bbox_px"]:                              # title / heading box in blue
            tx0, ty0, tx1, ty1 = [int(v * s) for v in r["title_bbox_px"]]
            cv2.rectangle(ov, (tx0, ty0), (tx1, ty1), (255, 80, 0), max(1, th - 1))
    (out / "overlays").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "overlays" / f"{path.stem}.jpg"), ov[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="folder with the sheet images (the pdf\\out folder)")
    ap.add_argument("--out", default=None, help="output folder (default: layouts_astra next to --src)")
    ap.add_argument("--max-side", type=int, default=3000, help="long side of the image sent for layouts")
    ap.add_argument("--limit", type=int, default=0, help="only the first N sheets (for a test run)")
    ap.add_argument("--overwrite", action="store_true", help="ignore the per-sheet JSON already in --out")
    a = ap.parse_args()

    src = Path(a.src)
    out = Path(a.out) if a.out else src.parent / "layouts_astra"
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT)
    if a.limit:
        files = files[: a.limit]
    if not files:
        sys.exit(f"no images in {src}")

    astra = Astra(out / "cache")
    rows = []
    for n, f in enumerate(files, start=1):
        done = out / "json" / f"{f.stem}.json"
        if done.exists() and not a.overwrite:
            doc = json.loads(done.read_text(encoding="utf-8"))
            print(f"[{n}/{len(files)}] {f.name}: already done ({len(doc['layouts'])} layouts)")
        else:
            try:
                doc = process_sheet(f, out, astra, a.max_side)
                print(f"[{n}/{len(files)}] {f.name}: rotation {doc['rotation_deg']}, {len(doc['layouts'])} layouts, "
                      f"{sum(1 for r in doc['layouts'] if r['flags'])} flagged")
            except Exception as e:                       # one bad sheet should not stop the batch
                print(f"[{n}/{len(files)}] FAILED {f.name}: {e}")
                traceback.print_exc()
                continue
        rows.append({"file": f.name, "rotation_deg": doc["rotation_deg"], "layouts": len(doc["layouts"]),
                     "flagged": sum(1 for r in doc["layouts"] if r["flags"]),
                     "names": " | ".join(r["name"] or r["type"] for r in doc["layouts"]),
                     "issues": " ; ".join(doc["issues"])})

    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "rotation_deg", "layouts", "flagged", "names", "issues"])
        w.writeheader()
        w.writerows(rows)
    print(f"done: {len(rows)} sheets -> {out}")


if __name__ == "__main__":
    main()
