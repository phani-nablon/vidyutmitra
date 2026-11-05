"""Send equipment blocks to Qwen VL on Ollama and get back one standard JSON per block.

What is extracted per block
    equipment    every equipment label on the block (DT, RMU, LTP, CTPT, ...), with its type and rating if printed
    elements     walls, doors, louvres, columns and cutouts that bound the room, with their side (N/S/E/W)
    dimensions   every dimension text on the block, with the two elements it measures (from / to) and its value in mm
    distances    distances between wall and equipment, equipment and equipment, equipment and door, and so on
    clearances   clearances printed on the drawing (for example a minimum clearance in front of an RMU)

Pipeline
    1. Read the sheet JSON from extract_layouts.py: the block boxes, types and names.
    2. Crop each block from the full-resolution sheet, with a margin so dimensions on the edge are kept.
    3. Ask Qwen VL on Ollama for the content above. Ollama's structured output (a JSON schema) fixes the shape of the reply.
    4. Check the reply: IDs must exist, dimension numbers must match their text, values must be in range.
       Problems are listed under checks.issues. Nothing is dropped.
    5. Write one standard JSON per block, plus a JSON-lines file with all blocks and a summary CSV.

Setup
    ollama pull qwen3-vl:8b            # or qwen3-vl:32b for better reading (check the tag with `ollama list`)
    pip install ollama opencv-python-headless numpy

Usage
    python equipment_block_extract.py ^
        --json "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out\\bboxes\\json" ^
        --images "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out" ^
        --out "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out\\blocks_qwen" ^
        --model qwen3-vl:8b --limit 3

Notes
    - Only blocks whose type is in --types are sent (default: plan). --name-regex keeps only blocks whose name matches,
      for example "EQUIPMENT|CSS LAYOUT".
    - Results are cached by the crop image and the model name, so a rerun does not call the model again.
    - Qwen reads what is printed. It does not compute values. A value the model cannot read is null.
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
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

SCHEMA_VERSION = "css_block_extract/1.0"
PROMPT_VERSION = "block-extract-v1"
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

EQUIP_TYPES = ["transformer", "rmu", "ltp", "ctpt", "metering", "ht_switchgear", "lt_panel", "acdb", "dg_set",
               "battery", "apfc", "frtu", "other"]
ELEMENT_KINDS = ["wall", "door", "louvre", "window", "column", "cutout", "trench", "cable_tray", "other"]
SIDES = ["N", "S", "E", "W"]
ORIENT = ["horizontal", "vertical", "unknown"]
DIST_KINDS = ["wall_to_equipment", "equipment_to_equipment", "wall_to_wall", "equipment_to_door",
              "equipment_to_element", "other"]


# ---------------------------------------------------------------- JSON schema for Ollama structured output
def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


SCHEMA = {
    "type": "object",
    "properties": {
        "scale_text": {"type": ["string", "null"]},
        "equipment": {"type": "array", "items": _obj({
            "id": {"type": "string"},
            "label": {"type": ["string", "null"]},
            "equipment_type": {"type": "string", "enum": EQUIP_TYPES},
            "rating": {"type": ["string", "null"], "description": "as written, e.g. 2000 kVA 10 MT"},
            "size_text": {"type": ["string", "null"], "description": "as written, e.g. 2000 x 1500"},
        }, ["id", "label", "equipment_type"])},
        "elements": {"type": "array", "items": _obj({
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": ELEMENT_KINDS},
            "side": {"type": ["string", "null"], "enum": SIDES + [None]},
            "label": {"type": ["string", "null"]},
        }, ["id", "kind"])},
        "dimensions": {"type": "array", "items": _obj({
            "id": {"type": "string"},
            "text_as_written": {"type": "string"},
            "value_mm": {"type": ["number", "null"]},
            "orientation": {"type": "string", "enum": ORIENT},
            "from_id": {"type": ["string", "null"]},
            "to_id": {"type": ["string", "null"]},
            "legible": {"type": "boolean"},
        }, ["id", "text_as_written", "value_mm", "orientation", "legible"])},
        "distances": {"type": "array", "items": _obj({
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": DIST_KINDS},
            "from_id": {"type": "string"},
            "to_id": {"type": "string"},
            "value_mm": {"type": ["number", "null"]},
            "dimension_id": {"type": ["string", "null"]},
            "text_as_written": {"type": ["string", "null"]},
        }, ["id", "kind", "from_id", "to_id", "value_mm"])},
        "clearances": {"type": "array", "items": _obj({
            "id": {"type": "string"},
            "description": {"type": "string"},
            "from_id": {"type": ["string", "null"]},
            "to_id": {"type": ["string", "null"]},
            "value_mm": {"type": ["number", "null"]},
            "text_as_written": {"type": ["string", "null"]},
            "is_minimum": {"type": ["boolean", "null"], "description": "true when printed as a minimum or required clearance"},
        }, ["id", "description", "value_mm"])},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scale_text", "equipment", "elements", "dimensions", "distances", "clearances", "notes"],
    "additionalProperties": False,
}

SYSTEM = (
    "You read AEML consumer-substation (CSS) equipment layout drawings for Adani Electricity Mumbai. "
    "You extract only what is printed on the drawing. Never calculate or guess a value: when a value is not printed or "
    "not legible, use null and say so in notes. Dimensions are in millimetres unless the drawing says metres; convert metres to mm. "
    "Return JSON only, matching the schema."
)

USER_PROMPT = """This image is one equipment block cut from a substation drawing. Block name as written: "{name}".

Extract the following.
1. equipment: every equipment label inside the room (DT, RMU, LTP, CTPT, metering, panels, DG, batteries, ...). Give the label as written and the type. Add the rating or size only if printed next to it.
2. elements: the walls around the room (give side N, S, E or W: top of the drawing is N), doors, louvres, windows, columns, cutouts and trenches. Use the element id you give it in the other lists.
3. dimensions: every dimension string on the block. For each, give the text exactly as written, the value in mm, its orientation, and the two elements its extension lines or arrows connect (from_id and to_id). Use the element ids from elements or equipment. If a dimension does not clearly end on two elements, leave from_id and to_id null.
4. distances: the gap between two objects. Include wall-to-equipment, equipment-to-equipment, equipment-to-door and wall-to-wall. Link each distance to the dimension that shows it (dimension_id). Use kind wall_to_equipment, equipment_to_equipment, wall_to_wall, equipment_to_door, equipment_to_element or other.
5. clearances: any printed clearance or working space, for example "1000 MIN" in front of an RMU or a passage width. Set is_minimum true when the drawing gives it as a minimum or required value.
6. scale_text: the printed scale, or null.
7. notes: anything you could not read or that is ambiguous, one short sentence each.

Ids: use E1, E2 for equipment; W1, W2 ... for walls; O1 ... for doors, louvres and other openings; D1 ... for dimensions; S1 ... for distances; C1 ... for clearances.
Use numbers in mm only. If a value is not printed, use null."""


# ---------------------------------------------------------------- image helpers
def load_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"cannot read {path}")
    return bgr[:, :, ::-1].copy()


def crop_block(img: np.ndarray, box1000: list[float], margin: float) -> np.ndarray:
    """Crop [x0,y0,x1,y1] (0..1000) with a margin of `margin` (fraction of the sheet)."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = [v / 1000 for v in box1000]
    x0, y0 = max(0.0, x0 - margin), max(0.0, y0 - margin)
    x1, y1 = min(1.0, x1 + margin), min(1.0, y1 + margin)
    return img[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)].copy()


def jpeg(img_rgb: np.ndarray, max_side: int, quality: int = 92) -> bytes:
    h, w = img_rgb.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        img_rgb = cv2.resize(img_rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_rgb[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("jpeg encode failed")
    return buf.tobytes()


# ---------------------------------------------------------------- Ollama call
def parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(text[i:j + 1])


class QwenOllama:
    def __init__(self, model: str, host: str | None, cache_dir: Path, num_ctx: int = 8192, timeout: float = 600.0):
        try:
            import ollama
        except ImportError:
            sys.exit("pip install ollama")
        self.client = ollama.Client(host=host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434", timeout=timeout)
        self.model = model
        self.num_ctx = num_ctx
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.think_supported = True
        try:
            names = [m.model for m in self.client.list().models]
            if model not in names:
                print(f"warning: model {model!r} not found in `ollama list`; run `ollama pull {model}`")
        except Exception as e:                       # the server may be down; the call below will say so
            print(f"warning: cannot list Ollama models ({e})")

    def ask(self, key: str, user: str, jpg: bytes, retries: int = 2) -> dict:
        p = self.cache / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user, "images": [jpg]}]
        options = {"temperature": 0, "num_ctx": self.num_ctx}
        last = None
        for attempt in range(retries + 1):
            try:
                kwargs = dict(model=self.model, messages=messages, format=SCHEMA, options=options)
                if self.think_supported:
                    kwargs["think"] = False           # no reasoning text in the reply; needs ollama python >= 0.5
                try:
                    r = self.client.chat(**kwargs)
                except TypeError:
                    self.think_supported = False
                    kwargs.pop("think", None)
                    r = self.client.chat(**kwargs)
                text = r["message"]["content"]
                out = parse_json(text)
                res = {"output": out, "model": self.model,
                       "eval_count": r.get("eval_count"), "prompt_eval_count": r.get("prompt_eval_count")}
                p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                return res
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Ollama failed for {key}: {last}")


# ---------------------------------------------------------------- checks (flag, do not drop)
def run_checks(d: dict) -> list[str]:
    issues = []
    ids = {}
    for group in ("equipment", "elements", "dimensions", "distances", "clearances"):
        seen = set()
        for it in d.get(group, []):
            i = it.get("id")
            if i in seen:
                issues.append(f"duplicate id {i} in {group}")
            seen.add(i)
            ids.setdefault(i, group)
    dims = {x["id"]: x for x in d.get("dimensions", [])}

    for x in d.get("dimensions", []):
        for key in ("from_id", "to_id"):
            ref = x.get(key)
            if ref and ref not in ids:
                issues.append(f"dimension {x['id']} refers to unknown {key} {ref}")
        v = x.get("value_mm")
        if v is not None and not (0 < v <= 100000):
            issues.append(f"dimension {x['id']} value {v} mm is out of range")
        digits = re.sub(r"[^0-9.]", "", x.get("text_as_written") or "")
        if v is not None and digits and digits.count(".") <= 1:
            try:
                if abs(float(digits) - float(v)) > 1:
                    issues.append(f"dimension {x['id']}: text '{x.get('text_as_written')}' and value {v} disagree")
            except ValueError:
                pass

    for x in d.get("distances", []):
        for key in ("from_id", "to_id"):
            if x.get(key) not in ids:
                issues.append(f"distance {x['id']} refers to unknown {key} {x.get(key)}")
        dim = x.get("dimension_id")
        if dim and dim not in dims:
            issues.append(f"distance {x['id']} refers to unknown dimension {dim}")
        if dim and dim in dims and x.get("value_mm") is not None and dims[dim].get("value_mm") is not None:
            if abs(x["value_mm"] - dims[dim]["value_mm"]) > 1:
                issues.append(f"distance {x['id']} ({x['value_mm']} mm) differs from its dimension {dim} ({dims[dim]['value_mm']} mm)")
        if x.get("value_mm") is not None and not (0 < x["value_mm"] <= 100000):
            issues.append(f"distance {x['id']} value out of range")

    for x in d.get("clearances", []):
        for key in ("from_id", "to_id"):
            if x.get(key) and x[key] not in ids:
                issues.append(f"clearance {x['id']} refers to unknown {key} {x[key]}")

    labels = [str(e.get("label") or "").strip().upper() for e in d.get("equipment", []) if e.get("label")]
    dup = sorted({l for l in labels if labels.count(l) > 1})
    if dup:
        issues.append(f"duplicate equipment labels: {dup}")
    if not d.get("equipment"):
        issues.append("no equipment read")
    return issues


# ---------------------------------------------------------------- per-block pipeline
def process_block(sheet: dict, idx: int, layout: dict, img: np.ndarray, qwen: QwenOllama, out: Path,
                  margin: float, max_side: int) -> dict:
    crop = crop_block(img, layout["bbox"], margin)
    jpg = jpeg(crop, max_side)
    key = f"{hashlib.sha256(jpg).hexdigest()[:20]}_{qwen.model.replace(':', '_')}_{PROMPT_VERSION}"
    name = layout.get("name") or layout.get("type") or "block"
    res = qwen.ask(key, USER_PROMPT.format(name=name), jpg)
    body = res["output"]
    issues = run_checks(body)
    doc = {
        "schema": SCHEMA_VERSION,
        "source": {
            "sheet_file": sheet["file"], "block_index": idx, "block_id": layout.get("id"),
            "block_name": layout.get("name"), "block_type": layout.get("type"),
            "block_bbox_0_1000": layout["bbox"], "crop_margin": margin,
            "model": qwen.model, "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "scale_text": body.get("scale_text"),
        "equipment": body.get("equipment", []),
        "elements": body.get("elements", []),
        "dimensions": body.get("dimensions", []),
        "distances": body.get("distances", []),
        "clearances": body.get("clearances", []),
        "notes": body.get("notes", []),
        "checks": {"issues": issues},
        "usage": {"prompt_tokens": res.get("prompt_eval_count"), "output_tokens": res.get("eval_count")},
    }
    (out / "json").mkdir(parents=True, exist_ok=True)
    (out / "json" / f"{Path(sheet['file']).stem}__B{idx}.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="folder of sheet JSON files (extract_layouts.py output)")
    ap.add_argument("--images", required=True, help="folder with the sheet images")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--model", default="qwen3-vl:8b", help="Ollama tag, e.g. qwen3-vl:8b or qwen3-vl:32b")
    ap.add_argument("--host", default=None, help="Ollama server (default: $OLLAMA_HOST or localhost:11434)")
    ap.add_argument("--types", default="plan", help="comma list of block types to send")
    ap.add_argument("--name-regex", default=None, help="only blocks whose name matches this regex (case-insensitive)")
    ap.add_argument("--margin", type=float, default=0.02, help="crop margin as a fraction of the sheet")
    ap.add_argument("--max-side", type=int, default=2000, help="longest side of the crop sent to the model")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--limit", type=int, default=0, help="only the first N blocks (for a test run)")
    a = ap.parse_args()

    jdir, idir, out = Path(a.json), Path(a.images), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    types = {t.strip().lower() for t in a.types.split(",") if t.strip()}
    rx = re.compile(a.name_regex, re.I) if a.name_regex else None

    qwen = QwenOllama(a.model, a.host, out / "cache", num_ctx=a.num_ctx)
    jobs = []
    for jp in sorted(jdir.glob("*.json")):
        sheet = json.loads(jp.read_text(encoding="utf-8"))
        for i, L in enumerate(sheet.get("layouts", [])):
            if not L.get("bbox") or (L.get("type") or "").lower() not in types:
                continue
            if rx and not rx.search(L.get("name") or ""):
                continue
            jobs.append((sheet, i, L))
    if a.limit:
        jobs = jobs[: a.limit]
    print(f"{len(jobs)} blocks to read")

    rows, all_docs = [], []
    images = {}
    for n, (sheet, i, L) in enumerate(jobs, start=1):
        try:
            if sheet["file"] not in images:
                images = {sheet["file"]: load_rgb(idir / sheet["file"])}
            doc = process_block(sheet, i, L, images[sheet["file"]], qwen, out, a.margin, a.max_side)
            all_docs.append(doc)
            iss = doc["checks"]["issues"]
            print(f"[{n}/{len(jobs)}] {sheet['file'][:40]} B{i} '{L.get('name')}': "
                  f"{len(doc['equipment'])} equipment, {len(doc['dimensions'])} dims, "
                  f"{len(doc['distances'])} distances, {len(doc['clearances'])} clearances, {len(iss)} issues")
            rows.append({"sheet": sheet["file"], "block": f"B{i}", "name": L.get("name"),
                         "equipment": len(doc["equipment"]), "dimensions": len(doc["dimensions"]),
                         "distances": len(doc["distances"]), "clearances": len(doc["clearances"]),
                         "issues": " ; ".join(iss)})
        except Exception as e:
            print(f"[{n}/{len(jobs)}] FAILED {sheet.get('file')} B{i}: {e}")
            traceback.print_exc()

    with open(out / "all_blocks.jsonl", "w", encoding="utf-8") as fh:
        for d in all_docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["sheet", "block", "name", "equipment", "dimensions", "distances", "clearances", "issues"])
        w.writeheader()
        w.writerows(rows)
    print(f"done: {len(all_docs)} blocks -> {out}")


if __name__ == "__main__":
    main()
