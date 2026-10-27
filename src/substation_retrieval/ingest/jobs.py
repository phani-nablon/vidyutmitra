"""Persistent ingest job queue (SQLite) + worker threads.

Job states: queued -> extracting -> awaiting_review | indexed | failed   (also: duplicate, deleted)
Jobs survive restarts: anything left in `extracting` is re-queued on start-up.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from ..core.util import now_iso

log = logging.getLogger(__name__)

COLS = ["job_id", "batch_id", "file_name", "path", "sha256", "status", "stage", "progress", "message", "doc_id",
        "options", "created_at", "updated_at", "error"]


class JobDB:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, batch_id TEXT, file_name TEXT, path TEXT, sha256 TEXT,
                           status TEXT, stage TEXT, progress REAL, message TEXT, doc_id TEXT, options TEXT, created_at TEXT,
                           updated_at TEXT, error TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status)")
        self.db.execute("UPDATE jobs SET status='queued', stage='requeued after restart' WHERE status='extracting'")
        self.db.commit()

    def add(self, file_name: str, path: str, options: dict | None = None, batch_id: str | None = None) -> dict:
        jid = uuid.uuid4().hex[:12]
        with self._lock, self.db:
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (jid, batch_id, file_name, path, None, "queued", "queued", 0.0, None, None, json.dumps(options or {}),
                             now_iso(), now_iso(), None))
        return self.get(jid)

    def update(self, jid: str, **kw) -> None:
        kw["updated_at"] = now_iso()
        sets = ",".join(f"{k}=?" for k in kw)
        with self._lock, self.db:
            self.db.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", (*kw.values(), jid))

    def get(self, jid: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT " + ",".join(COLS) + " FROM jobs WHERE job_id=?", (jid,)).fetchone()
        return self._row(r) if r else None

    def _row(self, r) -> dict:
        d = dict(zip(COLS, r))
        d["options"] = json.loads(d["options"] or "{}")
        return d

    def list(self, status: str | None = None, limit: int = 200, batch_id: str | None = None) -> list[dict]:
        sql = "SELECT " + ",".join(COLS) + " FROM jobs"
        args: list = []
        conds = []
        if status:
            conds.append("status=?")
            args.append(status)
        if batch_id:
            conds.append("batch_id=?")
            args.append(batch_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [self._row(r) for r in self.db.execute(sql, args).fetchall()]

    def claim(self) -> dict | None:
        with self._lock, self.db:
            r = self.db.execute("SELECT job_id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not r:
                return None
            self.db.execute("UPDATE jobs SET status='extracting', stage='started', updated_at=? WHERE job_id=?", (now_iso(), r[0]))
        return self.get(r[0])

    def counts(self) -> dict:
        with self._lock:
            return dict(self.db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())


class WorkerPool:
    def __init__(self, jobs: JobDB, handler: Callable[[dict], None], n: int = 1, poll_s: float = 1.0):
        self.jobs, self.handler, self.n, self.poll_s = jobs, handler, n, poll_s
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self):
        for i in range(self.n):
            t = threading.Thread(target=self._loop, name=f"ingest-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            job = self.jobs.claim()
            if job is None:
                time.sleep(self.poll_s)
                continue
            try:
                self.handler(job)
            except Exception as e:
                log.exception("job %s failed", job["job_id"])
                self.jobs.update(job["job_id"], status="failed", stage="failed", error=f"{type(e).__name__}: {e}",
                                 message=traceback.format_exc()[-1500:])

    def run_pending(self, max_jobs: int = 10_000) -> int:
        """Synchronous drain (CLI)."""
        n = 0
        while n < max_jobs:
            job = self.jobs.claim()
            if job is None:
                break
            try:
                self.handler(job)
            except Exception as e:
                self.jobs.update(job["job_id"], status="failed", stage="failed", error=f"{type(e).__name__}: {e}",
                                 message=traceback.format_exc()[-1500:])
            n += 1
        return n
