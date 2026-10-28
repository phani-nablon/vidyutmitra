"""Benchmark query generation from the indexed drawings.

For every drawing, five natural-language requirements are written, one per query type:
  equipment   counts per type (+ kVA / dry-oil when known), paraphrased (DT / transformer / trafo, LTP / LT panel ...)
  metadata    installation, floor, arrangement, building context, single / multiple substations
  area        room size (rounded, +-3 % noise), area, clear height, shape, platform
  site        geography: Mumbai locality when the drawing names one, building type / occupancy, site features
              (24x7 access from DP road, cable trench / HDPE entry, wayleave, open plot)
  mixed       a subset of all of the above; half of them also carry the room outline as a site polygon
              (rotated / mirrored / noised) so the geometry path (ALS) is exercised
Each query stores a machine-readable `spec` (the constraints it states) used by the judge; specs are made from
the drawing's own fields with tolerances, never from the engines' scores. Generation is seeded and deterministic.
Project names and file names are never put into queries (that would make text search trivially exact).
"""
from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path

from ..core.ontology import base_equipment_type

from ..core.places import BUILDING_TYPES, LOCALITIES, doc_building, doc_text  # noqa: F401
EQ_WORDS = {
    "transformer": [("DT", "DTs"), ("transformer", "transformers"), ("distribution transformer", "distribution transformers"), ("DTR", "DTRs")],
    "rmu": [("RMU", "RMUs"), ("ring main unit", "ring main units")],
    "ltp": [("LTP", "LTPs"), ("LT panel", "LT panels"), ("feeder pillar", "feeder pillars")],
    "metering": [("metering cubicle", "metering cubicles"), ("CTPT metering unit", "CTPT metering units")],
    "packaged": [("packaged substation", "packaged substations"), ("unitised CSS", "unitised CSS")],
    "ht_switchgear": [("HT panel", "HT panels"), ("VCB HT switchgear", "VCB HT switchgear")],
}
NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
FLOOR_WORDS = {"ground": ["ground floor", "ground level"], "stilt": ["stilt floor", "stilt level"], "basement": ["basement"],
               "podium": ["podium level"], "first": ["first floor"], "second": ["second floor"], "terrace": ["terrace"]}


def localities(doc: dict) -> list[str]:
    from ..core.places import localities as _loc
    return _loc(doc_text(doc))


def building_type(doc: dict) -> str | None:
    return doc_building(doc)


def _main_sub(doc: dict) -> dict | None:
    subs = doc.get("substations") or []
    return max(subs, key=lambda s: len(s["electrical"]["equipment"])) if subs else None


def _counts(sub: dict) -> dict[str, int]:
    c: dict[str, int] = {}
    for e in sub["electrical"]["equipment"]:
        b = base_equipment_type(e.get("ontology_class") or e.get("equipment_type") or "aux")
        if b != "aux":
            c[b] = c.get(b, 0) + 1
    return c


def _eq_phrase(rng, counts: dict, kvas: list[int], dt_kind: str | None) -> str:
    parts = []
    order = ["transformer", "packaged", "rmu", "metering", "ht_switchgear", "ltp"]
    for t in order:
        n = counts.get(t)
        if not n:
            continue
        sing, plur = rng.choice(EQ_WORDS[t])
        word = sing if n == 1 else plur
        style = rng.random()
        num = (NUM.get(n, str(n)) if style < 0.35 else f"{n} x" if style < 0.6 else f"{n} nos" if style < 0.75 else str(n))
        extra = ""
        if t == "transformer" and kvas and rng.random() < 0.8:
            k = sorted(set(kvas))
            extra = f"{k[0]} kVA " if len(k) == 1 else ""
            if dt_kind and rng.random() < 0.6:
                extra += f"{dt_kind} "
        if num.endswith("x"):
            parts.append(f"{num} {extra}{word}".replace("  ", " "))
        else:
            parts.append(f"{num} {extra}{word}".replace("  ", " "))
    if not parts:
        return ""
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]


def _round_m(mm: float, rng) -> float:
    v = mm * (1 + rng.uniform(-0.03, 0.03)) / 1000
    return round(v, 1) if rng.random() < 0.7 else round(v * 2) / 2


def generate(docs: list[dict], per_doc: int = 5, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for doc in sorted(docs, key=lambda d: d["doc_id"]):
        sub = _main_sub(doc)
        md = doc.get("metadata") or {}
        if sub is None:
            continue
        civ = sub["civil"]
        counts = _counts(sub)
        kvas = [e["kva"] for e in sub["electrical"]["equipment"] if e.get("kva") and base_equipment_type(e.get("ontology_class") or "") == "transformer"]
        dt_kind = None
        st = " ".join(str(e.get("subtype") or "") + " " + str(e.get("note") or "") for e in sub["electrical"]["equipment"]).lower()
        if "dry" in st:
            dt_kind = "dry"
        elif "oil" in st:
            dt_kind = "oil"
        feats = {f.get("cls") for f in (doc.get("site") or {}).get("features", [])}
        locs = localities(doc)
        btype = building_type(doc)
        floor = sub.get("floor") or md.get("floor")
        qs = []

        # 1. equipment
        if counts:
            spec = {"counts": counts}
            if kvas and len(set(kvas)) == 1:
                spec["kva"] = sorted(set(kvas))[0]
            phr = _eq_phrase(rng, counts, kvas if "kva" in spec else [], dt_kind)
            lead = rng.choice(["CSS with", "Need a substation layout with", "Find a design having", "Consumer substation:", "Looking for a CSS with"])
            qs.append(("equipment", f"{lead} {phr}", spec))
        # 2. metadata
        spec, words = {}, []
        if md.get("installation"):
            spec["installation"] = md["installation"]
            words.append({"indoor": rng.choice(["indoor", "inside the building", "indoor room"]),
                          "open_to_sky": rng.choice(["open to sky", "outdoor open-to-sky"]),
                          "semi_open": "semi-open shed"}.get(md["installation"], md["installation"]))
        if floor and floor in FLOOR_WORDS:
            spec["floor"] = floor
            words.append("on the " + rng.choice(FLOOR_WORDS[floor]))
        if md.get("arrangement"):
            spec["arrangement"] = md["arrangement"]
            words.append({"centralized": rng.choice(["centralised", "single-room centralized"]),
                          "distributed": rng.choice(["distributed over separate rooms", "distributed"])}[md["arrangement"]])
        if md.get("multiple_substations"):
            spec["multiple"] = True
            words.append(f"{NUM.get(md.get('substation_count') or 2, 'multiple')} separate substations")
        if btype and rng.random() < 0.7:
            spec["building"] = btype
            words.append(f"in a {btype}")
        if words:
            qs.append(("metadata", f"{rng.choice(['A', 'An existing', 'Show a'])} CSS " + ", ".join(words), spec))
        # 3. area / geometry
        L, B = civ.get("length_mm"), civ.get("width_mm")
        spec, words = {}, []
        if L and B:
            Lm, Bm = _round_m(max(L, B), rng), _round_m(min(L, B), rng)
            spec["dims"] = [max(L, B), min(L, B)]
            words.append(rng.choice([f"room {Lm} x {Bm} m", f"substation area of {Lm} m by {Bm} m", f"{Lm}m x {Bm}m room"]))
        elif civ.get("area_m2"):
            spec["area"] = civ["area_m2"]
            words.append(f"about {round(civ['area_m2'])} sq m")
        if civ.get("clear_height_mm") and rng.random() < 0.8:
            spec["height"] = civ["clear_height_mm"]
            words.append(f"clear height {round(civ['clear_height_mm'] / 1000, 2)} m")
        if civ.get("shape") in ("L_shape", "chamfered") and rng.random() < 0.7:
            spec["shape"] = civ["shape"]
            words.append({"L_shape": "L-shaped", "chamfered": "with a chamfered corner"}[civ["shape"]])
        if civ.get("plinth_mm") and rng.random() < 0.5:
            spec["platform"] = True
            words.append("raised platform")
        if words:
            qs.append(("area", "CSS " + ", ".join(words), spec))
        # 4. site / geography
        spec, words = {}, []
        if locs:
            spec["locality"] = locs[0]
            words.append(f"in {locs[0].upper() if locs[0] == 'bkc' else locs[0].title()}")
        if btype and "building" not in spec:
            spec["building"] = btype
            words.append(f"for a {btype}")
        if "access_path" in feats or "wayleave" in feats:
            spec["access"] = True
            words.append(rng.choice(["24x7 access from the DP road", "vehicle access path / wayleave from DP road"]))
        if "trench" in feats or any(r for r in sub["cable"].get("routes", [])):
            spec["trench"] = True
            words.append(rng.choice(["cable trench to the road", "HDPE pipe cable entry trench"]))
        if md.get("installation") == "open_to_sky":
            spec["installation"] = "open_to_sky"
            words.append("open plot")
        if words:
            qs.append(("site", "Substation " + ", ".join(words), spec))
        # 5. mixed (+ outline for half)
        spec, words = {}, []
        structured = None
        if counts:
            spec["counts"] = counts
            words.append(_eq_phrase(rng, counts, kvas if (kvas and len(set(kvas)) == 1 and rng.random() < 0.5) else [], None))
            if kvas and len(set(kvas)) == 1 and str(kvas[0]) in words[-1]:
                spec["kva"] = kvas[0]
        if md.get("installation"):
            spec["installation"] = md["installation"]
            words.append(md["installation"].replace("_", " "))
        if floor in FLOOR_WORDS and rng.random() < 0.6:
            spec["floor"] = floor
            words.append(rng.choice(FLOOR_WORDS[floor]))
        poly = civ.get("polygon_mm")
        if poly and len(poly) >= 3 and rng.random() < 0.5:
            spec["outline"] = True
            spec["dims"] = [max(L or 0, B or 0), min(L or 0, B or 0)] if L and B else None
            structured = {"site_polygon_mm": _perturb(poly, rng)}
        elif L and B:
            spec["dims"] = [max(L, B), min(L, B)]
            words.append(f"{_round_m(max(L, B), rng)} x {_round_m(min(L, B), rng)} m")
        if civ.get("clear_height_mm") and rng.random() < 0.5:
            spec["height"] = civ["clear_height_mm"]
            words.append(f"height {round(civ['clear_height_mm'] / 1000, 1)} m")
        if words or structured:
            qs.append(("mixed", "CSS: " + "; ".join(w for w in words if w), spec))
            if structured:
                qs[-1] = qs[-1] + (structured,)
        # drawings without a room size or site marks get extra mixed queries so every drawing has `per_doc`
        extra_n = 0
        while len(qs) < per_doc and counts and extra_n < 3:
            extra_n += 1
            spec = {"counts": counts}
            words = [_eq_phrase(rng, counts, [], None)]
            if md.get("installation"):
                spec["installation"] = md["installation"]
                words.append(md["installation"].replace("_", " "))
            if btype and extra_n == 1:
                spec["building"] = btype
                words.append(f"for a {btype}")
            elif floor in FLOOR_WORDS:
                spec["floor"] = floor
                words.append(rng.choice(FLOOR_WORDS[floor]))
            qs.append(("mixed", "Substation with " + ", ".join(words), spec))
        for i, item in enumerate(qs[:per_doc]):
            qtype, text, spec = item[:3]
            out.append({"qid": f"{doc['doc_id']}-{i}", "type": qtype, "text": re.sub(r"\s+", " ", text).strip(),
                        "structured": item[3] if len(item) > 3 else None, "spec": {k: v for k, v in spec.items() if v is not None},
                        "source_doc": doc["doc_id"], "source_file": doc["source"]["file_name"]})
    return out


def _perturb(poly, rng) -> list[list[float]]:
    """Rotate by k*90 deg (+-3 deg), maybe mirror, scale +-3 %, translate: the outline a builder plan would give."""
    k = rng.randint(0, 3)
    ang = math.radians(90 * k + rng.uniform(-3, 3))
    mir = rng.random() < 0.5
    s = 1 + rng.uniform(-0.03, 0.03)
    dx, dy = rng.uniform(-5000, 5000), rng.uniform(-5000, 5000)
    out = []
    for x, y in poly:
        x = -x if mir else x
        out.append([round((x * math.cos(ang) - y * math.sin(ang)) * s + dx, 1), round((x * math.sin(ang) + y * math.cos(ang)) * s + dy, 1)])
    return out


def save(queries: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
