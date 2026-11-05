"""Extract all printed text from equipment block crops with Qwen VL on Ollama. Same role as the Astra passes, but local.

For every crop image in a folder it returns one standard JSON with:
    texts                  every legible text string on the crop, with its kind (label, dimension, clearance, note, ...)
    blocks                 equipment blocks and room parts drawn on the crop (DT, RMU, LTP, CTPT, panel, door, ...)
    dimensions             every dimension string, its value in mm, and the labels it spans (from_text, to_text)
    clearances             printed clearances, working spaces and passages, with a flag for minimum values
    substation_dimensions  overall room length, width and height, when printed
    scale_text, notes

Only printed text is read. Qwen does not calculate values, and anything it cannot read is null.

Optional tiling (--tiles 2)
    A second pass runs on a 2 x 2 grid of tiles and reads only dimensions and clearances. Small dimension text is
    easier to read at a larger scale. Tile results are merged with the full-crop results, and duplicates are removed.

Setup
    ollama pull qwen3-vl:8b            # or qwen3-vl:32b for better reading
    pip install ollama pydantic opencv-python-headless numpy

Usage
    python qwen_crop_extract.py ^
        --crops "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out\\blocks_crops" ^
        --out "C:\\Users\\ibpha\\work\\projects\\substation\\data\\all_data\\css\\pdf\\out\\crop_text" ^
        --model qwen3-vl:8b --tiles 2

Outputs (under --out)
    <crop>.json             one standard JSON per crop image, named after the image (e.g. DT_LAYOUT_01.json)
    all_crops.jsonl         every crop on one line each
    summary.csv             counts and the substation dimensions per crop, with the number of issues
    cache/                  raw model replies, so a rerun does not call the model again for finished crops
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

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError:
    sys.exit("pip install pydantic")

SCHEMA_VERSION = "crop_text_extract/1.0"
PROMPT_VERSION = "crop-text-v1"
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

TEXT_KINDS = ["label", "dimension", "clearance", "note", "title", "scale", "other"]
BLOCK_TYPES = ["equipment", "transformer", "rmu", "ltp", "ctpt", "metering", "ht_switchgear", "lt_panel", "acdb",
               "dg_set", "battery", "apfc", "door", "louvre", "cable_riser", "room", "other"]
ORIENT = ["horizontal", "vertical", "unknown"]


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


DIM_ITEM = _obj({
    "text_as_written": {"type": "string"},
    "value_mm": {"type": ["number", "null"]},
    "orientation": {"type": "string", "enum": ORIENT},
    "from_text": {"type": ["string", "null"], "description": "label or wall the dimension starts at, as written"},
    "to_text": {"type": ["string", "null"], "description": "label or wall the dimension ends at, as written"},
    "legible": {"type": "boolean"},
}, ["text_as_written", "value_mm", "orientation", "legible"])

CLEAR_ITEM = _obj({
    "text_as_written": {"type": ["string", "null"]},
    "description": {"type": "string"},
    "value_mm": {"type": ["number", "null"]},
    "is_minimum": {"type": ["boolean", "null"]},
}, ["description", "value_mm"])

FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "texts": {"type": "array", "items": _obj({
            "text": {"type": "string"}, "kind": {"type": "string", "enum": TEXT_KINDS}}, ["text", "kind"])},
        "blocks": {"type": "array", "items": _obj({
            "label": {"type": ["string", "null"]},
            "block_type": {"type": "string", "enum": BLOCK_TYPES},
            "size_text": {"type": ["string", "null"]},
        }, ["label", "block_type"])},
        "dimensions": {"type": "array", "items": DIM_ITEM},
        "clearances": {"type": "array", "items": CLEAR_ITEM},
        "substation_dimensions": _obj({
            "overall_length_mm": {"type": ["number", "null"]},
            "overall_width_mm": {"type": ["number", "null"]},
            "height_mm": {"type": ["number", "null"]},
            "text_as_written": {"type": "array", "items": {"type": "string"}},
        }, ["overall_length_mm", "overall_width_mm", "height_mm", "text_as_written"]),
        "scale_text": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["texts", "blocks", "dimensions", "clearances", "substation_dimensions", "scale_text", "notes"],
    "additionalProperties": False,
}

TILE_SCHEMA = {
    "type": "object",
    "properties": {
        "dimensions": {"type": "array", "items": DIM_ITEM},
        "clearances": {"type": "array", "items": CLEAR_ITEM},
    },
    "required": ["dimensions", "clearances"],
    "additionalProperties": False,
}

SYSTEM = (
    "You read AEML consumer-substation drawings for Adani Electricity Mumbai. You extract only text that is printed. "
    "Never calculate or guess a value. Dimensions are millimetres unless the drawing says metres; convert metres to mm. "
    "Use null when a value is not printed or not legible. Return JSON only, matching the schema."
)

FULL_PROMPT = """This image is a crop of one equipment block from a substation drawing. Read everything printed on it.

1. texts: every legible text string, copied as written, with its kind (label, dimension, clearance, note, title, scale, other).
2. blocks: each equipment block or room part (DT, RMU, LTP, CTPT, metering, panels, DG set, batteries, doors, louvres, cable riser). label as written, block_type, and size_text only if printed next to it.
3. dimensions: every dimension string. For each give the text as written, the value in mm, its orientation, the label or wall it starts from (from_text) and the one it ends at (to_text), and whether it is legible.
4. clearances: printed clearances, working spaces and passages, for example "1000 MIN" or "PASSAGE 1200". Set is_minimum to true when it is printed as a minimum or required value.
5. substation_dimensions: the overall room length, width and height, when printed. These are usually the outermost dimension strings. Put every such string in text_as_written.
6. scale_text: the printed scale, or null.
7. notes: anything unreadable or ambiguous, one short sentence each.

Use null for any value that is not printed. Do not add up dimensions to get a total."""

TILE_PROMPT = """This image is one part of a substation drawing crop, enlarged for reading. List only the dimension strings and clearances
you can see in this part, with the same fields as before (text as written, value in mm, orientation, from_text, to_text, legible).
Do not repeat anything you are not sure about. Use null for any value that is not printed."""


# ---------------------------------------------------------------- images
def load_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"cannot read {path}")
    return bgr[:, :, ::-1].copy()


def jpeg(img_rgb: np.ndarray, max_side: int, quality: int = 92) -> bytes:
    h, w = img_rgb.shape[:2]
    s = max_side / max(h, w)
    if s < 1:
        img_rgb = cv2.resize(img_rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_rgb[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("jpeg encode failed")
    return buf.tobytes()


def tiles(img: np.ndarray, n: int, overlap: float = 0.08):
    """n x n tiles with a small overlap, so a dimension on a tile edge is seen whole on one of them."""
    H, W = img.shape[:2]
    for r in range(n):
        for c in range(n):
            y0 = max(0, int((r / n - overlap) * H))
            y1 = min(H, int(((r + 1) / n + overlap) * H))
            x0 = max(0, int((c / n - overlap) * W))
            x1 = min(W, int(((c + 1) / n + overlap) * W))
            yield f"r{r}c{c}", img[y0:y1, x0:x1]


# ---------------------------------------------------------------- Ollama
def parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object in reply")
    return json.loads(text[i:j + 1])


# ---------------------------------------------------------------- Pydantic models: every reply must pass these
class Dimension(BaseModel):
    text_as_written: str
    value_mm: float | None = None
    orientation: str = "unknown"
    from_text: str | None = None
    to_text: str | None = None
    legible: bool = True


class Clearance(BaseModel):
    description: str
    text_as_written: str | None = None
    value_mm: float | None = None
    is_minimum: bool | None = None


class TextItem(BaseModel):
    text: str
    kind: str = "other"


class BlockItem(BaseModel):
    label: str | None = None
    block_type: str = "other"
    size_text: str | None = None


class SubstationDims(BaseModel):
    overall_length_mm: float | None = None
    overall_width_mm: float | None = None
    height_mm: float | None = None
    text_as_written: list[str] = Field(default_factory=list)


class CropResult(BaseModel):
    texts: list[TextItem] = Field(default_factory=list)
    blocks: list[BlockItem] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    clearances: list[Clearance] = Field(default_factory=list)
    substation_dimensions: SubstationDims = Field(default_factory=SubstationDims)
    scale_text: str | None = None
    notes: list[str] = Field(default_factory=list)


class TileResult(BaseModel):
    dimensions: list[Dimension] = Field(default_factory=list)
    clearances: list[Clearance] = Field(default_factory=list)


for _model in (Dimension, Clearance, TextItem, BlockItem, SubstationDims, CropResult, TileResult):
    _model.model_rebuild()                          # resolve the nested types explicitly


class Qwen:
    """Ollama's native client. The JSON schema goes in as `format`, so the model is constrained to it.
    Each reply is then validated with Pydantic before it is cached."""

    def __init__(self, model: str, host: str | None, cache: Path, num_ctx: int = 8192, timeout: float = 900.0,
                 num_predict: int = 6000):
        try:
            import ollama
        except ImportError:
            sys.exit("pip install ollama")
        self.api_base = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.client = ollama.Client(host=self.api_base, timeout=timeout)
        self.model, self.num_ctx, self.num_predict = model, num_ctx, num_predict
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)
        self.think_ok = True
        try:                                        # catch a wrong tag before the first crop
            names = [m.model for m in self.client.list().models]
            if model not in names:
                print(f"warning: {model!r} not on {self.api_base}; run `ollama pull {model}`")
        except Exception as e:
            print(f"warning: cannot reach Ollama at {self.api_base} ({e})")

    def ask(self, key: str, user: str, jpg: bytes, schema: dict, model_cls, retries: int = 2) -> dict:
        p = self.cache / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user, "images": [jpg]}]
        last = None
        for attempt in range(retries + 1):
            try:
                # first attempts: the schema is enforced by Ollama. last attempt: plain JSON mode, then Pydantic still checks it
                kw = dict(model=self.model, messages=messages,
                          format=schema if attempt < retries else "json",
                          options={"temperature": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict})
                if self.think_ok:
                    kw["think"] = False
                try:
                    r = self.client.chat(**kw)
                except TypeError:                   # older ollama python: no `think` argument
                    self.think_ok = False
                    kw.pop("think", None)
                    r = self.client.chat(**kw)
                msg = r["message"]
                text = msg.get("content") or ""
                thinking = msg.get("thinking") or ""
                if not text.strip() and thinking.strip():
                    # the model answered in its reasoning field instead of the content field: read the JSON from there
                    print(f"  note: {key[:40]} answer came back in the 'thinking' field; reading it from there")
                    text = thinking
                if not text.strip():
                    dump = self.cache / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.raw.json"
                    dump.write_text(json.dumps(r, default=str, indent=1), encoding="utf-8")
                    raise ValueError(f"empty reply (eval_count={r.get('eval_count')}, done_reason={r.get('done_reason')}); "
                                     f"raw response saved to {dump.name}. Check the model tag, and that think=False is honoured")
                try:
                    raw = parse_json(text)
                except ValueError:
                    raise ValueError(f"reply is not JSON (done_reason={r.get('done_reason')}): {text[:300]!r}")
                try:
                    obj = model_cls.model_validate(raw)
                except ValidationError as e:
                    raise ValueError(f"reply failed Pydantic validation: {str(e)[:400]}")
                res = {"output": obj.model_dump(), "model": self.model,
                       "prompt_tokens": r.get("prompt_eval_count"), "output_tokens": r.get("eval_count")}
                p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                return res
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Qwen failed for {key}: {last}")


# ---------------------------------------------------------------- merge and checks
def _norm(s) -> str:
    return re.sub(r"[\s\-_]", "", str(s or "")).upper()


def merge_dims(full: list[dict], extra: list[dict]) -> tuple[list[dict], int]:
    """Same text (and orientation) = same dimension. The full-crop reading is kept; a tile reading fills in a missing value.
    A tile reading with a different non-null value is kept as a separate dimension, so a conflict is visible."""
    merged: list[dict] = [dict(d) for d in full]
    added = 0
    for d in extra:
        key = _norm(d.get("text_as_written"))
        match = None
        for m in merged:
            if _norm(m.get("text_as_written")) == key and m.get("orientation") == d.get("orientation"):
                if m.get("value_mm") is None or d.get("value_mm") is None or abs(m["value_mm"] - d["value_mm"]) < 1:
                    match = m
                    break
        if match is None:
            merged.append(dict(d))
            added += 1
        elif match.get("value_mm") is None and d.get("value_mm") is not None:
            match["value_mm"] = d["value_mm"]
    return merged, added


def merge_clears(full: list[dict], extra: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in full + extra:
        k = (_norm(c.get("text_as_written") or c.get("description")), c.get("value_mm"))
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def run_checks(d: dict) -> list[str]:
    issues = []
    dims = d.get("dimensions", [])
    for x in dims:
        v = x.get("value_mm")
        if v is not None and not (0 < v <= 100000):
            issues.append(f"dimension '{x.get('text_as_written')}' = {v} mm is out of range")
        digits = re.sub(r"[^0-9.]", "", x.get("text_as_written") or "")
        if v is not None and digits and digits.count(".") <= 1:
            try:
                if abs(float(digits) - float(v)) > 1:
                    issues.append(f"'{x.get('text_as_written')}' and value {v} mm disagree")
            except ValueError:
                pass
    sub = d.get("substation_dimensions") or {}
    L = sub.get("overall_length_mm")
    W = sub.get("overall_width_mm")
    horiz = [x["value_mm"] for x in dims if x.get("orientation") == "horizontal" and x.get("value_mm")]
    vert = [x["value_mm"] for x in dims if x.get("orientation") == "vertical" and x.get("value_mm")]
    if L and horiz and max(horiz) > L * 1.02:
        issues.append(f"a horizontal dimension ({max(horiz):.0f} mm) is longer than the overall length ({L:.0f} mm)")
    if W and vert and max(vert) > W * 1.02:
        issues.append(f"a vertical dimension ({max(vert):.0f} mm) is longer than the overall width ({W:.0f} mm)")
    labels = [_norm(b.get("label")) for b in d.get("blocks", []) if b.get("label")]
    dup = sorted({l for l in labels if labels.count(l) > 1})
    if dup:
        issues.append(f"duplicate block labels: {dup}")
    if not dims:
        issues.append("no dimensions read")
    return issues


# ---------------------------------------------------------------- per crop
def process(path: Path, qwen: Qwen, out: Path, max_side: int, n_tiles: int, tile_side: int) -> dict:
    img = load_rgb(path)
    full_res = qwen.ask(f"{path.stem}_{hashlib.sha256(jpeg(img, max_side)).hexdigest()[:12]}_full_{PROMPT_VERSION}",
                        FULL_PROMPT, jpeg(img, max_side), FULL_SCHEMA, CropResult)
    body = full_res["output"]
    dims, clears = body.get("dimensions", []), body.get("clearances", [])
    tile_added, tokens = 0, [full_res]
    if n_tiles > 1:
        extra_d, extra_c = [], []
        for tid, t in tiles(img, n_tiles):
            r = qwen.ask(f"{path.stem}_{tid}_{tile_side}_{PROMPT_VERSION}", TILE_PROMPT, jpeg(t, tile_side), TILE_SCHEMA, TileResult)
            tokens.append(r)
            extra_d += r["output"].get("dimensions", [])
            extra_c += r["output"].get("clearances", [])
        dims, added = merge_dims(dims, extra_d)
        tile_added = added
        clears = merge_clears(clears, extra_c)
    body["dimensions"], body["clearances"] = dims, clears
    issues = run_checks(body)
    doc = {
        "schema": SCHEMA_VERSION,
        "source": {"crop_file": path.name, "model": qwen.model, "prompt_version": PROMPT_VERSION,
                   "tiles": n_tiles, "tile_dimensions_added": tile_added,
                   "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        "scale_text": body.get("scale_text"),
        "substation_dimensions": body.get("substation_dimensions"),
        "blocks": body.get("blocks", []),
        "dimensions": dims,
        "clearances": clears,
        "texts": body.get("texts", []),
        "notes": body.get("notes", []),
        "checks": {"issues": issues},
        "usage": [{"prompt_tokens": r.get("prompt_tokens"), "output_tokens": r.get("output_tokens")} for r in tokens],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{path.stem}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crops", required=True, help="folder of equipment block crop images")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--model", default="qwen3-vl:8b", help="Ollama tag, e.g. qwen3-vl:8b or qwen3-vl:32b")
    ap.add_argument("--host", default=None, help="Ollama server (default: $OLLAMA_HOST or localhost:11434)")
    ap.add_argument("--max-side", type=int, default=2400, help="longest side of the full crop sent to the model")
    ap.add_argument("--tiles", type=int, default=1, help="1 = full crop only; 2 = also read a 2 x 2 tile grid")
    ap.add_argument("--tile-side", type=int, default=1600, help="longest side of each tile sent to the model")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--limit", type=int, default=0, help="only the first N crops (test run)")
    a = ap.parse_args()

    src, out = Path(a.crops), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT)
    if a.limit:
        files = files[: a.limit]
    if not files:
        sys.exit(f"no images in {src}")
    qwen = Qwen(a.model, a.host, out / "cache", num_ctx=a.num_ctx)
    print(f"{len(files)} crops, model {a.model}, tiles {a.tiles}")

    docs, rows = [], []
    for n, f in enumerate(files, start=1):
        try:
            d = process(f, qwen, out, a.max_side, a.tiles, a.tile_side)
            docs.append(d)
            s = d.get("substation_dimensions") or {}
            print(f"[{n}/{len(files)}] {f.name[:50]}: {len(d['blocks'])} blocks, {len(d['dimensions'])} dims, "
                  f"{len(d['clearances'])} clearances, overall {s.get('overall_length_mm')} x {s.get('overall_width_mm')}, "
                  f"{len(d['checks']['issues'])} issues")
            rows.append({"crop": f.name, "blocks": len(d["blocks"]), "dimensions": len(d["dimensions"]),
                         "clearances": len(d["clearances"]), "overall_length_mm": s.get("overall_length_mm"),
                         "overall_width_mm": s.get("overall_width_mm"), "height_mm": s.get("height_mm"),
                         "issues": len(d["checks"]["issues"]), "issue_text": " ; ".join(d["checks"]["issues"])})
        except Exception as e:
            print(f"[{n}/{len(files)}] FAILED {f.name}: {e}")
            traceback.print_exc()

    with open(out / "all_crops.jsonl", "w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["crop", "blocks", "dimensions", "clearances", "overall_length_mm",
                                           "overall_width_mm", "height_mm", "issues", "issue_text"])
        w.writeheader()
        w.writerows(rows)
    print(f"done: {len(docs)} crops -> {out}")


if __name__ == "__main__":
    main()
