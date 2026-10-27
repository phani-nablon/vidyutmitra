"""Small shared helpers."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def doc_id_for(sha: str) -> str:
    return "d" + sha[:15]


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:n] or "x"


def dump_json(obj: Any, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(p)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


FLOOR_PATTERNS = (
    (r"upper\s+basement|lower\s+basement|basement|cellar", "basement"),
    (r"\bstilt", "stilt"),
    (r"podium", "podium"),
    (r"\b(1st|first)\s*(floor|fl\b|flr)", "first"),
    (r"\b(2nd|second)\s*floor", "second"),
    (r"terrace|roof", "terrace"),
    (r"ground\s*(floor|level|fl\b)|\bgf\b|\bgrade\b|open\s+plot|open\s+ground", "ground"),
)


def _strip_context(t: str) -> str:
    """Drop phrases describing what is above/below the substation (not where it sits)."""
    t = re.sub(r"(lower\s+|upper\s+)?(basements?|cellar|stack\s+parking|puzzle\s+parking|parking)[^.;,()]{0,40}\bbelow\b", " ", t)
    t = re.sub(r"\b(over|above)\s+(the\s+)?(lower\s+|upper\s+)?(basement|stilt|parking)[^.;,()]*", " ", t)
    t = re.sub(r"\bbelow\b[^.;,()]{0,40}(basement|cellar)[^.;,()]*", " ", t)
    t = re.sub(r"(first|1st|second|2nd)\s+floor\s+above|(flats|floors?|podium slabs?|dg)[^.;,()]{0,20}above", " ", t)
    return t


def derive_floor(text: str | None) -> str | None:
    if not text:
        return None
    t = _strip_context(text.lower())
    for pat, fl in FLOOR_PATTERNS:
        if re.search(pat, t):
            return fl
    return None


def derive_context(text: str | None, installation: str | None) -> str | None:
    t = (text or "").lower()
    if re.search(r"standalone|stand[\s-]alone|separate\s+(single[\s-]storey\s+)?building|independent\s+building|single[\s-]storey", t):
        return "standalone_building"
    if "temporary" in t:
        return "temporary"
    if installation == "open_to_sky":
        return "outdoor_yard"
    if re.search(r"building|tower|residential|commercial|hotel|hospital|office|mall|data\s*cent|rehab|wing", t):
        return "in_building"
    return None
