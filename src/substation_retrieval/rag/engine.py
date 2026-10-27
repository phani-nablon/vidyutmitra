"""Regular RAG matching (fallback engine).

Stage 1 (shortlist, every indexed substation):
    text       BM25 over drawing text, labels and metadata
    features   presence-rule similarity on the feature vector (facets, counts, kVA, size, height, openings)
    raster     best-pose IoU of the canonical 128x128 mask raster (only when the query has an outline)
    embedding  cosine of an optional text embedding (OpenAI-compatible endpoint)
Stage 2 (rerank the shortlist): the same ALS + compatibility checks the KG engine uses.
    S = 0.5 * stage-1 score + 0.5 * ALS   (ALS only when the query carries geometry)
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter

import numpy as np

from ..geometry import raster as R
from ..retrieval.query import present, room_geom
from ..retrieval.scoring import Result, conflicts_of, feature_score, geometry_eval, hard_facet_factor, verdict

TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOP = set("the a an of and or to in on for with at by from is are be as this that it its into".split())


def tokens(s: str) -> list[str]:
    return [t for t in TOKEN.findall((s or "").lower()) if t not in STOP and len(t) > 1]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.toks = [tokens(d) for d in docs]
        self.N = len(docs)
        self.avg = sum(map(len, self.toks)) / max(self.N, 1)
        self.df = Counter(t for d in self.toks for t in set(d))
        self.tf = [Counter(d) for d in self.toks]
        self.k1, self.b = k1, b

    def scores(self, q: str) -> np.ndarray:
        qt = tokens(q)
        out = np.zeros(self.N)
        for t in set(qt):
            df = self.df.get(t)
            if not df:
                continue
            idf = math.log(1 + (self.N - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self.tf):
                f = tf.get(t)
                if f:
                    out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(self.toks[i]) / self.avg))
        return out


class RAGEngine:
    name = "rag"

    def __init__(self, index, settings, embedder=None):
        self.index = index
        self.settings = settings
        self.embedder = embedder
        self._version = -1
        self._subs: list[dict] = []
        self._bm25: BM25 | None = None
        self._rasters: list = []

    def _refresh(self):
        v = self.index.version()
        if v != self._version:
            self._subs = self.index.substations()
            self._bm25 = BM25([s["text"] or "" for s in self._subs])
            self._rasters = [R.unpack(s["raster"]) for s in self._subs]
            self._version = v

    def health(self) -> dict:
        self._refresh()
        return {"ok": bool(self._subs), "substations": len(self._subs), "embedding": self.embedder is not None}

    def search(self, q: dict, k: int = 10) -> tuple[list[Result], dict]:
        t0 = time.time()
        self._refresh()
        if not self._subs:
            return [], {"candidates": 0}
        w = self.settings.get("engines.rag.weights")
        pres = present(q)
        qg = room_geom(q)
        n = len(self._subs)
        comps: dict[str, np.ndarray] = {}
        text = q.get("text") or ""
        if text.strip():
            bm = self._bm25.scores(text)
            comps["text"] = bm / bm.max() if bm.max() > 0 else bm
        fs = [feature_score(q, s["features"]) for s in self._subs]
        if any(f[0] is not None for f in fs):
            comps["features"] = np.array([f[0] or 0.0 for f in fs])
        if qg.outline is not None:
            qr, _ = R.rasterise(qg)
            comps["raster"] = np.array([R.raster_similarity(qr, r) for r in self._rasters])
        if self.embedder is not None and text.strip():
            try:
                qe = np.asarray(self.embedder([text])[0], np.float32)
                qe /= np.linalg.norm(qe) + 1e-9
                comps["embedding"] = np.array([float(np.dot(qe, s["embedding"] / (np.linalg.norm(s["embedding"]) + 1e-9)))
                                               if s["embedding"] is not None else 0.0 for s in self._subs])
            except Exception:
                pass
        if not comps:
            return [], {"candidates": 0, "note": "query has no usable details"}
        used_w = sum(w.get(c, 0) for c in comps)
        stage1 = sum(w.get(c, 0) * v for c, v in comps.items()) / used_w
        order = np.argsort(-stage1)[: int(self.settings.get("engines.rag.shortlist", 40))]
        geo = pres["footprint"] or pres["height"] or pres["structure"] or pres["cutouts"] or pres["site"]
        best: dict[str, Result] = {}
        for i in order:
            s = self._subs[int(i)]
            doc = self.index.load_doc(s["doc_id"])
            if doc is None:
                continue
            sub = next((x for x in doc["substations"] if f'{doc["doc_id"]}:{x["id"]}' == s["sid"]), None)
            if sub is None:
                continue
            ge = geometry_eval(q, qg, sub, doc, self.settings) if (geo or q.get("equipment_items")) else None
            als_score = ge["als"].score if ge else None
            score = float(stage1[i]) if als_score is None or not geo else 0.5 * float(stage1[i]) + 0.5 * als_score
            hard, hard_notes = hard_facet_factor(q, s["features"], self.settings.get("scoring.hard_facets"))
            score *= hard
            checks = ge["checks"] if ge else []
            conflicts = conflicts_of(checks)
            c = {kk: float(v[i]) for kk, v in comps.items()}
            if ge:
                c.update({f"als.{kk}": vv for kk, vv in ge["als"].components.items()})
            f_parts = fs[int(i)][1]
            expl = [f"{kk}: {vv:.2f}" for kk, vv in sorted(f_parts.items(), key=lambda t: -t[1])[:8]] + hard_notes
            cov = used_w / sum(w.values())
            if ge and geo:
                cov = 0.5 * cov + 0.5 * ge["als"].coverage
            r = Result(engine="rag", doc_id=s["doc_id"], substation_id=s["sid"], file_name=doc["source"]["file_name"],
                       substation_name=sub.get("name") or "", score=score, verdict=verdict(score, conflicts, self.settings.get("scoring.verdict")),
                       coverage=cov, components=c, geometry=ge["als"].as_dict() if ge else None, checks=checks,
                       conflicts=conflicts, explanation=expl, status=(doc.get("quality") or {}).get("status"))
            if s["doc_id"] not in best or r.score > best[s["doc_id"]].score:
                best[s["doc_id"]] = r
        out = sorted(best.values(), key=lambda r: -r.score)[:k]
        return out, {"candidates": len(order), "elapsed_ms": round((time.time() - t0) * 1000), "stage1": list(comps)}
