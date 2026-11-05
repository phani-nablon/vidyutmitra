"""Clearance table of one substation: drawn dimension, measured gap and the operating-clearance rule side by side."""
from __future__ import annotations

import re

from ..core.rules import match_equipment, required_gap, sides

_WALL = {"north": "N", "south": "S", "east": "E", "west": "W", "n": "N", "s": "S", "e": "E", "w": "W"}


def _key(x) -> str:
    t = re.sub(r"\s+", "", str(x or "").lower())
    m = re.match(r"wall_?(north|south|east|west|n|s|e|w)$", t)
    return f"wall{_WALL[m.group(1)]}" if m else re.sub(r"[^a-z0-9+]", "", t)


def clearance_table(sub: dict, rules: list[dict]) -> list[dict]:
    who = "DSS" if "dss" in str(sub.get("name") or "").lower() else "CSS"
    eq = (sub.get("electrical") or {}).get("equipment") or []
    by_key = {}
    for e in eq:
        for k in (e.get("label"), e.get("id")):
            if k:
                by_key[_key(k)] = e
    rule_of = {id(e): match_equipment(rules, e, who) for e in eq}
    rows: dict[tuple, dict] = {}
    for c in sub.get("clearances") or []:
        a, b = _key(c.get("a")), _key(c.get("b"))
        if not a or not b or c.get("mm") is None:
            continue
        k = tuple(sorted((a, b)))
        r = rows.setdefault(k, {"a": c.get("a"), "b": c.get("b"), "drawn_mm": None, "measured_mm": None, "relation": c.get("relation"),
                                "p": None, "q": None, "drawn_source": None})
        if c.get("source") == "measured":
            r.update(measured_mm=c["mm"], relation=c.get("relation") or r["relation"], p=c.get("p"), q=c.get("q"),
                     a=c.get("a"), b=c.get("b"))
        elif r["drawn_mm"] is None:
            r["drawn_mm"], r["drawn_source"] = c["mm"], c.get("source")
    out = []
    for k, r in rows.items():
        ea, eb = by_key.get(_key(r["a"])), by_key.get(_key(r["b"]))
        ra, rb = (rule_of.get(id(ea)) if ea else None), (rule_of.get(id(eb)) if eb else None)
        kind = "wall" if (k[0].startswith("wall") or k[1].startswith("wall")) else "equipment" if (ea and eb) else "other"
        need = required_gap(ra, rb) if kind != "other" else None
        have = r["measured_mm"] if r["measured_mm"] is not None else r["drawn_mm"]
        status = "no rule" if need is None else ("pass" if have is not None and have >= need else "fail")
        out.append({**r, "kind": kind, "required_mm": need, "status": status,
                    "margin_mm": (round(have - need, 1) if (need is not None and have is not None) else None),
                    "rule_a": ra and {"id": ra["id"], "title": ra["title"], "sides": sides(ra)},
                    "rule_b": rb and {"id": rb["id"], "title": rb["title"], "sides": sides(rb)},
                    "a_id": ea and ea.get("id"), "b_id": eb and eb.get("id")})
    order = {"fail": 0, "pass": 1, "no rule": 2}
    return sorted(out, key=lambda r: (order[r["status"]], r["measured_mm"] if r["measured_mm"] is not None else (r["drawn_mm"] or 1e9)))
