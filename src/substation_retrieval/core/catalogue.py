"""Equipment catalogue and design rules (extracted from Equip_DimensionRules.xlsx -> catalog.json).

match_dimensions()  catalogue rows within tolerance of a drawn equipment's L x B (either orientation),
                    H and weight. Used to infer a transformer rating when the drawing does not state it:
                    the rating is filled only when exactly one rating survives (else left empty).
lookup()            the catalogue row a *query* item implies (type + kVA / ways / kind), used for fit checks
                    when the requirement names equipment without dimensions.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("catalog.json")

CLASS_TO_ROWS = {
    "transformer": lambda e: e["particular"] == "Distribution Transformers" and "2X" not in e["name"].upper(),
    "rmu": lambda e: e["type"] == "RMU",
    "metering": lambda e: e["type"] == "Metering RMU",
    "ltp": lambda e: e["particular"] == "LT Panel",
    "packaged": lambda e: e["type"] in ("Package", "Pad Mounted", "Customised"),
    "ht_switchgear": lambda e: "Swgr" in e["particular"] and e["type"] in ("Panel", "ICOG"),
    "aux": lambda e: e["particular"] in ("Aux Equipments", "Capacitor Bank"),
}


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def rules() -> dict:
    return load()["rules"]


def kva_of(name: str) -> int | None:
    m = re.search(r"(\d{3,4})\s*k?va", name or "", re.I)
    return int(m.group(1)) if m else None


def ways_of(name: str) -> int | None:
    m = re.search(r"(\d+)\s*-?\s*w(ay)?\b", name or "", re.I)
    return int(m.group(1)) if m else None


def parse_weight_kg(w) -> float | None:
    """'10 MT' -> 10000, '4880 kg' -> 4880, 2.5 -> 2500 (tonnes assumed below 100)."""
    if w is None:
        return None
    if isinstance(w, (int, float)):
        return float(w) * 1000 if w < 100 else float(w)
    s = str(w).lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mt|t|ton|tonne|kg)?", s)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2) or ("t" if v < 100 else "kg")
    return v * 1000 if unit in ("mt", "t", "ton", "tonne") else v


def _rel(a, b):
    return abs(a - b) / max(abs(b), 1e-9)


def match_dimensions(cls: str, L=None, B=None, H=None, weight_kg=None, tol=None, scope="CSS") -> list[dict]:
    """Catalogue rows of the class consistent with the measured dimensions (missing values are skipped)."""
    tol = tol or {"footprint": 0.05, "height": 0.08, "weight": 0.08}
    base = "transformer" if cls.startswith("transformer") else cls
    sel = CLASS_TO_ROWS.get(base)
    if sel is None:
        return []
    out = []
    for e in load()["equipment"]:
        if e["scope"] != scope or not sel(e):
            continue
        checks, ok = 0, True
        if L and B and e.get("L_mm") and e.get("B_mm"):
            lo, hi = sorted((L, B))
            elo, ehi = sorted((e["L_mm"], e["B_mm"]))
            ok &= _rel(lo, elo) <= tol["footprint"] and _rel(hi, ehi) <= tol["footprint"]
            checks += 1
        if H and e.get("H_mm"):
            ok &= _rel(H, e["H_mm"]) <= tol["height"]
            checks += 1
        if weight_kg and e.get("weight_kg"):
            ok &= _rel(weight_kg, e["weight_kg"]) <= tol["weight"]
            checks += 1
        if ok and checks:
            out.append({**e, "kva": kva_of(e["name"]), "checks": checks})
    return out


def infer_rating(cls: str, L=None, B=None, H=None, weight_kg=None, tol=None) -> dict:
    """{kva, unique, candidates[]}: kva only when all surviving rows agree on one rating."""
    rows = match_dimensions(cls, L, B, H, weight_kg, tol)
    ratings = sorted({r["kva"] for r in rows if r.get("kva")})
    return {"kva": ratings[0] if len(ratings) == 1 else None, "unique": len(ratings) == 1,
            "candidates": [r["name"] + " " + r["type"] for r in rows]}


def lookup(cls: str, kva=None, ways=None, dt_kind=None, subtype=None) -> dict | None:
    """Catalogue row implied by a requirement item (for fit checks when no dimensions are given)."""
    eq = [e for e in load()["equipment"] if e["scope"] == "CSS"]
    base = "transformer" if cls.startswith("transformer") else cls
    if base == "transformer":
        kinds = {"dry": "DRY DT", "oil": "OIL DT", "ester": "NATURAL ESTER DT"}
        if cls == "transformer_dry":
            dt_kind = dt_kind or "dry"
        pool = [e for e in eq if CLASS_TO_ROWS["transformer"](e)]
        if dt_kind in kinds:
            pool = [e for e in pool if e["type"] == kinds[dt_kind]] or pool
        if kva:
            ge = sorted([e for e in pool if (kva_of(e["name"]) or 0) >= kva], key=lambda e: kva_of(e["name"]) or 0)
            return (ge or sorted(pool, key=lambda e: -(kva_of(e["name"]) or 0)))[0] if pool else None
        return next((e for e in pool if kva_of(e["name"]) == 1000), pool[0] if pool else None)
    if base == "rmu":
        pool = [e for e in eq if e["type"] == "RMU"]
        if ways:
            pool = sorted(pool, key=lambda e: abs((ways_of(e["name"]) or 3) - ways))
        return pool[0] if pool else None
    if base == "ltp":
        pool = [e for e in eq if e["particular"] == "LT Panel"]
        if subtype and "icog" in subtype.lower():
            pool = [e for e in pool if e["type"] == "ICOG"] or pool
        elif subtype and "pcc" in subtype.lower():
            pool = [e for e in pool if e["type"] == "LTPCC panel"] or pool
        else:
            pool = [e for e in pool if e["type"] == "LT Panel"] or pool
            if ways:
                pool = sorted(pool, key=lambda e: abs((ways_of(e["name"]) or 10) - ways))
        return pool[0] if pool else None
    sel = CLASS_TO_ROWS.get(base)
    pool = [e for e in eq if sel and sel(e)]
    if kva:
        pool = sorted(pool, key=lambda e: abs((kva_of(e["name"]) or 0) - kva))
    return pool[0] if pool else None


def door_min_width(cls: str) -> float:
    r = rules()["doors"]
    if cls.startswith("transformer") or cls == "packaged":
        return float(r["transformer"][0])
    return float(r["other_equipment"][0])
