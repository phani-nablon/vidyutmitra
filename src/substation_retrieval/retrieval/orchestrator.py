"""Engine orchestration: retrieval_mode kg | rag | auto | both, independent enable flags, fallback.

auto  KG first; falls back to RAG when
        - the KG engine is disabled or the graph store is unreachable / slower than engines.kg.timeout_s
        - the query graph has fewer than engines.kg.min_typed_vertices typed vertices
        - fewer than engines.kg.min_candidates results score >= engines.kg.min_score
both  runs both and merges per document (max score, engine tags kept)
kg    KG only (error if disabled/unavailable)       rag   RAG only
The ontology visualisation never depends on these flags.
"""
from __future__ import annotations

import time

from ..kg.matcher import KGUnavailable
from .query import describe, present


class Orchestrator:
    def __init__(self, kg_engine, rag_engine, settings):
        self.kg = kg_engine
        self.rag = rag_engine
        self.settings = settings

    def flags(self) -> dict:
        return {"retrieval_mode": self.settings.get("engines.retrieval_mode", "auto"),
                "kg_enabled": bool(self.settings.get("engines.kg.enabled", True)),
                "rag_enabled": bool(self.settings.get("engines.rag.enabled", True))}

    def set_flags(self, retrieval_mode=None, kg_enabled=None, rag_enabled=None) -> dict:
        if retrieval_mode:
            if retrieval_mode not in ("kg", "rag", "auto", "both"):
                raise ValueError("retrieval_mode must be kg | rag | auto | both")
            self.settings.set("engines.retrieval_mode", retrieval_mode)
        if kg_enabled is not None:
            self.settings.set("engines.kg.enabled", bool(kg_enabled))
        if rag_enabled is not None:
            self.settings.set("engines.rag.enabled", bool(rag_enabled))
        return self.flags()

    def health(self) -> dict:
        f = self.flags()
        out = {"flags": f}
        try:
            out["kg"] = {**self.kg.health(), "enabled": f["kg_enabled"]}
        except Exception as e:
            out["kg"] = {"ok": False, "error": str(e), "enabled": f["kg_enabled"]}
        try:
            out["rag"] = {**self.rag.health(), "enabled": f["rag_enabled"]}
        except Exception as e:
            out["rag"] = {"ok": False, "error": str(e), "enabled": f["rag_enabled"]}
        return out

    def search(self, q: dict, k: int | None = None, mode: str | None = None) -> dict:
        k = k or int(self.settings.get("scoring.top_k", 10))
        f = self.flags()
        mode = mode or f["retrieval_mode"]
        t0 = time.time()
        trace: list[str] = []
        info: dict = {}
        results = []
        engine_used = None

        def run_kg():
            if not f["kg_enabled"]:
                raise KGUnavailable("KG engine disabled")
            return self.kg.search(q, k)

        def run_rag():
            if not f["rag_enabled"]:
                raise RuntimeError("RAG engine disabled")
            return self.rag.search(q, k)

        if mode == "kg":
            results, info["kg"] = run_kg()
            engine_used = "kg"
        elif mode == "rag":
            results, info["rag"] = run_rag()
            engine_used = "rag"
        elif mode == "both":
            merged = {}
            for name, fn in (("kg", run_kg), ("rag", run_rag)):
                try:
                    rs, info[name] = fn()
                except Exception as e:
                    trace.append(f"{name} skipped: {e}")
                    continue
                for r in rs:
                    cur = merged.get(r.doc_id)
                    if cur is None or r.score > cur.score:
                        if cur is not None:
                            r.explanation = r.explanation + [f"{cur.engine} score {cur.score:.2f}"]
                        merged[r.doc_id] = r
                    else:
                        cur.explanation.append(f"{r.engine} score {r.score:.2f}")
            results = sorted(merged.values(), key=lambda r: -r.score)[:k]
            engine_used = "both"
        else:  # auto
            reason = None
            try:
                results, info["kg"] = run_kg()
                engine_used = "kg"
                kgc = self.settings.get("engines.kg")
                if info["kg"]["typed_vertices"] < kgc["min_typed_vertices"] and not present(q)["footprint"]:
                    reason = f"query graph has {info['kg']['typed_vertices']} typed vertices (< {kgc['min_typed_vertices']})"
                elif sum(1 for r in results if r.score >= kgc["min_score"]) < kgc["min_candidates"]:
                    reason = f"fewer than {kgc['min_candidates']} KG candidates scored >= {kgc['min_score']}"
            except Exception as e:
                reason = str(e)
            if reason:
                trace.append(f"fallback to RAG: {reason}")
                try:
                    rag_results, info["rag"] = run_rag()
                    if rag_results:
                        results, engine_used = rag_results, "rag"
                except Exception as e:
                    trace.append(f"RAG unavailable: {e}")
        from ..geometry.compat import query_feasibility
        feas = query_feasibility(q, q.get("area_m2"), self.settings.get("rules"))
        return {"engine": engine_used, "feasibility": feas, "mode": mode, "flags": f, "results": [r.as_dict() for r in results],
                "info": info, "trace": trace, "present": present(q), "asked": describe(q), "query": _public_query(q),
                "elapsed_ms": round((time.time() - t0) * 1000)}


def _public_query(q: dict) -> dict:
    return {k: v for k, v in q.items() if not k.startswith("_")}
