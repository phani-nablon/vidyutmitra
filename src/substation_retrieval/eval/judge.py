"""Relevance judgments and ranking metrics.

A drawing's grade for a query is computed from the query's `spec` (the constraints it states) against the
drawing's extracted fields, independently of either engine:
  2 = every stated constraint holds for at least one of its substations   (fully relevant)
  1 = at least 60 % of the constraints hold                               (partially relevant)
  0 = otherwise
Tolerances: room dims +-10 %, area +-15 %, clear height +-10 %; counts, kVA, facets exact.
Metrics: known-item Hit@1/5/10 and MRR of the drawing the query was written from; graded nDCG@10;
P@5 and R@10 of fully relevant drawings (R@10 is over min(10, |relevant|)).
"""
from __future__ import annotations

import math

from ..core.ontology import base_equipment_type
from .generate import building_type, localities


def _sub_checks(spec: dict, sub: dict, doc: dict) -> list[bool]:
    md = doc.get("metadata") or {}
    civ = sub["civil"]
    feats = {f.get("cls") for f in (doc.get("site") or {}).get("features", [])}
    out = []
    if "counts" in spec:
        c: dict[str, int] = {}
        for e in sub["electrical"]["equipment"]:
            b = base_equipment_type(e.get("ontology_class") or e.get("equipment_type") or "aux")
            c[b] = c.get(b, 0) + 1
        out += [c.get(t, 0) == n for t, n in spec["counts"].items()]
    if "kva" in spec:
        out.append(any(e.get("kva") == spec["kva"] for e in sub["electrical"]["equipment"]))
    for k in ("installation", "arrangement"):
        if k in spec:
            out.append(md.get(k) == spec[k])
    if "floor" in spec:
        out.append((sub.get("floor") or md.get("floor")) == spec["floor"])
    if "multiple" in spec:
        out.append(bool(md.get("multiple_substations")) == bool(spec["multiple"]))
    if "building" in spec:
        out.append(building_type(doc) == spec["building"])
    if spec.get("dims"):
        L, B = civ.get("length_mm"), civ.get("width_mm")
        if L and B:
            Lq, Bq = spec["dims"]
            out.append(abs(max(L, B) / Lq - 1) <= 0.10 and abs(min(L, B) / Bq - 1) <= 0.10)
        else:
            out.append(False)
    if "area" in spec:
        a = civ.get("area_m2")
        out.append(bool(a) and abs(a / spec["area"] - 1) <= 0.15)
    if "height" in spec:
        h = civ.get("clear_height_mm")
        out.append(bool(h) and abs(h / spec["height"] - 1) <= 0.10)
    if "shape" in spec:
        out.append(civ.get("shape") == spec["shape"])
    if "platform" in spec:
        out.append(bool(civ.get("plinth_mm")))
    if "locality" in spec:
        out.append(spec["locality"] in localities(doc))
    if "access" in spec:
        out.append(bool(feats & {"access_path", "wayleave"}))
    if "trench" in spec:
        out.append("trench" in feats or any(sub["cable"].get("routes", [])))
    return out


def grade(spec: dict, doc: dict) -> int:
    best = 0.0
    for sub in doc.get("substations") or []:
        chk = _sub_checks(spec, sub, doc)
        if not chk:
            continue
        best = max(best, sum(chk) / len(chk))
    return 2 if best >= 0.999 else 1 if best >= 0.6 else 0


def judgments(queries: list[dict], docs: dict[str, dict]) -> dict[str, dict[str, int]]:
    return {q["qid"]: {did: grade(q["spec"], d) for did, d in docs.items()} for q in queries}


def metrics(ranked: list[str], qrels: dict[str, int], source: str, k: int = 10) -> dict:
    pos = ranked.index(source) + 1 if source in ranked else None
    gains = [(2 ** qrels.get(d, 0) - 1) for d in ranked[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted((2 ** g - 1 for g in qrels.values()), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    rel = {d for d, g in qrels.items() if g == 2}
    return {
        "hit1": float(pos == 1), "hit5": float(bool(pos) and pos <= 5), "hit10": float(bool(pos) and pos <= 10),
        "mrr": 1.0 / pos if pos else 0.0,
        "ndcg10": dcg / idcg if idcg else 0.0,
        "p5": sum(1 for d in ranked[:5] if d in rel) / 5,
        "r10": (sum(1 for d in ranked[:k] if d in rel) / min(k, len(rel))) if rel else 0.0,
        "n_rel": len(rel),
    }


def mean(rows: list[dict], keys=("hit1", "hit5", "hit10", "mrr", "ndcg10", "p5", "r10")) -> dict:
    n = max(1, len(rows))
    return {k: round(sum(r[k] for r in rows) / n, 3) for k in keys} | {"n": len(rows)}
