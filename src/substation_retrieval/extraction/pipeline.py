"""Extraction v2 pipeline (PDF; DXF/DWG through their adapters).

  Pass 1  (LLM, whole page)       layouts with bbox/type/floor, title block, legend, metadata, substations
  Pass 2  (LLM, per layout crop)  plan: room, equipment, openings, cutouts, beams, columns, trenches, trays, dims
                                  section: heights, cut-through equipment;  site plan: features, dims, distances
  Segmentation refine             legend colour + PDF vector paths, seeded by the LLM boxes
  Scale                           >= 2 dimension fits within 3 %, else printed scale (low confidence)
  Section association             cut through a transformer > tallest equipment > door / rolling shutter
  Catalogue match                 rating from L x B x H / weight when unique (normalise.enrich)
  Plot clearances                 historic self-check against the norms (normalise.enrich)
  Validation + review gate        quality score, coverage, auto / provisional
Debug artifacts (page renders, crops, overlays, masks, raw LLM JSON) go to var/artifacts/<doc_id>/.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

import numpy as np

from ..core.schema import validate_doc
from ..core.util import doc_id_for, dump_json, now_iso
from ..geometry import polygons as P
from . import prompts, scale as SC, segment
from .llm import LLMClient, LLMUnavailable
from .normalise import enrich, metadata_from_classification, substation_from_px

log = logging.getLogger(__name__)

PLAN_TYPES = ("equipment_layout",)
EXTRA_PLAN_TYPES = ("css_layout", "beam_layout", "cutout_layout", "cable_tray_layout", "trench_layout", "ms_plate_layout")
SITE_TYPES = ("site_plan", "key_plan", "wayleave")
MAX_PAGES = 4
MAX_SECTIONS = 3


def _n2px(pt, box):
    """normalised 0..1000 point in a crop -> page px."""
    x0, y0, x1, y1 = box
    return [x0 + float(pt[0]) / 1000 * (x1 - x0), y0 + float(pt[1]) / 1000 * (y1 - y0)]


def _nbox(b, box):
    p0, p1 = _n2px(b[:2], box), _n2px(b[2:4], box)
    return [min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])]


def _fill(tmpl: str, L: dict) -> str:
    """Prompt templates contain JSON braces, so placeholders are replaced literally (no str.format)."""
    return tmpl.replace("{layout_id}", str(L["id"])).replace("{title}", str(L.get("title") or ""))


def _valid_box(b) -> bool:
    return isinstance(b, (list, tuple)) and len(b) >= 4 and all(isinstance(v, (int, float)) for v in b[:4])


def _pad(b, W, H, frac=0.02):
    w, h = b[2] - b[0], b[3] - b[1]
    return [max(0, b[0] - frac * w), max(0, b[1] - frac * h), min(W, b[2] + frac * w), min(H, b[3] + frac * h)]


class Artifacts:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = root
        self.enabled = enabled
        if enabled:
            root.mkdir(parents=True, exist_ok=True)

    def img(self, name: str, rgb: np.ndarray, max_side: int = 2400) -> str | None:
        if not self.enabled:
            return None
        import cv2
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        h, w = rgb.shape[:2]
        s = max_side / max(h, w)
        im = cv2.resize(rgb, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else rgb
        cv2.imwrite(str(p), im[:, :, ::-1] if im.ndim == 3 else im, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return str(p.relative_to(self.root))

    def mask(self, name: str, m: np.ndarray) -> str | None:
        if not self.enabled:
            return None
        import cv2
        p = self.root / "masks" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), m)
        return str(p.relative_to(self.root))

    def json(self, name: str, obj) -> None:
        if self.enabled:
            dump_json(obj, self.root / "llm" / name)


def overlay(rgb: np.ndarray, layouts: list[dict], segs: list[dict]) -> np.ndarray:
    import cv2
    im = rgb.copy()
    t = max(2, im.shape[1] // 900)
    for L in layouts:
        b = L.get("bbox_px")
        if b:
            cv2.rectangle(im, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (37, 99, 235), t)
            cv2.putText(im, f'{L["id"]} {L["type"]}', (int(b[0]) + 5, int(b[1]) + 30 * t // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * t, (37, 99, 235), t)
    col = {"room": (0, 160, 0), "equipment": (235, 99, 37), "trench": (29, 158, 117), "cutout": (225, 29, 93), "beam": (124, 58, 237)}
    for s in segs:
        if s.get("polygon_px"):
            pts = np.array(s["polygon_px"], np.int32)
            cv2.polylines(im, [pts], True, col.get(s["cls"], (234, 138, 12)), t)
    return im


class Extractor:
    def __init__(self, settings, llm: LLMClient | None = None):
        self.s = settings
        self.llm = llm or LLMClient(settings, settings.var_dir / "cache" / "llm")
        self.pv = settings.get("llm.prompt_version", "x2.1")

    # ------------------------------------------------------------ entry
    def extract(self, path: str | Path, sha: str, progress: Callable[[str, float], None] | None = None) -> dict:
        path = Path(path)
        progress = progress or (lambda s, f: None)
        kind = path.suffix.lower().lstrip(".")
        doc_id = doc_id_for(sha)
        art = Artifacts(self.s.var("artifacts", doc_id, mkdir=False), bool(self.s.get("extraction.save_debug", True)))
        if kind == "dwg":
            from .adapters.dwg import dwg_to_dxf
            progress("converting DWG", 0.05)
            path = dwg_to_dxf(path, self.s.var("tmp"))
            kind = "dxf"
        if kind == "dxf":
            from .adapters.dxf import extract_dxf
            doc = extract_dxf(path, sha, self.s)
            doc["source"]["file_name"] = Path(path).name if doc["source"].get("file_name") is None else doc["source"]["file_name"]
            enrich(doc, self.s.get("rules"), self.s.get("extraction"))
            return validate_doc(doc)
        if kind != "pdf":
            raise ValueError(f"unsupported file type: {path.suffix}")
        from .adapters.pdf import PdfDocument
        pdf = PdfDocument(path)
        try:
            if not self.llm.available:
                progress("text-only (no LLM key)", 0.2)
                doc = self._text_only(pdf, path, sha, doc_id)
            else:
                doc = self._llm(pdf, path, sha, doc_id, art, progress)
        finally:
            pdf.close()
        progress("validating", 0.9)
        enrich(doc, self.s.get("rules"), self.s.get("extraction"))
        return validate_doc(doc)

    # ------------------------------------------------------------ text-only fallback
    def _text_only(self, pdf, path, sha, doc_id) -> dict:
        from ..retrieval.query_parser import parse
        text = re.sub(r"\s+", " ", pdf.text())[:20000]
        q = parse(text)
        md = metadata_from_classification({"installation": q.get("installation"), "css_arrangement": q.get("arrangement"),
                                           "location_in_building": q.get("floor") or "", "substation_count": q.get("substation_count") or 1,
                                           "multiple_substations": q.get("multiple_substations", False)}, text)
        eq = []
        for it in q.get("equipment_items") or []:
            for i in range(min(int(it.get("count") or 1), 8)):
                eq.append({"id": f'{it["type"]}-{i + 1}', "type": it["type"], "kva": it.get("kva"), "subtype": it.get("subtype")})
        subs = [substation_from_px({"name": "CSS", "equipment": eq, "room": {"length_mm": q.get("length_mm"), "width_mm": q.get("width_mm"),
                                                                             "clear_height_mm": q.get("clear_height_mm")}}, 0)] if eq else []
        return self._doc(path, sha, doc_id, "text_only", md, [], subs, {}, text, ["LLM unavailable: text-only extraction"])

    def _doc(self, path, sha, doc_id, method, md, layouts, subs, site, text, issues, segments=None, assoc=None) -> dict:
        return {"schema_version": "2.1", "doc_id": doc_id,
                "source": {"file_name": Path(path).name, "sha256": sha, "kind": "pdf", "pages": 1, "path": str(path)},
                "provenance": {"method": method, "llm_model": self.llm.model if method == "llm_v2" else None,
                               "prompt_version": self.pv, "created_at": now_iso()},
                "metadata": md, "layouts": layouts, "segments": segments or [], "associations": assoc or [],
                "substations": subs, "site": site or {}, "quality": {"status": "provisional", "issues": issues}, "text": text}

    # ------------------------------------------------------------ LLM pipeline
    def _ask(self, prompt: str, image: np.ndarray, key: str, art: Artifacts) -> dict:
        out = self.llm.complete(prompts.SYSTEM, prompt, [image], json_mode=True, cache_key=key)
        art.json(key.split("__", 1)[-1] + ".json", out)
        return out if isinstance(out, dict) else {}

    def _llm(self, pdf, path, sha, doc_id, art, progress) -> dict:
        dpi1 = float(self.s.get("llm.render_dpi_pass1", 110))
        dpi2 = float(self.s.get("llm.render_dpi_pass2", 200))
        ex = self.s.get("extraction")
        layouts, segs_all, subs, site, assoc, issues = [], [], [], {}, [], []
        md_raw: dict = {}
        legend: list = []
        title_block: dict = {}
        texts = []
        page_images: list[dict] = []
        n_pages = min(len(pdf), MAX_PAGES)
        for pi in range(n_pages):
            progress(f"pass 1 page {pi + 1}/{n_pages}", 0.05 + 0.1 * pi / n_pages)
            pg = pdf.page(pi, dpi2)
            texts.append(" ".join(w[4] for w in pg.words))
            W, H = pg.size_px
            if art.img(f"page{pi + 1}.jpg", pg.image):
                page_images.append({"page": pi + 1, "base": "artifacts", "file": f"page{pi + 1}.jpg", "size_px": list(pg.size_px)})
            small = pdf.render(pi, dpi1)
            p1 = self._ask(prompts.PASS1, small, f"{sha}__{self.pv}__p{pi + 1}_pass1", art)
            if not md_raw and p1.get("metadata"):
                md_raw = p1["metadata"]
            legend += p1.get("legend") or []
            title_block = title_block or (p1.get("title_block") or {})
            page_layouts = []
            for j, L in enumerate(p1.get("layouts") or []):
                if not _valid_box(L.get("bbox")):
                    continue
                b = _nbox(L["bbox"], [0, 0, W, H])
                lid = f'p{pi + 1}_{L.get("id") or "L" + str(j + 1)}'
                page_layouts.append({"id": lid, "page": pi + 1, "type": (L.get("type") or "unknown").lower(), "title": L.get("title"),
                                     "bbox_px": [round(v, 1) for v in b], "dpi": dpi2, "floor": L.get("floor"),
                                     "scale_text": L.get("scale_text"), "markers": L.get("section_markers") or [],
                                     "_llm_id": L.get("id")})
            layouts += page_layouts
            idmap = {L["_llm_id"]: L for L in page_layouts}
            sub_specs = p1.get("substations") or [{}]
            plans = [L for L in page_layouts if L["type"] in PLAN_TYPES]
            sections = [L for L in page_layouts if L["type"] == "section"]
            extras = [L for L in page_layouts if L["type"] in EXTRA_PLAN_TYPES]
            sites = [L for L in page_layouts if L["type"] in SITE_TYPES]
            for si, spec in enumerate(sub_specs):
                plan = idmap.get(spec.get("equipment_layout")) or (plans[si] if si < len(plans) else (plans[0] if plans else None))
                if plan is None:
                    continue
                progress(f"pass 2 plan {plan['id']}", 0.2 + 0.5 * si / max(1, len(sub_specs)))
                raw, seg = self._plan(pdf, pg, plan, legend, sha, art, ex)
                segs_all += seg
                secs = [idmap[x] for x in spec.get("section_layouts") or [] if x in idmap] or sections
                chosen, reason, sec_out = self._sections(pg, secs[:MAX_SECTIONS], raw, sha, art)
                if chosen:
                    self._apply_section(raw, sec_out)
                    raw["section_used"] = chosen["id"]
                    raw["section_reason"] = reason
                elif not secs:
                    issues.append(f"{spec.get('name') or plan['id']}: no section found; heights missing")
                if len(sub_specs) == 1:
                    for xl in extras:
                        self._merge_extra(pdf, pg, xl, raw, sha, art, ex, segs_all)
                raw["name"] = spec.get("name") or f"CSS-{len(subs) + 1}"
                raw["floor"] = plan.get("floor")
                raw["equipment_layout_id"] = plan["id"]
                raw["page"] = pi + 1
                sub = substation_from_px(raw, len(subs))
                for extra_key in ("_cutouts_mm", "_beams_mm", "_columns_mm", "_routes_mm"):
                    for item in raw.get(extra_key, []):
                        if extra_key == "_cutouts_mm":
                            sub["cable"]["cutouts"].append(item)
                        elif extra_key == "_routes_mm":
                            sub["cable"]["routes"].append(item)
                        else:
                            sub["civil"]["structures"].append(item)
                subs.append(sub)
                assoc.append({"substation_id": sub["id"], "plan_layout": plan["id"], "section_layout": raw.get("section_used"),
                              "method": "section_rank", "reason": raw.get("section_reason")})
            if sites and not site:
                site = self._site(pdf, pg, sites[0], legend, sha, art, ex, segs_all)
            art.img(f"page{pi + 1}_overlay.jpg", overlay(pg.image, page_layouts, [s for s in segs_all if s.get("page") == pi + 1]))
        for L in layouts:
            L.pop("_llm_id", None)
            L.pop("markers", None)
        md = metadata_from_classification({"installation": md_raw.get("installation"), "css_arrangement": md_raw.get("arrangement"),
                                           "location_in_building": md_raw.get("location_in_building") or md_raw.get("floor") or "",
                                           "evidence": md_raw.get("evidence"), "substation_count": md_raw.get("substation_count") or len(subs) or 1,
                                           "multiple_substations": md_raw.get("multiple_substations"), "voltage_kv": md_raw.get("voltage_kv")},
                                          " ".join(texts))
        if md_raw.get("context"):
            md["context"] = md_raw["context"]
        if md_raw.get("floor"):
            md["floor"] = md_raw["floor"]
        md["title_block"] = title_block
        md["legend"] = legend
        if not subs:
            issues.append("no equipment layout found")
        text = re.sub(r"\s+", " ", " ".join(texts))[:20000]
        segments = [{"id": f"M{i + 1}", **s} for i, s in enumerate(segs_all)]
        doc = self._doc(path, sha, doc_id, "llm_v2", md, layouts, subs, site, text, issues, segments, assoc)
        doc["source"]["page_images"] = page_images
        doc["source"]["pages"] = len(pdf)
        return doc

    # ------------------------------------------------------------ pass 2 helpers
    def _crop(self, pg, L):
        b = _pad(L["bbox_px"], *pg.size_px)
        x0, y0, x1, y1 = (int(v) for v in b)
        return pg.image[y0:y1, x0:x1], [x0, y0, x1, y1]

    def _scale(self, L, dims_px, pg, box, ex) -> dict:
        ends = SC.vector_ends(pg.paths, box)
        cal = SC.calibrate(dims_px, ex.get("scale_fit_tol", 0.03), ex.get("scale_min_fits", 2), ends)
        cal = cal or SC.from_scale_text(L.get("scale_text"), pg.dpi)
        if cal:
            L.update({"mm_per_px": round(cal["mm_per_px"], 5), "scale_source": cal["source"], "scale_confidence": cal["confidence"]})
        else:
            L.update({"scale_source": "none"})
        return cal or {}

    def _plan(self, pdf, pg, L, legend, sha, art, ex):
        crop, box = self._crop(pg, L)
        L["crop_image"] = art.img(f"layouts/{L['id']}.jpg", crop)
        L["crop_base"] = "artifacts" if L["crop_image"] else None
        out = self._ask(_fill(prompts.PASS2_PLAN, L), crop, f"{sha}__{self.pv}__{L['id']}_plan", art)
        dims = [{"value_mm": d.get("value_mm"), "p1": _n2px(d["p1"], box), "p2": _n2px(d["p2"], box), "text": d.get("text")}
                for d in out.get("dimensions") or [] if d.get("p1") and d.get("p2")]
        cal = self._scale(L, dims, pg, box, ex)
        s = cal.get("mm_per_px")
        room = out.get("room") or {}
        poly = [_n2px(p, box) for p in room.get("polygon") or []]
        rb = None
        if len(poly) >= 3:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            rb = [min(xs), min(ys), max(xs), max(ys)]
        seeds = {"cutout": [_nbox(c["bbox"], box) for c in out.get("cutouts") or [] if _valid_box(c.get("bbox"))],
                 "beam": [_nbox(c["bbox"], box) for c in out.get("beams") or [] if _valid_box(c.get("bbox"))],
                 "column": [_nbox(c["bbox"], box) for c in out.get("columns") or [] if _valid_box(c.get("bbox"))]}
        segs, masks = segment.refine(crop, (box[0], box[1]), seeds, legend, pg.paths)
        for cls, m in masks.items():
            art.mask(f"{L['id']}_{cls}.png", m)
        for sg in segs:
            sg.update({"layout_id": L["id"], "page": L["page"]})
        segs.append({"cls": "room", "polygon_px": poly, "layout_id": L["id"], "page": L["page"], "source": "llm", "confidence": 0.7})
        raw = {"mm_per_px": s, "room": {"bbox": rb, "polygon": poly if len(poly) >= 3 else None,
                                        "length_mm": room.get("length_mm"), "width_mm": room.get("width_mm"), "shape": room.get("shape")},
               "equipment": [], "openings": [], "clearances_dimensioned": out.get("clearances") or []}
        for i, e in enumerate(out.get("equipment") or []):
            if not _valid_box(e.get("bbox")):
                continue
            raw["equipment"].append({"id": e.get("label") or f"E{i + 1}", "label": e.get("label"), "type": e.get("equipment_type"),
                                     "subtype": e.get("subtype"), "kva": e.get("kva"), "bbox": _nbox(e["bbox"], box),
                                     "footprint_mm": e.get("dims_text_mm"), "height_mm": e.get("height_mm"), "weight": e.get("weight_text")})
        for o in out.get("openings") or []:
            if _valid_box(o.get("bbox")):
                raw["openings"].append({"type": o.get("type"), "label": o.get("label"), "bbox": _nbox(o["bbox"], box),
                                        "width_mm": o.get("width_mm"), "height_mm": o.get("height_mm")})
        origin = (rb[0], rb[1]) if rb else (box[0], box[1])
        if s:
            self._refined_to_mm(segs, origin, s, raw, out)
            for t in out.get("trenches") or []:
                pl = [_n2px(p, box) for p in t.get("polyline") or []]
                if len(pl) >= 2:
                    raw.setdefault("_routes_mm", []).append({"id": f"RT{len(raw.get('_routes_mm', [])) + 1}", "type": "trench", "kind": t.get("kind"),
                                                             "polyline_mm": P.px_to_mm(pl, origin, s), "width_mm": t.get("width_mm"),
                                                             "depth_mm": t.get("depth_mm"), "label": t.get("label")})
            for t in out.get("cable_trays") or []:
                pl = [_n2px(p, box) for p in t.get("polyline") or []]
                if len(pl) >= 2:
                    raw.setdefault("_routes_mm", []).append({"id": f"CT{len(raw.get('_routes_mm', [])) + 1}", "type": "cable_tray",
                                                             "polyline_mm": P.px_to_mm(pl, origin, s), "width_mm": t.get("width_mm")})
        return raw, segs

    def _refined_to_mm(self, segs, origin, s, raw, out):
        labels = {"beam": out.get("beams") or [], "cutout": out.get("cutouts") or []}
        for sg in segs:
            if sg["cls"] not in ("cutout", "beam", "column") or not sg.get("polygon_px"):
                continue
            pm = P.px_to_mm(sg["polygon_px"], origin, s)
            sg["polygon_mm"] = pm
            n = len(raw.get("_cutouts_mm", [])) + len(raw.get("_beams_mm", [])) + len(raw.get("_columns_mm", [])) + 1
            if sg["cls"] == "cutout":
                raw.setdefault("_cutouts_mm", []).append({"id": f"CO{n}", "type": "cutout", "polygon_mm": pm,
                                                          "label": (labels["cutout"][0].get("label") if labels["cutout"] else None)})
            elif sg["cls"] == "beam":
                raw.setdefault("_beams_mm", []).append({"id": f"B{n}", "type": "beam", "polygon_mm": pm,
                                                        "label": (labels["beam"][0].get("label") if labels["beam"] else None)})
            else:
                raw.setdefault("_columns_mm", []).append({"id": f"C{n}", "type": "column", "polygon_mm": pm})

    def _merge_extra(self, pdf, pg, L, raw, sha, art, ex, segs_all):
        """Beam / cutout / tray / trench panels: own scale + room outline -> room mm frame of the substation."""
        try:
            xraw, segs = self._plan(pdf, pg, L, [], sha, art, ex)
        except LLMUnavailable:
            return
        segs_all += segs
        for k in ("_cutouts_mm", "_beams_mm", "_columns_mm", "_routes_mm"):
            raw.setdefault(k, []).extend(xraw.get(k, []))
        if not raw.get("openings") and xraw.get("openings"):
            raw["openings"] = xraw["openings"]

    def _sections(self, pg, secs, raw, sha, art):
        outs = []
        for L in secs:
            crop, box = self._crop(pg, L)
            L["crop_image"] = art.img(f"layouts/{L['id']}.jpg", crop)
            L["crop_base"] = "artifacts" if L["crop_image"] else None
            try:
                o = self._ask(_fill(prompts.PASS2_SECTION, L), crop,
                              f"{sha}__{self.pv}__{L['id']}_section", art)
            except LLMUnavailable:
                continue
            outs.append((L, o))
        if not outs:
            return None, None, None

        def rank(t):
            L, o = t
            cuts = " ".join(o.get("cuts_through") or []).lower()
            tx = 3 if re.search(r"\bdt|transformer|tx\b", cuts) else 0
            tallest = max([h.get("height_mm") or 0 for h in o.get("equipment_heights") or []] or [0]) / 10000
            door = 1 if re.search(r"door|shutter", (o.get("notes") or "") + cuts) else 0
            return (tx, tallest, door, 1 if o.get("clear_height_mm") else 0)
        L, o = max(outs, key=rank)
        r = rank((L, o))
        reason = ("cuts through a transformer" if r[0] else "tallest equipment" if r[1] else "door / rolling shutter" if r[2] else "only section with heights")
        return L, f"{L.get('title') or L['id']}: {reason}", o

    def _apply_section(self, raw, o):
        room = raw.setdefault("room", {})
        for k in ("clear_height_mm", "slab_to_soffit_mm", "beam_bottom_mm", "plinth_mm", "floor_to_floor_mm"):
            if o.get(k):
                room[k] = o[k]
        if o.get("platform_type"):
            room["plinth_type"] = o["platform_type"]
        if o.get("trench_depth_mm"):
            for t in raw.get("_routes_mm", []):
                t.setdefault("depth_mm", o["trench_depth_mm"])
            room["trench_depth_mm"] = o["trench_depth_mm"]
        hs = {re.sub(r"\W", "", (h.get("label") or "").lower()): h for h in o.get("equipment_heights") or []}
        for e in raw.get("equipment", []):
            h = hs.get(re.sub(r"\W", "", (e.get("label") or "").lower()))
            if h:
                e["height_mm"] = e.get("height_mm") or h.get("height_mm")
                e["ceiling_clearance_mm"] = h.get("ceiling_clearance_mm")
        room["height_source"] = f"section: {o.get('section_name') or ''} {o.get('notes') or ''}".strip()

    def _site(self, pdf, pg, L, legend, sha, art, ex, segs_all) -> dict:
        crop, box = self._crop(pg, L)
        L["crop_image"] = art.img(f"layouts/{L['id']}.jpg", crop)
        L["crop_base"] = "artifacts" if L["crop_image"] else None
        try:
            out = self._ask(_fill(prompts.PASS2_SITE, L), crop, f"{sha}__{self.pv}__{L['id']}_site", art)
        except LLMUnavailable:
            return {}
        dims = [{"value_mm": d.get("value_mm"), "p1": _n2px(d["p1"], box), "p2": _n2px(d["p2"], box)}
                for d in out.get("dimensions") or [] if d.get("p1") and d.get("p2")]
        cal = self._scale(L, dims, pg, box, ex)
        s = cal.get("mm_per_px")
        org = (box[0], box[1])
        feats, polys = [], []
        for i, f in enumerate(out.get("features") or []):
            pp = [_n2px(p, box) for p in f.get("polygon") or []]
            pl = [_n2px(p, box) for p in f.get("polyline") or []]
            rec = {"id": f"F{i + 1}", "cls": f.get("cls"), "label": f.get("label"), "layout_id": L["id"], "width_m": f.get("width_m"),
                   "polygon_px": pp or None, "polyline_px": pl or None}
            if s:
                rec["polygon_mm"] = P.px_to_mm(pp, org, s) if len(pp) >= 3 else None
                rec["polyline_mm"] = P.px_to_mm(pl, org, s) if len(pl) >= 2 else None
            feats.append(rec)
            segs_all.append({"cls": f.get("cls"), "polygon_px": pp or None, "layout_id": L["id"], "page": L["page"], "source": "llm", "confidence": 0.6})
        seeds = {"site_boundary": []}
        for poly in out.get("substation_polygons") or []:
            pp = [_n2px(p, box) for p in poly]
            if len(pp) >= 3:
                xs, ys = [p[0] for p in pp], [p[1] for p in pp]
                seeds["site_boundary"].append([min(xs), min(ys), max(xs), max(ys)])
                if s:
                    polys.append(P.px_to_mm(pp, org, s))
        segs, masks = segment.refine(crop, org, seeds, legend, pg.paths)
        for cls, m in masks.items():
            art.mask(f"{L['id']}_{cls}.png", m)
        for sg in segs:
            sg.update({"layout_id": L["id"], "page": L["page"]})
            if s and sg["cls"] == "site_boundary" and sg["source"] != "llm":
                polys = [P.px_to_mm(sg["polygon_px"], org, s)]
        segs_all += segs
        checks = []
        norms = self.s.get("rules.plot_clearance_mm")
        for d in out.get("stated_distances") or []:
            k = d.get("feature")
            if k in norms and d.get("mm"):
                checks.append({"feature": k, "cls": k, "distance_mm": d["mm"], "norm_mm": norms[k], "meets_norm": d["mm"] >= norms[k],
                               "source": "stated"})
        return {"layout_id": L["id"], "mm_per_px": s, "substation_polygons_mm": polys, "features": feats, "clearance_checks": checks}
