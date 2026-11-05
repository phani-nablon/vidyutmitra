"""Reviewer verdicts on extracted items: correct / wrong / unsure, with a note. One small JSON file per drawing."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

VERDICTS = ("correct", "wrong", "unsure")
_lock = threading.Lock()


class Marks:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _path(self, doc_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", doc_id):
            raise ValueError("bad document id")
        return self.folder / f"{doc_id}.json"

    def load(self, doc_id: str) -> dict:
        p = self._path(doc_id)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set(self, doc_id: str, item_id: str, verdict: str | None, note: str = "") -> dict:
        if verdict is not None and verdict not in VERDICTS:
            raise ValueError("verdict must be correct, wrong, unsure or empty")
        with _lock:
            d = self.load(doc_id)
            if verdict is None and not note.strip():
                d.pop(item_id, None)
            else:
                d[item_id] = {"verdict": verdict, "note": note.strip(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self.folder.mkdir(parents=True, exist_ok=True)
            p = self._path(doc_id)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
            return d

    def bulk(self, doc_id: str, item_ids: list[str], verdict: str | None) -> dict:
        d = {}
        for i in item_ids:
            old = self.load(doc_id).get(i, {})
            d = self.set(doc_id, i, verdict, old.get("note", ""))
        return d or self.load(doc_id)
