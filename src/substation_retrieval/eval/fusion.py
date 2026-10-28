"""Offline hybrid strategies over saved KG and RAG runs (no engine calls), tuned on dev, reported on test.

  score fusion   s = a * KG + (1 - a) * RAG            (a tuned on dev)
  RRF            s = 1/(60 + rank_KG) + 1/(60 + rank_RAG)
  LTR rerank     linear model on both engines' component scores, trained with a pairwise logistic loss on
                 dev graded judgments (numpy, L2-regularised); weights saved for the live `hybrid` mode
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from .judge import mean, metrics

FEATURES = ["kg", "rag", "kg.nodes", "kg.edges", "kg.geom", "kg.facets", "rag.text", "rag.features", "rag.raster",
            "als.footprint", "als.size", "als.height", "als.equipment", "als.structure", "als.site", "conflicts",
            "kg.hard_facets", "kg_rank_rr", "rag_rank_rr", "agree_top5"]


def _index(run: list[dict]) -> dict[str, dict[str, dict]]:
    out = {}
    for r in run:
        out[r["qid"]] = {x["doc"]: {**x, "rank": i + 1} for i, x in enumerate(r["ranked"])}
    return out


def feature_rows(qid: str, kg: dict, rag: dict, docs: list[str]) -> tuple[list[str], np.ndarray]:
    K, R = kg.get(qid, {}), rag.get(qid, {})
    cand = sorted(set(K) | set(R)) or docs
    X = np.zeros((len(cand), len(FEATURES)))
    for i, d in enumerate(cand):
        k, r = K.get(d), R.get(d)
        f = dict.fromkeys(FEATURES, 0.0)
        if k:
            f["kg"] = k["score"]
            c = k["components"]
            for n in ("nodes", "edges", "geom", "facets"):
                f[f"kg.{n}"] = c.get(n, 0.0)
            f["kg.hard_facets"] = 1.0 - c.get("hard_facets", 1.0)
            f["kg_rank_rr"] = 1.0 / k["rank"]
        if r:
            f["rag"] = r["score"]
            c = r["components"]
            for n in ("text", "features", "raster"):
                f[f"rag.{n}"] = c.get(n, 0.0)
            f["rag_rank_rr"] = 1.0 / r["rank"]
        src = (k or r)["components"]
        for n in ("footprint", "size", "height", "equipment", "structure", "site"):
            f[f"als.{n}"] = src.get(f"als.{n}", 0.0)
        f["conflicts"] = float(min(3, max((k or {}).get("n_conflicts", 0), (r or {}).get("n_conflicts", 0))))
        f["agree_top5"] = float(bool(k and r and k["rank"] <= 5 and r["rank"] <= 5))
        X[i] = [f[n] for n in FEATURES]
    return cand, X


def rank_by(cand: list[str], s: np.ndarray) -> list[str]:
    order = sorted(range(len(cand)), key=lambda i: (-s[i], cand[i]))
    return [cand[i] for i in order]


def strategies(kg_run, rag_run):
    kg, rag = _index(kg_run), _index(rag_run)

    def fuse(a):
        def f(qid):
            cand, X = feature_rows(qid, kg, rag, [])
            return rank_by(cand, a * X[:, 0] + (1 - a) * X[:, 1])
        return f

    def rrf(qid):
        cand, X = feature_rows(qid, kg, rag, [])
        s = np.array([(1 / (60 + 1 / x[17]) if x[17] else 0) + (1 / (60 + 1 / x[18]) if x[18] else 0) for x in X])
        return rank_by(cand, s)
    return kg, rag, fuse, rrf


def train_ltr(qids, kg, rag, qrels, l2=0.01, epochs=300, lr=0.5, seed=0):
    rng = np.random.default_rng(seed)
    P, N = [], []
    for qid in qids:
        cand, X = feature_rows(qid, kg, rag, [])
        g = np.array([qrels[qid].get(d, 0) for d in cand])
        for i in range(len(cand)):
            for j in range(len(cand)):
                if g[i] > g[j]:
                    P.append(X[i])
                    N.append(X[j])
    P, N = np.array(P), np.array(N)
    D = P - N
    mu, sd = np.zeros(D.shape[1]), np.ones(D.shape[1])
    w = np.zeros(D.shape[1])
    for _ in range(epochs):
        idx = rng.choice(len(D), size=min(len(D), 4096), replace=False)
        z = D[idx] @ w
        grad = -(D[idx] * (1 / (1 + np.exp(z)))[:, None]).mean(0) + l2 * w
        w -= lr * grad
    return w


def evaluate_rankers(queries, qrels, rankers: dict, split: dict[str, str]) -> dict:
    qmap = {q["qid"]: q for q in queries}
    rep = {}
    for name, fn in rankers.items():
        rows = defaultdict(list)
        for qid, q in qmap.items():
            m = metrics(fn(qid), qrels[qid], q["source_doc"])
            for key in ("all", q["type"], split[qid]):
                rows[key].append(m)
        rep[name] = {k: mean(v) for k, v in rows.items()}
    return rep


def save_weights(w: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"features": FEATURES, "weights": [round(float(x), 5) for x in w]}, indent=1), encoding="utf-8")
