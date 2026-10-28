"""Geography / occupancy vocabulary shared by extraction (facets), the query parser and the benchmark.

localities()    Mumbai localities named in a drawing's text or file name (or in a query)
building_type() occupancy of the host building (rehab, residential tower, hospital, data centre, ...)
"""
from __future__ import annotations

import re

LOCALITIES = ("bandra", "bkc", "malad", "vikhroli", "chembur", "mankhurd", "seepz", "andheri", "goregaon", "powai", "kurla",
              "santacruz", "juhu", "borivali", "kandivali", "dahisar", "ghatkopar", "mulund", "worli", "dadar", "sion", "wadala",
              "colaba", "versova", "jogeshwari", "kanjurmarg", "bhandup", "thane", "airoli", "vashi", "marol", "chandivali", "byculla",
              "parel", "mahim", "matunga", "khar", "vile parle")
BUILDING_TYPES = (("residential rehab building", r"rehab"), ("residential tower", r"residential|flats|tower|apartment"),
                  ("hospital", r"hospital"), ("data centre", r"data\s*cent"), ("commercial building", r"commercial|office|it park|business park"),
                  ("hotel", r"hotel"), ("pagoda / religious site", r"pagoda|temple"), ("industrial unit", r"industr|factory|plant"),
                  ("school / college", r"school|college|university"), ("mall", r"\bmall\b"))


def localities(text: str) -> list[str]:
    t = (text or "").lower()
    return [l for l in LOCALITIES if re.search(rf"\b{l}\b", t)]


def building_type(text: str) -> str | None:
    t = (text or "").lower()
    for name, pat in BUILDING_TYPES:
        if re.search(pat, t):
            return name
    return None


def doc_text(doc: dict) -> str:
    md = doc.get("metadata") or {}
    return " ".join([doc.get("text") or "", doc["source"]["file_name"], str(md.get("location_in_building") or "")])


def doc_building(doc: dict) -> str | None:
    md = doc.get("metadata") or {}
    return building_type(str(md.get("location_in_building") or "") + " " + (doc.get("text") or "")[:1500])
