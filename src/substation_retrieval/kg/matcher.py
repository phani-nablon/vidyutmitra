"""Ontology knowledge-graph matching (primary engine).

1. Query graph: the requirement becomes a small graph typed by the ontology - a query substation
   vertex, equipment vertices (one per unit, with kVA / ways / catalogue dimensions), opening,
   structure, route and site-feature vertices, facet links, and the relations the query implies
   (FACES, UNDER, BELOW_BEAM, CONNECTS, DISTANCE_TO).
2. Candidates: substations sharing facets or ontology classes with the query (graph shortlist).
3. Vertex pairing: Hungarian on  sim = WuPalmer(class) x attribute similarity x geometric proximity
   (after the ALS alignment), scored as a soft Dice over the relevant candidate vertices.
4. Edge consistency: query edges whose paired endpoints carry the same relation, IDF-weighted by
   how rare the relation is in the corpus.
5. S_KG = nodes 0.30 + edges 0.25 + geom 0.30 + facets 0.15 (renormalised over present parts),
   times c_count = 2 min(nq, nr) / (nq + nr) when the query states a substation count.
Compatibility checks add CLEARS / CONFLICTS_WITH edges to the result graph.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..core import catalogue
from ..core.ontology import base_equipment_type, equipment_class, opening_class, wu_palmer
from ..geometry import compat
from ..geometry import polygons as P
from ..geometry.als import Item, RoomGeom, align, als
from ..retrieval.query import facets as query_facets
from ..retrieval.query import present, room_geom
from ..retrieval.scoring import Result, conflicts_of, facet_score, hard_facet_factor, idf, verdict
from .build import concept_vid, facet_vid

MAX_UNITS = 12
CIVIL_TO_CLASS = {"rolling_shutter": ("opening", "rolling_shutter"), "fire_exit": ("opening", "fire_exit"),
                  "ventilation": ("opening", "louvre"), "staircase": ("opening", "staircase"),
                  "cutout": ("opening", "cutout"), "beam": ("structure", "beam"), "trench": ("route", "trench"),
                  "door": ("opening", "door"), "platform": ("structure", "platform")}


class KGUnavailable(RuntimeError):
    pass


@dataclass
class QV:
    qid: str
    label: str
    cls: str
    attrs: dict = field(default_factory=dict)
    geom: object = None
    inferred: bool = False


def query_graph(q: dict, qg: RoomGeom, rules: dict) -> dict:
    V: list[QV] = [QV("Q", "substation", "substation", {"clear_height_mm": q.get("clear_height_mm"), "area_m2": q.get("area_m2")}, qg.outline)]
    E: list[tuple] = []
    n = 0
    for it in q.get("equipment_items") or []:
        cls = equipment_class(it.get("type"), None, it.get("dt_kind"))
        # catalogue dimensions only when the user fixed a size (kVA / ways / subtype): presence rule
        sized = bool(it.get("kva") or it.get("ways") or it.get("subtype"))
        row = (catalogue.lookup(cls, it.get("kva"), it.get("ways"), it.get("dt_kind"), it.get("subtype")) or {}) if sized else {}
        for _ in range(min(int(it.get("count") or 1), MAX_UNITS)):
            n += 1
            V.append(QV(f"E{n}", "equipment", cls, {"kva": it.get("kva"), "ways": it.get("ways"),
                                                     "L_mm": row.get("L_mm"), "B_mm": row.get("B_mm"), "H_mm": row.get("H_mm")}))
            E.append(("CONTAINS", "Q", f"E{n}", {}))
    geo_groups = {"door": "door", "column": "column", "beam": "beam", "cutout": "cutout", "trench": "trench"}
    for grp, items in qg.groups.items():
        if grp == "equipment":
            continue
        for it in items:
            n += 1
            label = {"door": "opening", "cutout": "opening", "column": "structure", "beam": "structure", "trench": "route"}.get(grp, "opening")
            V.append(QV(f"G{n}", label, it.cls if grp == "door" else geo_groups.get(grp, grp), dict(it.attrs), it.geom))
    civil = q.get("civil") or {}
    have = {v.cls for v in V}
    for k, val in civil.items():
        if k == "door_count":
            for _ in range(min(int(val), 6)):
                n += 1
                V.append(QV(f"C{n}", "opening", "door", {"width_mm": q.get("door_width_mm")}))
            continue
        if not val or k not in CIVIL_TO_CLASS:
            continue
        label, cls = CIVIL_TO_CLASS[k]
        if cls in have:
            continue
        n += 1
        V.append(QV(f"C{n}", label, cls, {"bottom_mm": q.get("beam_bottom_mm")} if cls == "beam" else
                    ({"width_mm": q.get("door_width_mm")} if cls == "door" else {})))
    if (q.get("site") or {}).get("trench") and "trench" not in {v.cls for v in V}:
        n += 1
        V.append(QV(f"C{n}", "route", "trench"))
    norms = rules["plot_clearance_mm"]
    for f in (q.get("site_context") or {}).get("features") or []:
        n += 1
        V.append(QV(f"F{n}", "site_feature", f.get("cls"), {"label": f.get("label")}, P.poly(f.get("polygon_mm")) or P.line(f.get("polyline_mm"))))
    for k, mm in (q.get("site_distances") or {}).items():
        n += 1
        V.append(QV(f"F{n}", "site_feature", k, {"distance_mm": mm, "meets_norm": mm >= norms.get(k, 0)}))
    # relations the requirement implies (weighted 0.5)
    eq = [v for v in V if v.label == "equipment"]
    by = lambda c: [v for v in V if v.cls == c]
    for t in [v for v in eq if base_equipment_type(v.cls) in ("transformer", "packaged")]:
        for d in by("rolling_shutter")[:1] or by("door")[:1]:
            E.append(("FACES", t.qid, d.qid, {"inferred": True}))
        for b in by("beam")[:1]:
            E.append(("BELOW_BEAM", t.qid, b.qid, {"inferred": True}))
    for c in by("cutout")[:1]:
        for p in [v for v in eq if v.cls in ("rmu", "ltp", "metering")][:2]:
            E.append(("UNDER", c.qid, p.qid, {"inferred": True}))
    for r in by("trench")[:1]:
        for p in [v for v in eq if v.cls in ("rmu", "ltp", "transformer", "transformer_dry", "transformer_oil")][:2]:
            E.append(("CONNECTS", r.qid, p.qid, {"inferred": True}))
    return {"vertices": V, "edges": E}


def query_graph_json(qgraph: dict) -> dict:
    """Serialisable query graph for the UI."""
    return {"vertices": [{"id": v.qid, "label": v.label, "cls": v.cls, "attrs": {k: x for k, x in v.attrs.items() if x is not None},
                          "has_geometry": v.geom is not None} for v in qgraph["vertices"]],
            "edges": [{"label": l, "out": a, "in": b, "props": p} for l, a, b, p in qgraph["edges"]]}


# ---------------------------------------------------------------- candidate side
def _cand(part: dict, sid: str):
    """Candidate vertices of one substation + its geometry, rebuilt from the graph partition."""
    V = {v["vid"]: v for v in part["vertices"]}
    sv = V[sid]
    prefix = sid + ":"
    owned = {vid: v for vid, v in V.items() if vid.startswith(prefix)}
    pk = sv["pk"]
    feats = {vid: v for vid, v in V.items() if v["label"] == "site_feature" and v["pk"] == pk}
    edges = [e for e in part["edges"]]
    rg = RoomGeom(outline=P.poly(sv["props"].get("polygon_mm")), clear_height_mm=sv["props"].get("clear_height_mm"),
                  beam_bottom_mm=sv["props"].get("beam_bottom_mm"), label=sv["props"].get("name", ""))
    if rg.outline is None and sv["props"].get("length_mm") and sv["props"].get("width_mm"):
        rg.outline = P.poly(P.rect(0, 0, sv["props"]["length_mm"], sv["props"]["width_mm"]))
    routes = []
    for vid, v in owned.items():
        p = v["props"]
        cls = p.get("cls") or ""
        g = P.poly(p.get("polygon_mm")) or P.line(p.get("polyline_mm"))
        if v["label"] == "equipment" and g is not None:
            rg.groups.setdefault("equipment", []).append(Item(cls, g, {"H_mm": p.get("H_mm"), "id": p.get("label"), "vid": vid}))
        elif v["label"] == "opening" and g is not None:
            grp = "door" if cls in ("door", "rolling_shutter", "fire_exit", "gate") else "louvre" if cls in ("louvre", "window") else \
                "cutout" if cls == "cutout" else None
            if grp:
                rg.groups.setdefault(grp, []).append(Item(cls, g, {"width_mm": p.get("width_mm"), "wall": p.get("wall"), "vid": vid}))
        elif v["label"] == "structure" and cls in ("beam", "column") and g is not None:
            rg.groups.setdefault(cls, []).append(Item(cls, g, {"bottom_mm": p.get("bottom_mm"), "vid": vid}))
        elif v["label"] == "route":
            routes.append({"type": p.get("type")})
            if g is not None:
                rg.groups.setdefault("cable_tray" if cls == "cable_tray" else "trench", []).append(Item(cls, g, {"vid": vid}))
    own_checks = [{"feature": V.get(e["in"], {}).get("props", {}).get("label") or e["in"], "distance_mm": e["props"].get("mm"),
                   "norm_mm": e["props"].get("norm_mm"), "meets_norm": e["props"].get("meets_norm")}
                  for e in edges if e["label"] == "DISTANCE_TO" and e["out"] == sid]
    facets: dict[str, list] = {}
    for e in edges:
        if e["label"] == "HAS_FACET" and e["out"] in (sid, pk):
            k, val = e["in"][2:].split("=", 1)
            facets.setdefault(k, []).append(val)
    facets["kva"] = [float(x) for x in facets.get("kva", [])]
    cand_vs = list(owned.values()) + list(feats.values())
    counts: dict[str, int] = {}
    for v in owned.values():
        if v["label"] == "equipment":
            b = base_equipment_type(v["props"].get("cls") or "")
            counts[b] = counts.get(b, 0) + 1
    return sv, cand_vs, rg, {"cable": {"routes": routes}, "counts": counts}, {"site": {"clearance_checks": own_checks}}, facets


def _attr_sim(qv: QV, cv: dict) -> float:
    p = cv["props"]
    s = 1.0
    if qv.attrs.get("kva") and p.get("kva"):
        s *= math.exp(-abs(math.log(qv.attrs["kva"] / float(p["kva"]))) / 0.3)
    if qv.attrs.get("L_mm") and p.get("L_mm") and p.get("B_mm"):
        qa = qv.attrs["L_mm"] * qv.attrs["B_mm"]
        ca = float(p["L_mm"]) * float(p["B_mm"])
        s *= max(0.3, math.exp(-abs(math.log(qa / ca)) / 0.3))
    if qv.attrs.get("width_mm") and p.get("width_mm"):
        s *= math.exp(-abs(math.log(qv.attrs["width_mm"] / float(p["width_mm"]))) / 0.25)
    return s


def match_substation(qgraph, q, qg, part, sid, deg, n_docs, edge_df, settings) -> Result:
    sc = settings.get("scoring")
    rules = settings.get("rules")
    sv, cvs, rg, sub_min, doc_min, cfacets = _cand(part, sid)
    pres = present(q)
    # geometry first: the pose is needed for geometric vertex proximity
    geo_needed = pres["footprint"] or pres["height"] or pres["structure"] or pres["cutouts"] or pres["site"] or qg.has("door")
    pose = align(qg, rg, sc.get("align_refine_deg", 10))
    checks, extra = compat.check_all(q, qg, rg, pose, sub_min, doc_min, rules)
    als_res = als(qg, rg, sc["als_weights"], sc.get("match_sigma_mm", 600), sc.get("area_log_tol", 0.3),
                  sc.get("iou_dilate_mm", 150), extra=extra, pose=pose) if geo_needed else None
    # ---- vertex pairing
    # site features describe the new plot: they feed the clearance checks, not vertex pairing
    qvs = [v for v in qgraph["vertices"] if v.qid != "Q" and v.label != "site_feature"]
    cvs = [c for c in cvs if c["label"] != "site_feature"]
    pairs_out = []
    node_score = None
    pair_map = {"Q": sid}
    if qvs:
        S = np.zeros((len(qvs), max(1, len(cvs))))
        for i, qv in enumerate(qvs):
            for j, cv in enumerate(cvs):
                ccls = cv["props"].get("cls") or ""
                t = wu_palmer(qv.cls, ccls)
                if t < 0.5:          # only same-kind vertices pair (transformer variants yes, transformer vs LTP no)
                    continue
                s = t * _attr_sim(qv, cv)
                if qv.geom is not None:
                    cg = P.poly(cv["props"].get("polygon_mm")) or P.line(cv["props"].get("polyline_mm"))
                    if cg is not None and cv["label"] != "site_feature":
                        d = qv.geom.centroid.distance(pose.apply(cg).centroid)
                        s *= math.exp(-d / (2 * sc.get("match_sigma_mm", 600)))
                S[i, j] = s
        if cvs:
            ri, ci = linear_sum_assignment(-S)
            matched = [(int(i), int(j), float(S[i, j])) for i, j in zip(ri, ci) if S[i, j] > 0.05]
        else:
            matched = []
        qcls = {v.cls for v in qvs}
        relevant = [cv for cv in cvs if max((wu_palmer(c, cv["props"].get("cls") or "") for c in qcls), default=0) >= 0.5]
        node_score = 2 * sum(m[2] for m in matched) / (len(qvs) + len(relevant)) if (qvs or relevant) else None
        # per-type count agreement: asking for 2 transformers and finding 1 must cost more than an extra louvre
        qn: dict[str, int] = {}
        for v in qvs:
            if v.label == "equipment":
                qn[base_equipment_type(v.cls)] = qn.get(base_equipment_type(v.cls), 0) + 1
        if qn and node_score is not None:
            cn: dict[str, int] = {}
            for cv in cvs:
                if cv["label"] == "equipment":
                    b = base_equipment_type(cv["props"].get("cls") or "")
                    cn[b] = cn.get(b, 0) + 1
            agree = [2 * min(n, cn.get(t, 0)) / (n + cn.get(t, 0)) for t, n in qn.items()]
            node_score *= sum(agree) / len(agree)
        for i, j, s in matched:
            qv, cv = qvs[i], cvs[j]
            pair_map[qv.qid] = cv["vid"]
            pairs_out.append({"query": qv.qid, "query_cls": qv.cls, "candidate": cv["vid"], "candidate_label": cv["props"].get("label"),
                              "candidate_cls": cv["props"].get("cls"), "sim": round(s, 3)})
    # ---- edge consistency
    edge_score = None
    scored_edges = [e for e in qgraph["edges"] if e[0] != "CONTAINS"]
    if scored_edges:
        cedges = {(e["label"], e["out"], e["in"]): e for e in part["edges"]}
        num = den = 0.0
        for label, a, b, props in scored_edges:
            w = idf(edge_df.get(label, 0), n_docs) * (0.5 if props.get("inferred") else 1.0)
            den += w
            ca, cb = pair_map.get(a), pair_map.get(b)
            if not ca or not cb:
                continue
            e = cedges.get((label, ca, cb)) or cedges.get((label, cb, ca))
            if e is None:
                continue
            if label == "DISTANCE_TO" and "meets_norm" in props:
                num += w * (1.0 if bool(e["props"].get("meets_norm")) == bool(props["meets_norm"]) else 0.3)
            else:
                num += w
        edge_score = num / den if den else None
    # ---- facets
    fscore, fhits = facet_score(q, cfacets, deg, n_docs)
    comps = {"nodes": node_score, "edges": edge_score, "geom": als_res.score if als_res else None, "facets": fscore}
    w = settings.get("engines.kg.weights")
    used = {k: v for k, v in comps.items() if v is not None}
    s_kg = sum(w[k] * v for k, v in used.items()) / sum(w[k] for k in used) if used else 0.0
    hard, hard_notes = hard_facet_factor(q, cfacets, sc.get("hard_facets"))
    s_kg *= hard
    md_count = None
    if q.get("substation_count"):
        nq, nr = int(q["substation_count"]), int(sv["props"].get("_doc_sub_count") or 1)
        md_count = 2 * min(nq, nr) / (nq + nr)
        s_kg *= md_count
    total_w = sum(w.values())
    coverage = sum(w[k] for k in used) / total_w
    if als_res:
        coverage = 0.5 * coverage + 0.5 * als_res.coverage
    conflicts = conflicts_of(checks)
    expl = [f"HAS_FACET {h}" for h in fhits] + hard_notes
    expl += [f"{p['query']} ({p['query_cls']}) -> {p['candidate_label'] or p['candidate']} ({p['candidate_cls']}) sim {p['sim']}" for p in pairs_out[:8]]
    comp_out = {k: v for k, v in used.items()}
    if als_res:
        comp_out.update({f"als.{k}": v for k, v in als_res.components.items()})
    if md_count is not None:
        comp_out["c_count"] = md_count
    if hard < 1:
        comp_out["hard_facets"] = hard
    return Result(engine="kg", doc_id=sv["pk"], substation_id=sid, file_name="", substation_name=sv["props"].get("name") or "",
                  score=float(s_kg), verdict=verdict(s_kg, conflicts, sc["verdict"]), coverage=float(coverage), components=comp_out,
                  geometry=als_res.as_dict() if als_res else {"pose": pose.as_dict()}, checks=checks, conflicts=conflicts,
                  explanation=expl, pairs=pairs_out)


class KGEngine:
    name = "kg"

    def __init__(self, store, index, settings):
        self.store = store
        self.index = index
        self.settings = settings
        self._cache_version = -1
        self._deg = {}
        self._edge_df = {}
        self._parts: dict[str, dict] = {}

    def _refresh(self):
        v = self.index.version()
        if v != self._cache_version:
            self._deg = self.store.facet_degrees()
            self._edge_df = self.store.edge_label_df()
            self._parts = {}
            self._cache_version = v

    def warm(self) -> int:
        """Preload every partition into the cache (called after start-up and after each new index version)."""
        self._refresh()
        pks = [d["doc_id"] for d in self.index.documents()]
        self._parts.update(self.store.partitions([p for p in pks if p not in self._parts]))
        return len(self._parts)

    def health(self) -> dict:
        t0 = time.time()
        ok = self.store.ping()
        return {"ok": ok, "backend": self.store.name, "latency_ms": round((time.time() - t0) * 1000, 1)}

    def search(self, q: dict, k: int = 10) -> tuple[list[Result], dict]:
        t0 = time.time()
        timeout = float(self.settings.get("engines.kg.timeout_s", 5.0))
        rules = self.settings.get("rules")
        self._refresh()
        qg = room_geom(q)
        qgraph = query_graph(q, qg, rules)
        info = {"query_graph": query_graph_json(qgraph),
                "typed_vertices": sum(1 for v in qgraph["vertices"] if v.qid != "Q" and v.label != "site_feature")}
        docs = {d["doc_id"]: d for d in self.index.documents()}
        n_docs = max(1, len(docs))
        fac = [facet_vid(kk, vv) for kk, vv in query_facets(q).items()] + [facet_vid("kva", int(x)) for x in q.get("kva") or []]
        cons = sorted({concept_vid(v.cls) for v in qgraph["vertices"] if v.qid != "Q"})
        try:
            hits = self.store.shortlist(fac, cons)
        except Exception as e:
            raise KGUnavailable(f"graph shortlist failed: {type(e).__name__}: {e}")
        if time.time() - t0 > timeout:
            raise KGUnavailable(f"graph shortlist took {time.time() - t0:.1f}s (> {timeout}s)")
        subs = self.store.substations()
        sub_count = {}
        for s in subs:
            sub_count[s["pk"]] = sub_count.get(s["pk"], 0) + 1
        limit = int(self.settings.get("engines.kg.max_candidates", 300))
        cand_ids = sorted(hits, key=lambda x: (-hits[x], x))[:limit]          # deterministic tie order
        if len(cand_ids) < limit:
            cand_ids += sorted(s["vid"] for s in subs if s["vid"] not in hits)[: limit - len(cand_ids)]
        missing = sorted({sid.split(":")[0] for sid in cand_ids} - set(self._parts))
        if missing:
            try:
                self._parts.update(self.store.partitions(missing))
            except Exception as e:
                raise KGUnavailable(f"graph partition fetch failed: {type(e).__name__}: {e}")
        results = []
        for sid in cand_ids:
            pk = sid.split(":")[0]
            if pk not in docs:
                continue
            if pk not in self._parts:
                self._parts[pk] = self.store.partition(pk)
            part = self._parts[pk]
            sv = next((v for v in part["vertices"] if v["vid"] == sid), None)
            if sv is None:
                continue
            sv["props"]["_doc_sub_count"] = sub_count.get(pk, 1)
            r = match_substation(qgraph, q, qg, part, sid, self._deg, n_docs, self._edge_df, self.settings)
            r.file_name = docs[pk]["file_name"]
            r.status = docs[pk]["status"]
            results.append(r)
            if time.time() - t0 > timeout * 4:
                info["truncated"] = True
                break
        # best substation per document
        best: dict[str, Result] = {}
        for r in results:
            if r.doc_id not in best or r.score > best[r.doc_id].score:
                best[r.doc_id] = r
        out = sorted(best.values(), key=lambda r: -r.score)[:k]
        info["candidates"] = len(results)
        info["elapsed_ms"] = round((time.time() - t0) * 1000)
        return out, info
