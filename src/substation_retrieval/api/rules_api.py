"""Design rules: view, add, edit, delete, reset, export / import, and load from the AEML workbook.

The rules live in core/rules.py's RuleStore: config/rules_seed.json (built from Equip_DimensionRules.xlsx by
scripts/build_rules.py) plus the edits, which the first change copies to var/rules.json, so re-seeding never
overwrites them. A rule with an `enforce` key and a numeric value drives a live check setting, re-applied
after every change. Writes need the admin role (api/auth.py): in the detailed design, managing rules is an
SME / admin right, and this app has no separate SME role.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..config import REPO_ROOT
from ..core.rules import RuleStore
from ..services import get_services

router = APIRouter(prefix="/api/rules", tags=["rules"])


def store() -> RuleStore:
    sv = get_services()
    seed = sv.settings.path("paths.rules_seed") if sv.settings.get("paths.rules_seed") else REPO_ROOT / "config" / "rules_seed.json"
    return RuleStore(seed, sv.settings.var("rules.json", mkdir=False))


def _applied(out):
    store().apply(get_services().settings)
    return out


class RuleIn(BaseModel):
    section: Optional[str] = None
    group: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    value: Optional[Any] = None
    unit: Optional[str] = None
    values: Optional[dict] = None
    applies_to: Optional[str] = None
    source: Optional[str] = None
    reference: Optional[str] = None
    ref_status: Optional[str] = None
    rule_no: Optional[str] = None
    enforce: Optional[str] = None
    match: Optional[dict] = None


@router.get("")
def rules_get():
    return store().load()


@router.post("")
def rules_add(r: RuleIn):
    try:
        return _applied(store().add(r.model_dump()))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{rid}")
def rules_update(rid: str, r: RuleIn):
    try:
        return _applied(store().update(rid, r.model_dump(exclude_unset=True)))
    except KeyError:
        raise HTTPException(404, "no such rule")


@router.delete("/{rid}")
def rules_delete(rid: str):
    try:
        store().delete(rid)
    except KeyError:
        raise HTTPException(404, "no such rule")
    return _applied({"ok": True})


@router.post("/reset")
def rules_reset():
    store().reset()
    return _applied({"ok": True})


@router.get("/export")
def rules_export():
    d = {k: v for k, v in store().load().items() if k != "edited"}
    return Response(json.dumps(d, indent=1, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="design_rules.json"'})


def _check(d: Any) -> dict:
    if not isinstance(d, dict) or not isinstance(d.get("sections"), list) or not isinstance(d.get("rules"), list):
        raise HTTPException(400, "not a rules file: expected {\"sections\": [...], \"rules\": [...]}")
    ids = {s.get("id") for s in d["sections"] if isinstance(s, dict)}
    for r in d["rules"]:
        if not isinstance(r, dict) or not r.get("id") or not r.get("title"):
            raise HTTPException(400, "every rule needs an id and a title")
        if r.get("section") not in ids:
            raise HTTPException(400, f"rule {r['id']} names an unknown section {r.get('section')}")
    return {"sections": d["sections"], "rules": d["rules"], "references": d.get("references") or []}


def _merge(new: dict, keep_user: bool) -> dict:
    """New rules replace the old ones; with keep_user, rules people added by hand (origin "user") survive."""
    if keep_user:
        old = store().load()
        ids = {r["id"] for r in new["rules"]}
        sec = {s["id"] for s in new["sections"]}
        for r in old["rules"]:
            if r.get("origin") == "user" and r["id"] not in ids:
                if r.get("section") not in sec:
                    new["sections"].append(next(s for s in old["sections"] if s["id"] == r["section"]))
                    sec.add(r["section"])
                new["rules"].append(r)
    return new


@router.post("/import")
async def rules_import(file: UploadFile = File(...), keep_user: bool = Form(True)):
    """Load a rules file exported from here (JSON)."""
    try:
        d = json.loads((await file.read()).decode("utf-8-sig"))
    except Exception:
        raise HTTPException(400, "the file is not valid JSON")
    d = _merge(_check(d), keep_user)
    st = store()
    st._save(d)                       # noqa: SLF001 - RuleStore's own atomic writer
    _applied(None)
    return {"sections": len(d["sections"]), "rules": len(d["rules"])}


@router.post("/load-workbook")
async def rules_load_workbook(file: UploadFile = File(...), keep_user: bool = Form(True)):
    """Rebuild the rules from Equip_DimensionRules.xlsx with scripts/build_rules.py, then use them."""
    if importlib.util.find_spec("openpyxl") is None:
        raise HTTPException(501, "Reading the workbook needs openpyxl: uv pip install openpyxl (then restart sr serve)")
    script = REPO_ROOT / "scripts" / "build_rules.py"
    spec = importlib.util.spec_from_file_location("sr_build_rules", script)
    if spec is None or not script.exists():
        raise HTTPException(500, f"{script} is missing")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as tmp:
        xls = Path(tmp) / "rules.xlsx"
        xls.write_bytes(await file.read())
        out = Path(tmp) / "rules_seed.json"
        try:
            mod.main(str(xls), None, str(out))
            d = json.loads(out.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(400, f"could not read the workbook ({type(e).__name__}: {e})")
    for r in d.get("rules", []):
        r.setdefault("origin", "seed")
    d = _merge(_check(d), keep_user)
    store()._save(d)                  # noqa: SLF001
    _applied(None)
    return {"sections": len(d["sections"]), "rules": len(d["rules"]), "source": file.filename}
