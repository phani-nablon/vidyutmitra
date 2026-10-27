"""Command line:  sr <command>

  serve                    API + UI (http://127.0.0.1:8077) with in-process ingest workers
  import-v1 [--dir D]      index the v1 extractions (no LLM cost)
  ingest PATH... [--wait]  queue PDF/DXF/DWG files or folders; --wait processes them now
  worker                   run ingest workers without the API
  rebuild                  re-index every stored extraction (no LLM)
  extract FILE [--out F]   run extraction v2 on one drawing and write the JSON (does not index)
  search "TEXT" [--mode M] [--json FILE] [--k N]
  jobs | health | versions
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def _svc():
    from .services import get_services
    return get_services()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sr", description="CSS design retrieval")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p = sub.add_parser("import-v1")
    p.add_argument("--dir")
    p.add_argument("--pdf-dir")
    p = sub.add_parser("ingest")
    p.add_argument("paths", nargs="+")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-v1", action="store_true")
    sub.add_parser("worker")
    sub.add_parser("rebuild")
    p = sub.add_parser("extract")
    p.add_argument("file")
    p.add_argument("--out")
    p = sub.add_parser("search")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--json")
    p.add_argument("--mode")
    p.add_argument("--k", type=int, default=5)
    sub.add_parser("jobs")
    sub.add_parser("health")
    sub.add_parser("versions")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "serve":
        import uvicorn
        from .config import settings
        s = settings()
        uvicorn.run("substation_retrieval.api.app:app", host=a.host or s.get("server.host", "127.0.0.1"),
                    port=int(a.port or s.get("server.port", 8077)), log_level="info")
        return 0
    sv = _svc()
    if a.cmd == "import-v1":
        r = sv.ingest.import_v1(a.dir, a.pdf_dir)
        print(json.dumps({k: v for k, v in r.items() if k != "failed"}, indent=1))
        for f in r["failed"]:
            print("FAILED", f["file"], f["error"], file=sys.stderr)
    elif a.cmd == "ingest":
        files = []
        for p in a.paths:
            pp = Path(p)
            files += [x for x in sorted(pp.iterdir()) if x.suffix.lower() in (".pdf", ".dxf", ".dwg")] if pp.is_dir() else [pp]
        r = sv.ingest.submit_many(files, {"force": a.force, "use_v1": not a.no_v1})
        print(f"queued {len(r['jobs'])} job(s), batch {r['batch_id']}")
        if a.wait:
            from .ingest.jobs import WorkerPool
            n = WorkerPool(sv.jobs, sv.ingest.handle).run_pending()
            for j in sv.jobs.list(batch_id=r["batch_id"]):
                print(f"{j['status']:16} {j['file_name']}  {j.get('message') or j.get('error') or ''}")
            print(f"processed {n}")
    elif a.cmd == "worker":
        sv.ingest.start_workers(1)
        print("worker running (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "rebuild":
        print(json.dumps(sv.ingest.rebuild(), indent=1))
    elif a.cmd == "extract":
        from .core.util import dump_json, sha256_file
        sha = sha256_file(a.file)
        doc = sv.ingest.extractor.extract(a.file, sha, lambda s_, f_: print(f"  {f_:4.0%} {s_}"))
        out = a.out or str(Path(a.file).with_suffix(".v2.json"))
        dump_json(doc, out)
        print(f"wrote {out}: {len(doc['substations'])} substation(s), quality {doc['quality']['score']} ({doc['quality']['status']})")
    elif a.cmd == "search":
        from .retrieval.query import build_query
        structured = json.loads(Path(a.json).read_text()) if a.json else None
        res = sv.orchestrator.search(build_query(a.text, structured), a.k, a.mode)
        print(f"engine {res['engine']} ({res['elapsed_ms']} ms)  asked: {', '.join(res['asked'])}")
        for t in res["trace"]:
            print("  ", t)
        for i, r in enumerate(res["results"], 1):
            print(f"{i:2}. {r['score']:.3f} {r['verdict']:6} {r['file_name']} [{r['substation_name']}]  cov {r['coverage']:.0%}")
            for c in r["conflicts"][:3]:
                print("      conflict:", c)
    elif a.cmd == "jobs":
        for j in sv.jobs.list(limit=50):
            print(f"{j['created_at']}  {j['status']:16} {j['progress'] or 0:4.0%}  {j['file_name']}  {j.get('error') or j.get('message') or ''}")
    elif a.cmd == "health":
        print(json.dumps({"index": sv.index.stats(), "graph": sv.store.stats(), "engines": sv.orchestrator.health(),
                          "llm": sv.llm.describe(), "jobs": sv.jobs.counts()}, indent=1, default=str))
    elif a.cmd == "versions":
        for v in sv.index.versions():
            print(v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
