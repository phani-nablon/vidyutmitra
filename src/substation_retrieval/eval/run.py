"""Run the benchmark: every query through each engine, rankings + component scores saved, metrics reported.

  sr eval-generate            -> var/eval/queries.jsonl (250 queries from the indexed drawings)
  sr eval-run [--modes kg,rag,auto,hybrid]  -> var/eval/runs/<mode>.jsonl + var/eval/report.json / report.md

Split: queries are assigned to dev / test by their source drawing (alternate drawings in doc-id order), so
anything tuned on dev (fusion weights, reranker) is reported on drawings it never saw.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from ..retrieval.query import build_query
from .judge import judgments, mean, metrics

K = 50


def split_of(queries: list[dict]) -> dict[str, str]:
    docs = sorted({q["source_doc"] for q in queries})
    side = {d: ("dev" if i % 2 == 0 else "test") for i, d in enumerate(docs)}
    return {q["qid"]: side[q["source_doc"]] for q in queries}


def run_mode(services, queries: list[dict], mode: str, log=print) -> list[dict]:
    out = []
    t0 = time.time()
    for i, q in enumerate(queries):
        qq = build_query(q["text"], q.get("structured"))
        res = services.orchestrator.search(qq, K, mode)
        out.append({"qid": q["qid"], "engine": res.get("engine"), "trace": res.get("trace"),
                    "ranked": [{"doc": r["doc_id"], "score": r["score"], "components": r["components"],
                                "n_conflicts": len(r["conflicts"]), "engine": r["engine"]} for r in res["results"]]})
        if (i + 1) % 50 == 0:
            log(f"  {mode}: {i + 1}/{len(queries)} ({time.time() - t0:.0f}s)")
    return out


def evaluate(queries: list[dict], runs: dict[str, list[dict]], docs: dict[str, dict]) -> dict:
    qrels = judgments(queries, docs)
    split = split_of(queries)
    qmap = {q["qid"]: q for q in queries}
    report = {}
    for mode, recs in runs.items():
        rows = defaultdict(list)
        for r in recs:
            q = qmap[r["qid"]]
            m = metrics([x["doc"] for x in r["ranked"]], qrels[r["qid"]], q["source_doc"])
            for key in ("all", q["type"], split[r["qid"]]):
                rows[key].append(m)
        report[mode] = {k: mean(v) for k, v in rows.items()}
    return report


def to_markdown(report: dict) -> str:
    keys = ["hit1", "hit5", "hit10", "mrr", "ndcg10", "p5", "r10"]
    lines = ["| engine | slice | n | " + " | ".join(keys) + " |", "|" + "---|" * (len(keys) + 3)]
    for mode, slices in report.items():
        for sl in ("all", "dev", "test", "equipment", "metadata", "area", "site", "mixed"):
            if sl in slices:
                m = slices[sl]
                lines.append(f"| {mode} | {sl} | {m['n']} | " + " | ".join(f"{m[k]:.3f}" for k in keys) + " |")
    return "\n".join(lines)


def save_run(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")


def load_run(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
