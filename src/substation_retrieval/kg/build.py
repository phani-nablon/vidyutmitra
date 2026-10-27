"""Schema-2.1 document -> knowledge-graph partition (vertices + edges), typed by the ontology.

Partition key (pk) = doc_id: every vertex a drawing owns carries it, so re-ingesting a drawing is a
delete-by-pk followed by an insert. Shared vertices (ontology concepts, facets, catalogue items) have
pk = "_shared" and are upserted idempotently.

Vertex labels  document, layout, substation, equipment, opening, structure, route, site_feature,
               concept, facet, catalogue_item
Edge labels    HAS_LAYOUT, HAS_SUBSTATION, HAS_FEATURE, SECTION_OF, DRAWN_IN, CONTAINS, ON_WALL, UNDER,
               BELOW_BEAM, CLEARANCE, FACES, CONNECTS, DISTANCE_TO, IS_A, SUBCLASS_OF,
               MATCHES_CATALOGUE, HAS_FACET, SIMILAR_TO
Polygons are stored as JSON (`polygon_mm`) and WKT (`geo_wkt`, mapped to a JanusGraph geoshape).
"""
from __future__ import annotations

import json
from typing import Any

from ..core.ontology import (ONTOLOGY, base_equipment_type, equipment_class, opening_class, walk)
from ..geometry import polygons as P

SHARED = "_shared"


def concept_vid(cls: str) -> str:
    return f"c:{cls}"


def facet_vid(key: str, value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"f:{key}={value}"


def _wkt(poly_mm) -> str | None:
    g = P.poly(poly_mm) or P.line(poly_mm)
    return g.wkt if g is not None else None


def _props(**kw) -> dict:
    out = {}
    for k, v in kw.items():
        if v is None or v == [] or v == {}:
            continue
        out[k] = json.dumps(v) if isinstance(v, (list, dict)) else v
    return out


def ontology_partition() -> tuple[list[dict], list[dict]]:
    vs, es = [], []
    for node, depth, parent in walk(ONTOLOGY):
        vs.append({"vid": concept_vid(node.id), "label": "concept", "pk": SHARED,
                   "props": _props(name=node.label, cls=node.id, depth=depth, kg_vertex=node.vertex, uses=node.uses)})
        if parent:
            es.append({"label": "SUBCLASS_OF", "out": concept_vid(node.id), "in": concept_vid(parent), "pk": SHARED, "props": {}})
    return vs, es


def doc_facets(doc: dict) -> dict[str, Any]:
    md = doc.get("metadata") or {}
    f = {}
    for k in ("installation", "arrangement", "context", "floor", "kind"):
        if md.get(k):
            f[k] = md[k]
    if md.get("voltage_kv"):
        f["voltage_kv"] = int(md["voltage_kv"])
    f["multiple_substations"] = bool(md.get("multiple_substations"))
    return f


def sub_facets(sub: dict, doc: dict) -> dict[str, list]:
    f: dict[str, list] = {}
    kv = sorted({e["kva"] for e in sub["electrical"]["equipment"] if e.get("kva")
                 and base_equipment_type(e.get("ontology_class") or "") in ("transformer", "packaged")})
    if kv:
        f["kva"] = kv
    if sub.get("floor"):
        f["floor"] = [sub["floor"]]
    plat = (sub["civil"].get("platform") or {})
    f["platform"] = [bool(plat.get("height_mm") or plat.get("type"))]
    if sub["civil"].get("shape"):
        f["shape"] = [sub["civil"]["shape"]]
    return f


def build_partition(doc: dict) -> tuple[list[dict], list[dict]]:
    pk = doc["doc_id"]
    V: list[dict] = []
    E: list[dict] = []

    def v(_vid, _label, **props):
        V.append({"vid": _vid, "label": _label, "pk": pk, "props": _props(**props)})
        return _vid

    def e(_label, _out, _in, **props):
        E.append({"label": _label, "out": _out, "in": _in, "pk": pk, "props": _props(**props)})

    md = doc.get("metadata") or {}
    q = doc.get("quality") or {}
    d = v(pk, "document", name=doc["source"]["file_name"], sha256=doc["source"]["sha256"], status=q.get("status"),
          quality=q.get("score"), method=(doc.get("provenance") or {}).get("method"), installation=md.get("installation"),
          arrangement=md.get("arrangement"), floor=md.get("floor"), substation_count=md.get("substation_count"))
    for k, val in doc_facets(doc).items():
        e("HAS_FACET", d, facet_vid(k, val), key=k)
    for L in doc.get("layouts", []):
        lv = v(f"{pk}:L:{L['id']}", "layout", layout_id=L["id"], type=L.get("type"), title=L.get("title"), page=L.get("page"),
               mm_per_px=L.get("mm_per_px"))
        e("HAS_LAYOUT", d, lv)

    for sub in doc.get("substations", []):
        sid = f"{pk}:{sub['id']}"
        civ = sub["civil"]
        v(sid, "substation", name=sub.get("name"), floor=sub.get("floor"), area_m2=civ.get("area_m2"), length_mm=civ.get("length_mm"),
          width_mm=civ.get("width_mm"), clear_height_mm=civ.get("clear_height_mm"), beam_bottom_mm=civ.get("beam_bottom_mm"),
          plinth_mm=civ.get("plinth_mm"), shape=civ.get("shape"), polygon_mm=civ.get("polygon_mm"), geo_wkt=_wkt(civ.get("polygon_mm")),
          counts=sub.get("counts"))
        e("HAS_SUBSTATION", d, sid)
        e("IS_A", sid, concept_vid("substation"))
        for k, vals in sub_facets(sub, doc).items():
            for val in vals:
                e("HAS_FACET", sid, facet_vid(k, val), key=k)
        if sub.get("equipment_layout_id"):
            e("DRAWN_IN", sid, f"{pk}:L:{sub['equipment_layout_id']}")
        if sub.get("section_layout_id"):
            e("SECTION_OF", f"{pk}:L:{sub['section_layout_id']}", sid, reason=sub.get("section_reason"))

        eq_ids = {}
        for eq in sub["electrical"]["equipment"]:
            cls = eq.get("ontology_class") or equipment_class(eq.get("equipment_type"), eq.get("label"), eq.get("subtype"))
            ev = v(f"{sid}:E:{eq['id']}", "equipment", label=eq.get("label"), equipment_type=eq.get("equipment_type"), cls=cls,
                   kva=eq.get("kva"), kva_source=eq.get("kva_source"), L_mm=eq.get("L_mm"), B_mm=eq.get("B_mm"), H_mm=eq.get("H_mm"),
                   weight_kg=eq.get("weight_kg"), centre_mm=eq.get("centre_mm"), polygon_mm=eq.get("polygon_mm"),
                   geo_wkt=_wkt(eq.get("polygon_mm")), subtype=eq.get("subtype"))
            eq_ids[eq["id"]] = (ev, P.poly(eq.get("polygon_mm")), eq, cls)
            e("CONTAINS", sid, ev)
            e("IS_A", ev, concept_vid(cls))
            cat = eq.get("catalogue") or {}
            if cat.get("unique") and cat.get("candidates"):
                cvid = f"k:{cat['candidates'][0]}"
                e("MATCHES_CATALOGUE", ev, cvid, unique=True)

        opens = []
        for o in sub["access"]["openings"] + sub["ventilation"]["openings"]:
            cls = o.get("ontology_class") or opening_class(o.get("type")) or "door"
            ov = v(f"{sid}:O:{o['id']}", "opening", type=o.get("type"), cls=cls, label=o.get("label"), width_mm=o.get("width_mm"),
                   height_mm=o.get("height_mm"), wall=o.get("wall"), polygon_mm=o.get("polygon_mm"), geo_wkt=_wkt(o.get("polygon_mm")))
            e("ON_WALL", ov, sid, wall=o.get("wall"))
            e("IS_A", ov, concept_vid(cls))
            opens.append((ov, P.poly(o.get("polygon_mm")), cls))
        cut = []
        for c in sub["cable"].get("cutouts", []):
            cv = v(f"{sid}:O:{c['id']}", "opening", type="cutout", cls="cutout", label=c.get("label"), width_mm=c.get("width_mm"),
                   polygon_mm=c.get("polygon_mm"), geo_wkt=_wkt(c.get("polygon_mm")))
            e("CONTAINS", sid, cv)
            e("IS_A", cv, concept_vid("cutout"))
            cut.append((cv, P.poly(c.get("polygon_mm"))))
        for c in sub["cable"].get("entries", []):
            cv = v(f"{sid}:O:{c['id']}", "opening", type=c.get("type"), cls="cable_entry", label=c.get("label"),
                   polygon_mm=c.get("polygon_mm"))
            e("CONTAINS", sid, cv)
            e("IS_A", cv, concept_vid("cable_entry"))
        beams = []
        for st in civ.get("structures", []):
            sv = v(f"{sid}:T:{st['id']}", "structure", type=st["type"], cls=st["type"], bottom_mm=st.get("bottom_mm"),
                   label=st.get("label"), polygon_mm=st.get("polygon_mm"), geo_wkt=_wkt(st.get("polygon_mm")))
            e("CONTAINS", sid, sv)
            e("IS_A", sv, concept_vid(st["type"] if st["type"] in ("beam", "column") else "platform"))
            if st["type"] == "beam":
                beams.append((sv, P.poly(st.get("polygon_mm")), st))
        if civ.get("platform"):
            pv = v(f"{sid}:T:platform", "structure", type="platform", cls="platform", height_mm=(civ["platform"] or {}).get("height_mm"),
                   label=(civ["platform"] or {}).get("type"))
            e("CONTAINS", sid, pv)
            e("IS_A", pv, concept_vid("platform"))
        routes = []
        for r in sub["cable"].get("routes", []):
            cls = "cable_tray" if r.get("type") == "cable_tray" else "trench"
            geom = r.get("polyline_mm") or r.get("polygon_mm")
            rv = v(f"{sid}:R:{r['id']}", "route", type=r.get("type"), cls=cls, kind=r.get("kind"), width_mm=r.get("width_mm"),
                   depth_mm=r.get("depth_mm"), label=r.get("label"), polygon_mm=r.get("polygon_mm"), polyline_mm=r.get("polyline_mm"),
                   geo_wkt=_wkt(geom))
            e("CONTAINS", sid, rv)
            e("IS_A", rv, concept_vid(cls))
            routes.append((rv, P.poly(r.get("polygon_mm")) or P.line(r.get("polyline_mm"))))

        # ---- spatial relations (room mm frame)
        items = list(eq_ids.values())
        for ev, g, eq, cls in items:
            if g is None:
                continue
            for bv, bg, st in beams:
                if bg is not None and g.intersects(bg):
                    gap = (st.get("bottom_mm") or 0) - (eq.get("H_mm") or 0) if st.get("bottom_mm") and eq.get("H_mm") else None
                    e("BELOW_BEAM", ev, bv, clearance_mm=gap)
            for cv, cg in cut:
                if cg is not None and g.intersects(cg):
                    e("UNDER", cv, ev)
            for rv, rg in routes:
                if rg is not None and g.distance(rg) < 300:
                    e("CONNECTS", rv, ev)
            if base_equipment_type(cls) in ("transformer", "packaged", "rmu", "ltp"):
                doors = [(ov, og) for ov, og, oc in opens if og is not None and oc in ("door", "rolling_shutter", "gate")]
                if doors:
                    ov, og = min(doors, key=lambda t: g.distance(t[1]))
                    e("FACES", ev, ov, distance_mm=round(g.distance(og)))
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a[1] is None or b[1] is None:
                    continue
                gap = a[1].distance(b[1])
                if gap < 3000:
                    e("CLEARANCE", a[0], b[0], mm=round(gap))

    # ---- site
    site = doc.get("site") or {}
    fvids = {}
    for f in site.get("features", []):
        fv = v(f"{pk}:F:{f['id']}", "site_feature", cls=f.get("cls"), label=f.get("label"), polygon_mm=f.get("polygon_mm"),
               polyline_mm=f.get("polyline_mm"), geo_wkt=_wkt(f.get("polygon_mm") or f.get("polyline_mm")), layout_id=f.get("layout_id"))
        fvids[f.get("label") or f["id"]] = fv
        e("HAS_FEATURE", d, fv)
        cls = f.get("cls")
        e("IS_A", fv, concept_vid({"access_path": "wayleave", "trench": "trench"}.get(cls, cls)))
    subs = doc.get("substations", [])
    for c in site.get("clearance_checks", []):
        fv = fvids.get(c.get("feature"))
        if fv and subs:
            e("DISTANCE_TO", f"{pk}:{subs[0]['id']}", fv, mm=c.get("distance_mm"), norm_mm=c.get("norm_mm"), meets_norm=c.get("meets_norm"))
    return V, E


def shared_for(V: list[dict], E: list[dict]) -> list[dict]:
    """Shared vertices referenced by a partition's edges (facets, catalogue items)."""
    out = {}
    for ed in E:
        t = ed["in"]
        if t.startswith("f:") and t not in out:
            k, val = t[2:].split("=", 1)
            out[t] = {"vid": t, "label": "facet", "pk": SHARED, "props": {"key": k, "value": val}}
        elif t.startswith("k:") and t not in out:
            out[t] = {"vid": t, "label": "catalogue_item", "pk": SHARED, "props": {"name": t[2:]}}
    return list(out.values())
