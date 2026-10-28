"""Hybrid retrieval: KG and RAG together, re-ranked by a learned linear model (default production mode).

Both engines score every candidate; a pairwise-trained linear reranker (config/reranker.json, re-trained by
`sr eval-run` on the dev half of the benchmark) orders the union using both engines' scores and components.
The displayed score stays on the engines' 0..1 scale (alpha * KG + (1 - alpha) * RAG) so verdict thresholds keep
their meaning; the reranker only decides the order. If one engine is off or down, the other's ranking is used.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..config import REPO_ROOT
from ..eval.fusion import FEATURES, feature_rows, rank_by

_W = None


def reranker_weights(settings) -> tuple[np.ndarray | None, float]:
    global _W
    if _W is None:
        p = settings.var_dir / "models" / "reranker.json"
        if not p.exists():
            p = REPO_ROOT / "config" / "reranker.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            w = dict(zip(d["features"], d["weights"]))
            _W = (np.array([w.get(f, 0.0) for f in FEATURES]), float(d.get("alpha", 0.35)))
        except Exception:
            _W = (None, 0.35)
    return _W


def _as_run(results: list[dict]) -> dict[str, dict]:
    return {r["doc_id"]: {"doc": r["doc_id"], "score": r["score"], "components": r["components"],
                          "n_conflicts": len(r["conflicts"]), "rank": i + 1} for i, r in enumerate(results)}


def hybrid_search(services, q: dict, k: int = 10) -> dict:
    orch = services.orchestrator
    flags = orch.flags()
    trace, kg_res, rag_res, info = [], [], [], {}
    if flags["kg_enabled"]:
        try:
            r = orch.search(q, max(k, 50), "kg")
            kg_res, info["kg"] = r["results"], r["info"].get("kg")
        except Exception as e:
            trace.append(f"KG unavailable: {e}")
    if flags["rag_enabled"]:
        try:
            r = orch.search(q, max(k, 50), "rag")
            rag_res, info["rag"] = r["results"], r["info"].get("rag")
        except Exception as e:
            trace.append(f"RAG unavailable: {e}")
    w, alpha = reranker_weights(services.settings)
    K, R = _as_run(kg_res), _as_run(rag_res)
    cand, X = feature_rows("q", {"q": K}, {"q": R}, [])
    if not cand:
        order = []
    elif w is not None and K and R:
        order = rank_by(cand, X @ w)
    else:
        order = rank_by(cand, alpha * X[:, 0] + (1 - alpha) * X[:, 1])
        trace.append("reranker weights missing or one engine absent: score fusion used")
    by_kg = {r["doc_id"]: r for r in kg_res}
    by_rag = {r["doc_id"]: r for r in rag_res}
    results = []
    for d in order[:k]:
        base = dict(by_kg.get(d) or by_rag.get(d))
        ks = by_kg[d]["score"] if d in by_kg else 0.0
        rs = by_rag[d]["score"] if d in by_rag else 0.0
        base["score"] = round(alpha * ks + (1 - alpha) * rs, 4) if (K and R) else max(ks, rs)
        base["engine"] = "hybrid"
        base["components"] = {**base["components"], "kg": round(ks, 3), "rag": round(rs, 3)}
        conf = base.get("conflicts") or []
        v = services.settings.get("scoring.verdict")
        base["verdict"] = "reuse" if base["score"] >= v["reuse"] and not conf else "adapt" if base["score"] >= v["adapt"] else "new"
        results.append(base)
    from .query import describe, present
    from ..geometry.compat import query_feasibility
    return {"engine": "hybrid", "mode": "hybrid", "flags": flags, "results": results, "info": info, "trace": trace,
            "present": present(q), "asked": describe(q), "query": {k_: v_ for k_, v_ in q.items() if not k_.startswith("_")},
            "feasibility": query_feasibility(q, q.get("area_m2"), services.settings.get("rules")), "elapsed_ms": None}
