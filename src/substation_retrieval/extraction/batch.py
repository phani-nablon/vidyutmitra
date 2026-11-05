"""Batch extraction v2 over a folder of drawings (real LLM calls) + a comparison report against v1.

  sr extract-all [--dir PDF_DIR] [--workers 4] [--limit N] [--index]

Each drawing: extraction v2 -> var/extractions/<doc_id>/doc.json (+ artifacts), then a row in
var/reports/extract_v2_<timestamp>.csv comparing it with the reviewed v1 extraction of the same file
(when one exists). LLM responses are cached, so a re-run only pays for drawings that failed or changed.
With --index each result is also upserted into the graph + RAG index and one version is published.
"""
from __future__ import annotations

import csv
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..core.util import dump_json, now_iso, sha256_file

COLS = ["file", "status", "seconds", "quality", "q_status", "n_layouts", "subs_v2", "subs_v1", "eq_v2", "eq_v1", "tx_v2", "tx_v1",
        "L_v2", "L_v1", "W_v2", "W_v1", "h_v2", "h_v1", "scale_source", "section_v2", "section_v1", "kva_v2", "kva_v1", "issues", "error"]


def _summary(doc: dict) -> dict:
    subs = doc.get("substations") or []
    s0 = subs[0] if subs else {"civil": {}, "electrical": {"equipment": []}}
    eq = [e for s in subs for e in s["electrical"]["equipment"]]
    lay = {L["id"]: L for L in doc.get("layouts", [])}
    plan = lay.get(s0.get("equipment_layout_id")) or {}
    return {"subs": len(subs), "eq": len(eq),
            "tx": sum(1 for e in eq if (e.get("equipment_type") or "").startswith("transformer")),
            "L": s0["civil"].get("length_mm"), "W": s0["civil"].get("width_mm"), "h": s0["civil"].get("clear_height_mm"),
            "scale": plan.get("scale_source"), "section": (lay.get(s0.get("section_layout_id")) or {}).get("title") or s0.get("section_layout_id"),
            "kva": "/".join(str(e["kva"]) for e in eq if e.get("kva"))}


def run(services, pdf_dir: Path, workers: int = 4, limit: int | None = None, index: bool = False, log=print) -> Path:
    s = services.settings
    files = sorted(p for p in Path(pdf_dir).iterdir() if p.suffix.lower() in (".pdf", ".dxf", ".dwg"))
    if limit:
        files = files[:limit]
    cache_only = not services.llm.available
    if cache_only:
        log("No LLM key (OPENAI_API_KEY / ASTRA_API_KEY): replaying var/cache/llm; drawings without cached replies are skipped")
    ex = services.ingest.extractor
    v1_dir = s.path("paths.v1_parsed_dir")
    report = s.var("reports", f"extract_v2_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    rows, done_ids = [], []

    def one(p: Path) -> dict:
        t0 = time.time()
        row = {"file": p.name}
        try:
            sha = sha256_file(p)
            if cache_only and not services.llm.cached(sha):
                row.update({"status": "skipped", "error": "no LLM key and no cached replies", "seconds": 0.0})
                return row
            doc = ex.extract(p, sha)
            doc["source"]["path"] = str(p)
            jp = services.ingest.doc_path(doc["doc_id"])
            dump_json(doc, jp)
            if index:
                services.ingest.index_doc(doc, jp, similar=False)
                done_ids.append(doc["doc_id"])
            a = _summary(doc)
            row.update({"status": "ok", "quality": doc["quality"]["score"], "q_status": doc["quality"]["status"],
                        "n_layouts": len(doc.get("layouts", [])), "subs_v2": a["subs"], "eq_v2": a["eq"], "tx_v2": a["tx"],
                        "L_v2": a["L"], "W_v2": a["W"], "h_v2": a["h"], "scale_source": a["scale"], "section_v2": a["section"],
                        "kva_v2": a["kva"], "issues": "; ".join(doc["quality"].get("issues", []))[:300]})
            v1 = v1_dir / p.stem / f"{p.stem}.json"
            if v1.exists():
                from .v1_import import convert
                b = _summary(convert(v1, None, s.get("rules"), s.get("extraction")))
                row.update({"subs_v1": b["subs"], "eq_v1": b["eq"], "tx_v1": b["tx"], "L_v1": b["L"], "W_v1": b["W"], "h_v1": b["h"],
                            "section_v1": b["section"], "kva_v1": b["kva"]})
        except Exception as e:
            row.update({"status": "failed", "error": f"{type(e).__name__}: {e}"[:300]})
            log(traceback.format_exc()[-800:])
        row["seconds"] = round(time.time() - t0, 1)
        return row

    log(f"extracting {len(files)} drawing(s) with {services.llm.model}, {workers} in parallel; report -> {report}")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(one, p): p for p in files}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            rows.append(r)
            log(f"[{i}/{len(files)}] {r['status']:6} {r.get('quality', ''):>5} {r['seconds']:6.1f}s  {r['file']}"
                + (f"  ERROR {r['error']}" if r.get("error") else ""))
            with open(report, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, COLS)
                w.writeheader()
                w.writerows(sorted(rows, key=lambda x: x["file"]))
    if index and done_ids:
        services.ingest.refresh_similar(None)
        v = services.index.publish(f"extract-all v2 ({len(done_ids)} docs) {now_iso()}", done_ids)
        log(f"published index version {v}")
    ok = sum(1 for r in rows if r["status"] == "ok")
    log(f"done: {ok}/{len(rows)} extracted; report {report}")
    return report
