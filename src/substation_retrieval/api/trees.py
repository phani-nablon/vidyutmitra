"""Tree views for the UI (the original 'Design graph' layout): a site's knowledge-graph partition and the
ontology, each as a nested {id, label, family, vid, children} tree laid out left to right."""
from __future__ import annotations

from collections import defaultdict

from ..core.ontology import ONTOLOGY, OClass, family_of, index

LEAF_CAP = 40


def _n(id_, label, family=None, vid=None, children=None, group=False, collapsed=False, detail=None):
    return {"id": id_, "label": label, "family": family, "vid": vid, "children": children or [], "group": group,
            "collapsed": collapsed, "detail": detail}


def _fmt_mm(v):
    return f"{v / 1000:.2f} m" if isinstance(v, (int, float)) else "–"


def _dims(p):
    vals = [p.get("L_mm"), p.get("B_mm"), p.get("H_mm")]
    if not any(vals):
        return ""
    return " · " + "×".join(str(round(v)) if isinstance(v, (int, float)) else "–" for v in vals)


def _cls_label(cls: str) -> str:
    node = index().get(cls)
    return node[0].label if node else (cls or "other").replace("_", " ")


def ontology_tree(counts: dict[str, int]) -> dict:
    def walk(c: OClass, depth: int) -> dict:
        n = counts.get(c.id, 0)
        label = f"{c.label}" + (f" ({n})" if n else "")
        detail = {"class": c.id, "kg vertex": c.vertex or "-", "fields": ", ".join(c.fields) or "-", "used by": c.uses or "-",
                  "description": c.desc or "-", "instances": n}
        return _n(f"o:{c.id}", label, family_of(c.id) or (c.id if depth == 2 else None), None,
                  [walk(ch, depth + 1) for ch in c.children], group=bool(c.children), detail=detail)
    return walk(ONTOLOGY, 1)


def design_tree(part: dict, doc_name: str) -> dict:
    V = {v["vid"]: v for v in part["vertices"]}
    out = defaultdict(list)
    for e in part["edges"]:
        out[e["out"]].append(e)
    doc = next((v for v in part["vertices"] if v["label"] == "document"), None)
    if doc is None:
        return _n("empty", "not in graph")
    did = doc["vid"]
    P = lambda vid: V.get(vid, {}).get("props", {})  # noqa: E731
    name = lambda vid: P(vid).get("label") or P(vid).get("name") or P(vid).get("type") or vid.split(":")[-1]  # noqa: E731

    facets = [_n(e["in"], f'{P(e["in"]).get("key")}: {P(e["in"]).get("value")}', vid=e["in"])
              for e in out[did] if e["label"] == "HAS_FACET"]
    kids = []
    dp = doc["props"]
    kids.append(_n(f"{did}:meta", "Metadata", "site", collapsed=True, children=facets + [
        _n(f"{did}:status", f'quality {dp.get("quality")} · {dp.get("status")} · {dp.get("method")}', vid=did)], group=True))

    # site features by class + clearance checks
    feats = defaultdict(list)
    for e in out[did]:
        if e["label"] == "HAS_FEATURE":
            p = P(e["in"])
            feats[p.get("cls") or "other"].append(_n(e["in"], str(name(e["in"]))[:60], "site", e["in"]))
    site_kids = [_n(f"{did}:F:{c}", f"{_cls_label(c)} ({len(v)})", "site", children=v[:LEAF_CAP], group=True, collapsed=True)
                 for c, v in sorted(feats.items())]
    if site_kids:
        kids.append(_n(f"{did}:site", f"Site plan ({sum(len(v) for v in feats.values())} features)", "site", children=site_kids, group=True))

    lays = [e["in"] for e in out[did] if e["label"] == "HAS_LAYOUT"]
    if lays:
        kids.append(_n(f"{did}:L", f"Layouts ({len(lays)})", None, children=[
            _n(l, f'{P(l).get("type")} · {str(P(l).get("title") or "")[:40]}', vid=l) for l in lays[:LEAF_CAP]],
            group=True, collapsed=True))

    for e in out[did]:
        if e["label"] != "HAS_SUBSTATION":
            continue
        sid = e["in"]
        sp = P(sid)
        summary = (f'{_fmt_mm(sp.get("length_mm"))} × {_fmt_mm(sp.get("width_mm"))} · {sp.get("area_m2") or "–"} m² · '
                   f'clear h {sp.get("clear_height_mm") or "–"} · {sp.get("shape") or ""}')
        groups: dict[str, list] = defaultdict(list)
        rel = []
        for e2 in out[sid]:
            t = e2["in"]
            tv = V.get(t)
            if e2["label"] == "CONTAINS" and tv:
                groups[tv["label"]].append(t)
            elif e2["label"] == "HAS_FACET":
                pass
            elif e2["label"] == "SIMILAR_TO":
                rel.append(_n(f"{sid}>{t}", f'similar to {t.split(":")[0][:10]}… {e2["props"].get("score")}', vid=t))
            elif e2["label"] == "DISTANCE_TO":
                ok = e2["props"].get("meets_norm")
                rel.append(_n(f"{sid}>{t}", f'{name(t)}: {e2["props"].get("mm")} mm (norm {e2["props"].get("norm_mm")}) {"ok" if ok else "VIOLATED"}',
                              "site", t))
        openings = [x["out"] for x in part["edges"] if x["label"] == "ON_WALL" and x["in"] == sid]
        s_kids = [_n(f"{sid}:sum", summary, "civil", sid)]
        eq_by = defaultdict(list)
        for v in groups.get("equipment", []):
            p = P(v)
            kva = f' · {p["kva"]} kVA' + (" (catalogue)" if p.get("kva_source") == "catalogue_unique" else "") if p.get("kva") else ""
            eq_by[p.get("cls") or "aux"].append(_n(v, f'{name(v)}{kva}{_dims(p)}', "electrical", v))
        if eq_by:
            s_kids.append(_n(f"{sid}:E", f"Electrical equipment ({sum(map(len, eq_by.values()))})", "electrical", group=True, children=[
                _n(f"{sid}:E:{c}", f"{_cls_label(c)} ({len(v)})", "electrical", children=v, group=True) for c, v in sorted(eq_by.items())]))
        ops = [_n(v, f'{P(v).get("type")} · {str(P(v).get("label") or "")[:32]}'
                     + (f' · {round(P(v)["width_mm"])} mm' if P(v).get("width_mm") else "")
                     + (f' · wall {P(v)["wall"]}' if P(v).get("wall") else ""), "access", v)
               for v in openings + [x for x in groups.get("opening", [])]]
        if ops:
            s_kids.append(_n(f"{sid}:O", f"Openings & cutouts ({len(ops)})", "access", children=ops[:LEAF_CAP], group=True, collapsed=len(ops) > 8))
        st = [_n(v, f'{P(v).get("type")} · {str(P(v).get("label") or "")[:32]}'
                    + (f' · bottom {P(v)["bottom_mm"]}' if P(v).get("bottom_mm") else "")
                    + (f' · {P(v)["height_mm"]} mm' if P(v).get("height_mm") else ""), "civil", v) for v in groups.get("structure", [])]
        if st:
            s_kids.append(_n(f"{sid}:T", f"Structure ({len(st)})", "civil", children=st, group=True))
        rt = [_n(v, f'{P(v).get("type")} · {P(v).get("kind") or ""} {str(P(v).get("label") or "")[:30]}', "cable", v) for v in groups.get("route", [])]
        if rt:
            s_kids.append(_n(f"{sid}:R", f"Cable routes ({len(rt)})", "cable", children=rt, group=True))
        rels = []
        ids = {x for g in groups.values() for x in g} | set(openings)
        for x in part["edges"]:
            if x["out"] in ids and x["label"] in ("FACES", "BELOW_BEAM", "UNDER", "CONNECTS", "CLEARANCE"):
                pr = x["props"]
                extra = f' · {pr.get("mm") or pr.get("distance_mm") or pr.get("clearance_mm")} mm' if (pr.get("mm") or pr.get("distance_mm") or pr.get("clearance_mm")) is not None else ""
                rels.append(_n(f'{x["out"]}>{x["label"]}>{x["in"]}', f'{name(x["out"])} {x["label"]} {name(x["in"])}{extra}', None, x["out"]))
        if rels:
            s_kids.append(_n(f"{sid}:X", f"Relations ({len(rels)})", None, children=rels[:LEAF_CAP], group=True, collapsed=True))
        if rel:
            s_kids.append(_n(f"{sid}:S", f"Context ({len(rel)})", "site", children=rel, group=True, collapsed=len(rel) > 4))
        kids.append(_n(sid, f'Substation · {sp.get("name") or ""}' + (f' · {sp["floor"]}' if sp.get("floor") else ""), "civil", sid, s_kids, group=True))
    return _n(did, doc_name, None, did, kids, group=True)
