"""Shared scoring library used by both engines (KG and RAG).

- facet_score     rarity-weighted (IDF) agreement on metadata facets; kVA matches nearest rating
- geometry_eval   ALS + compatibility checks for one historic substation against the query geometry
- feature_score   presence-rule similarity on the numeric/categorical feature vector (RAG)
- verdict         reuse / adapt / new from the final score and the conflicts
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from ..geometry import compat
from ..geometry.als import RoomGeom, als, room_geom_from_substation
from .query import facets as query_facets


@dataclass
class Result:
    engine: str
    doc_id: str
    substation_id: str
    file_name: str
    substation_name: str
    score: float
    verdict: str
    coverage: float
    components: dict[str, float] = field(default_factory=dict)
    geometry: dict[str, Any] | None = None
    checks: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)
    status: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["score"] = round(self.score, 4)
        d["coverage"] = round(self.coverage, 3)
        d["components"] = {k: round(v, 3) for k, v in self.components.items()}
        return d


def idf(df: int, n: int) -> float:
    return math.log(1 + max(n, 1) / max(df, 1))


def kva_sim(a: float, b: float) -> float:
    return math.exp(-abs(math.log(a / b)) / 0.1) if a and b else 0.0


def facet_score(q: dict, cand: dict, deg: dict[str, int], n: int) -> tuple[float | None, list[str]]:
    """q: query facets + kva list; cand: candidate facets {key: value|[values]}; deg: facet vid -> df."""
    qf = query_facets(q)
    num = den = 0.0
    hits = []
    for k, v in qf.items():
        vid = f"f:{k}={v}"
        w = idf(deg.get(vid, 0), n)
        den += w
        cv = cand.get(k)
        cvals = cv if isinstance(cv, list) else [cv]
        if any(str(x) == str(v) for x in cvals if x is not None):
            num += w
            hits.append(f"{k}={v}")
    for kv in q.get("kva") or []:
        vid = f"f:kva={int(kv)}"
        w = idf(deg.get(vid, 0), n)
        den += w
        best = max([kva_sim(kv, c) for c in cand.get("kva") or []] or [0.0])
        if best > 0.05:
            num += w * best
            hits.append(f"kva={kv}" + ("" if best > 0.99 else " (near)"))
    return (num / den if den else None), hits


def feature_score(q: dict, f: dict) -> tuple[float | None, dict[str, float]]:
    """Presence-rule similarity on the RAG feature vector."""
    parts: dict[str, float] = {}
    for k in ("installation", "arrangement", "context", "floor", "kind"):
        if q.get(k):
            parts[k] = 1.0 if f.get(k) == q[k] else 0.0
    if q.get("multiple_substations"):
        parts["multiple"] = 1.0 if f.get("multiple_substations") else 0.0
    if q.get("substation_count"):
        a, b = int(q["substation_count"]), int(f.get("substation_count") or 1)
        parts["substation_count"] = 2 * min(a, b) / (a + b)
    if q.get("platform") is not None:
        parts["platform"] = 1.0 if bool(f.get("platform")) == bool(q["platform"]) else 0.0
    for k, fk, tol in (("area_m2", "area_m2", 0.25), ("length_mm", "length_mm", 0.2), ("width_mm", "width_mm", 0.2),
                       ("clear_height_mm", "clear_height_mm", 0.12)):
        if q.get(k):
            parts[k] = math.exp(-abs(math.log(q[k] / f[fk])) / tol) if f.get(fk) else 0.0
    for t, n in (q.get("counts") or {}).items():
        m = (f.get("counts") or {}).get("packaged" if t == "packaged" else t, 0)
        parts[f"n_{t}"] = math.exp(-1.2 * abs(n - m)) if m else 0.0
    if q.get("kva"):
        cand = f.get("kva") or []
        parts["kva"] = sum(max([kva_sim(k, c) for c in cand] or [0]) for k in q["kva"]) / len(q["kva"])
    for k, v in (q.get("civil") or {}).items():
        if k == "door_count":
            parts[k] = math.exp(-0.7 * abs(v - (f.get("n_doors") or 0)))
        elif k in f and v is True:
            parts[k] = 1.0 if f.get(k) else 0.0
    for k in ("access", "trench"):
        if (q.get("site") or {}).get(k):
            parts[f"site_{k}"] = 1.0 if f.get(k) else 0.0
    if not parts:
        return None, parts
    # equipment counts and ratings decide reuse far more than generic facets
    w = {k: (2.5 if k.startswith("n_") or k == "kva" else 1.0) for k in parts}
    return sum(w[k] * v for k, v in parts.items()) / sum(w.values()), parts


def geometry_eval(query: dict, qg: RoomGeom, sub: dict, doc: dict, settings) -> dict:
    """ALS (+ height/site extras from the compatibility checks) for one historic substation."""
    rg = room_geom_from_substation(sub)
    rules = settings.get("rules")
    sc = settings.get("scoring")
    from ..geometry.als import align
    pose = align(qg, rg, sc.get("align_refine_deg", 10))
    checks, extra = compat.check_all(query, qg, rg, pose, sub, doc, rules)
    res = als(qg, rg, sc["als_weights"], sc.get("match_sigma_mm", 600), sc.get("area_log_tol", 0.3),
              sc.get("iou_dilate_mm", 150), sc.get("align_refine_deg", 10), extra=extra, pose=pose)
    return {"als": res, "checks": checks, "rg": rg}


def _norm(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(v)


def hard_facet_factor(q: dict, cand: dict, factors: dict) -> tuple[float, list[str]]:
    """Multiplicative penalty when the query states a gating facet (installation, ...) the candidate contradicts."""
    f, notes = 1.0, []
    for k, mult in (factors or {}).items():
        qv = q.get(k)
        if qv in (None, ""):
            continue
        cv = cand.get(k)
        cvals = [_norm(x) for x in (cv if isinstance(cv, list) else [cv]) if x is not None]
        if cvals and _norm(qv) not in cvals:
            f *= float(mult)
            notes.append(f"{k} differs ({cvals[0]} vs {qv}) x{mult}")
    return f, notes


def verdict(score: float, conflicts: list[str], cfg: dict) -> str:
    if score >= cfg.get("reuse", 0.8) and not conflicts:
        return "reuse"
    if score >= cfg.get("adapt", 0.6):
        return "adapt"
    return "new"


def conflicts_of(checks: list[dict]) -> list[str]:
    return [c["detail"] for c in checks if c["status"] == "fail"]
