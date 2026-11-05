"""Learn what each class looks like from the legends of already-parsed drawings.

    python scripts/learn_palette.py <folder with v1 parse jsons> [config/learned_palette.json]

For every legend entry that has a semantic class and a measured swatch colour, colours are grouped per class
(CIELAB distance <= 12). Each group keeps its mean RGB, the number of drawings that use it and its share of the
drawings that have that class in their legend. Segmentation uses these only where a drawing's own legend does not say."""
import colorsys
import glob
import json
import sys
from collections import Counter, defaultdict

import numpy as np

SKIP = {None, "other", ""}


def lab(rgb):
    import cv2
    return cv2.cvtColor(np.array([[[int(round(c * 255)) for c in rgb]]], np.uint8), cv2.COLOR_RGB2LAB).astype(float)[0, 0]


def sat(rgb):
    return colorsys.rgb_to_hsv(*rgb)[1]


def main(src, out="config/learned_palette.json", tol=12.0, min_sat=0.25):
    per_doc = defaultdict(lambda: defaultdict(list))                       # class -> doc -> [rgb]
    for f in sorted(glob.glob(f"{src}/*/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for p in d.get("pages") or []:
            for g in p.get("legend") or []:
                sem, rgb = g.get("semantic"), g.get("color_rgb")
                if sem in SKIP or not rgb or sat(rgb) < min_sat:          # greys / black cannot be segmented by colour
                    continue
                per_doc[sem][f].append(rgb)
    res = {}
    for cls, docs in per_doc.items():
        groups = []                                                       # [lab centroid, [rgb], set(docs)]
        for doc, rgbs in docs.items():
            for rgb in rgbs:
                L = lab(rgb)
                for g in groups:
                    if np.linalg.norm(g[0] - L) <= tol:
                        g[1].append(rgb); g[2].add(doc); break
                else:
                    groups.append([L, [rgb], {doc}])
        rows = []
        for L, rgbs, ds in sorted(groups, key=lambda g: -len(g[2])):
            m = np.mean(rgbs, 0)
            rows.append({"rgb": [int(round(c * 255)) for c in m], "docs": len(ds), "share": round(len(ds) / len(docs), 2)})
        res[cls] = {"drawings_with_legend": len(docs), "colours": rows}
    json.dump(res, open(out, "w"), indent=1)
    for k, v in res.items():
        print(k, v["drawings_with_legend"], [(c["rgb"], c["share"]) for c in v["colours"][:4]])
    return res


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3] or []))
