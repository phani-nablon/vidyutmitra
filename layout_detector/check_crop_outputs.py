"""Check the JSON files written by qwen_crop_extract.py.

    python check_crop_outputs.py --out "$DATA/css/pdf/out/crop_text" --crops "$DATA/css/pdf/out/blocks_crops"

For every crop it reports:
    - whether a JSON file exists and parses
    - whether all the expected fields are present
    - counts of blocks, dimensions, clearances and texts, and the substation dimensions
    - the consistency issues the extractor found
It also lists crops with no JSON file, so a failed run is easy to spot.
Exit code 0 when every crop has a valid JSON, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED = ["schema", "source", "scale_text", "substation_dimensions", "blocks", "dimensions",
            "clearances", "texts", "notes", "checks"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="folder the extractor wrote to")
    ap.add_argument("--crops", default=None, help="folder of crop images (to find crops with no JSON)")
    a = ap.parse_args()

    out = Path(a.out)
    jsons = sorted(out.glob("*.json"))
    problems = 0
    print(f"{'crop':45s} {'blk':>4s} {'dim':>4s} {'clr':>4s} {'txt':>4s} {'overall L x W x H (mm)':>26s} {'issues':>7s}")
    for jp in jsons:
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems += 1
            print(f"{jp.name:45s} INVALID JSON: {e}")
            continue
        missing = [k for k in REQUIRED if k not in d]
        if missing:
            problems += 1
            print(f"{jp.name:45s} MISSING FIELDS: {missing}")
            continue
        s = d.get("substation_dimensions") or {}
        overall = f"{s.get('overall_length_mm')} x {s.get('overall_width_mm')} x {s.get('height_mm')}"
        n_issues = len(d["checks"].get("issues", []))
        print(f"{jp.name[:45]:45s} {len(d['blocks']):4d} {len(d['dimensions']):4d} {len(d['clearances']):4d} "
              f"{len(d['texts']):4d} {overall:>26s} {n_issues:7d}")
        for issue in d["checks"].get("issues", []):
            print(f"      - {issue}")

    if a.crops:
        crops = {p.stem for p in Path(a.crops).iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT}
        done = {jp.stem for jp in jsons}
        missing = sorted(crops - done)
        if missing:
            problems += len(missing)
            print(f"\n{len(missing)} crop(s) with no JSON file:")
            for m in missing:
                print(f"  {m}")

    total = len(jsons)
    print(f"\n{total} JSON file(s) checked, {problems} problem(s)")
    sys.exit(0 if problems == 0 and total > 0 else 1)


if __name__ == "__main__":
    main()
