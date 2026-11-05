"""Design rules: seed (from the xlsx + approach note) plus the user's edits, and matching of equipment to its rule.

The seed lives in config/rules_seed.json (built by scripts/build_rules.py). The first edit copies it to var/rules.json;
from then on that file is the truth, so re-seeding never overwrites edits. A rule with an `enforce` key and a numeric
`value` drives the matching setting (e.g. rules.plot_clearance_mm.dp_road), applied when the rules change."""
from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

SIDES = ("front", "back", "left", "right")
_lock = threading.Lock()


def _num(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


class RuleStore:
    def __init__(self, seed: Path, user: Path):
        self.seed_path, self.user_path = Path(seed), Path(user)

    # ---- io
    def _seed(self) -> dict:
        try:
            return json.loads(self.seed_path.read_text(encoding="utf-8"))
        except Exception:
            return {"sections": [], "rules": [], "references": []}

    def load(self) -> dict:
        if self.user_path.exists():
            try:
                d = json.loads(self.user_path.read_text(encoding="utf-8"))
                d["edited"] = True
                return d
            except Exception:
                pass
        d = self._seed()
        d["edited"] = False
        return d

    def _save(self, d: dict) -> None:
        d = {k: v for k, v in d.items() if k != "edited"}
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.user_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.user_path)

    # ---- edits
    FIELDS = ("section", "group", "title", "text", "value", "unit", "values", "applies_to", "source", "reference", "ref_status", "rule_no",
              "enforce", "match")

    def add(self, rule: dict) -> dict:
        with _lock:
            d = self.load()
            if not str(rule.get("title") or "").strip():
                raise ValueError("a rule needs a title")
            if rule.get("section") not in {s["id"] for s in d["sections"]}:
                raise ValueError("unknown section")
            r = {k: rule.get(k) for k in self.FIELDS}
            r.update({"id": f"user.{uuid.uuid4().hex[:8]}", "origin": "user", "applies_to": rule.get("applies_to") or "CSS+DSS"})
            d["rules"].append(r)
            self._save(d)
            return r

    def update(self, rid: str, patch: dict) -> dict:
        with _lock:
            d = self.load()
            for r in d["rules"]:
                if r["id"] == rid:
                    for k in self.FIELDS:
                        if k in patch:
                            r[k] = patch[k]
                    if r.get("origin") == "seed":
                        r["origin"] = "edited"
                    self._save(d)
                    return r
        raise KeyError(rid)

    def delete(self, rid: str) -> None:
        with _lock:
            d = self.load()
            n = len(d["rules"])
            d["rules"] = [r for r in d["rules"] if r["id"] != rid]
            if len(d["rules"]) == n:
                raise KeyError(rid)
            self._save(d)

    def reset(self) -> None:
        with _lock:
            if self.user_path.exists():
                self.user_path.unlink()

    # ---- effect on settings
    def apply(self, settings) -> int:
        """Push numeric values of rules that carry an `enforce` key into the settings."""
        n = 0
        for r in self.load()["rules"]:
            if r.get("enforce") and isinstance(r.get("value"), (int, float)):
                settings.set(r["enforce"], r["value"])
                n += 1
        return n


# ---------------------------------------------------------------- matching equipment to a clearance rule
def sides(rule: dict) -> dict[str, float]:
    """Operating clearance per side in mm ('min 200 / max 750' -> 200)."""
    return {k: _num((rule.get("values") or {}).get(k)) for k in SIDES if _num((rule.get("values") or {}).get(k)) is not None}


def _ways(s: str) -> int | None:
    m = re.search(r"(\d)\s*-?\s*way", s.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d)\s*\+\s*(\d)\b", s)
    return int(m.group(1)) + int(m.group(2)) - 0 if m else None


def match_equipment(rules: list[dict], e: dict, who: str = "CSS") -> dict | None:
    """Best clearance rule for an extracted equipment item, or None. Heuristic: type keywords, then kVA / way-count."""
    cand = [r for r in rules if r["section"] == "clearance" and r.get("applies_to") in (who, "CSS+DSS")]
    if not cand:
        return None
    t = (e.get("equipment_type") or "").lower()
    txt = f'{e.get("label") or ""} {e.get("subtype") or ""}'.lower()

    def of(*words):
        return [r for r in cand if any(w in f'{(r.get("match") or {}).get("type") or ""} {(r.get("match") or {}).get("part") or ""} '
                                       f'{(r.get("match") or {}).get("name") or ""}'.lower() for w in words)]
    pool: list[dict] = []
    if "rmu" in t or "rmu" in txt:
        pool = of("rmu")
        pool = [r for r in pool if "meter" not in (r["match"].get("type") or "").lower()] or pool if "meter" not in txt else of("metering")
        w = _ways(txt)
        if w:
            sel = [r for r in pool if f"{w}way" in r["title"].lower().replace(" ", "")]
            pool = sel or pool
    elif "transformer" in t or "dt" in t or "kva" in txt:
        pool = of("distribution transformers", "oil dt", "dry dt")
        pool = [r for r in pool if "dry" not in (r["match"].get("type") or "").lower()] or pool
        k = e.get("kva") or _num(re.search(r"(\d{3,4})\s*k?va", txt).group(1)) if re.search(r"(\d{3,4})\s*k?va", txt) else e.get("kva")
        if k:
            pool = sorted(pool, key=lambda r: abs((_num(r["title"]) or 1e9) - k))
    elif "capacitor" in t or "cap" in t:
        pool = of("capacitor")
    elif "ltp" in t or "lt" in t or "panel" in t:
        pool = of("lt panel", "ltpcc") or of("panel")
    elif "ht" in t or "switchgear" in t or "icog" in t:
        m = re.search(r"\b(\d{1,2})\s*-?\s*w(?:ay)?\b", txt)
        pool = [r for r in of("lt panel") if m and r["title"].lower().startswith(f"{m.group(1)}w")] if m else []
        pool = pool or [r for r in of("icog") if (r["match"].get("part") or "").lower() != "substation"]
        pool = pool or [r for r in of("11 kv swgr", "swgr") if (r["match"].get("part") or "").lower() != "substation"]
    elif "meter" in t:
        pool = of("metering")
    elif "aux" in t or "battery" in t or "scada" in t:
        pool = of("aux", "battery", "scada", "distribution board")
    return pool[0] if pool else None


def required_gap(ra: dict | None, rb: dict | None) -> float | None:
    """Indicative minimum gap between two items: the larger of the two items' smallest side clearance."""
    vals = [min(sides(r).values()) for r in (ra, rb) if r and sides(r)]
    return max(vals) if vals else None
