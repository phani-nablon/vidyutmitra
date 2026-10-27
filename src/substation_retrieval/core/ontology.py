"""CSS layout ontology: the class tree that types every knowledge-graph vertex.

5 families -> classes (-> sub-classes). Each class names the KG vertex label it types, the fields it
carries and which score group consumes it. The KG matcher uses `wu_palmer(a, b)` as the type
similarity between a query vertex and a historic vertex, so a "dry transformer" query still pairs
with an "oil transformer" vertex at reduced similarity, while a transformer never pairs with a door.

Out of scope by decision: busbar, fault level, floor loading, interconnections, SLD.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class OClass:
    id: str
    label: str
    vertex: str = ""                 # KG vertex label instances carry
    fields: tuple = ()
    uses: str = ""
    desc: str = ""
    color: str = ""
    children: list["OClass"] = field(default_factory=list)


def C(id, label, vertex="", fields=(), uses="", desc="", children=(), color=""):
    return OClass(id, label, vertex, tuple(fields), uses, desc, color, list(children))


ONTOLOGY = C("css", "CSS layout", desc="Consumer substation designs (AEML) and the requirement graph matched against them.", children=[
    C("site", "Site / plot", color="#0EA5B7", desc="Where the substation sits and what is around it.", children=[
        C("site_boundary", "Proposed substation area", "site_feature", ["polygon_mm", "area_m2"], "geometry, site"),
        C("nalla", "Nalla / water body", "site_feature", ["polyline_mm", "distance_mm"], "plot clearance (6 m)"),
        C("road", "Road", "site_feature", ["polyline_mm", "width_m"], "plot clearance", children=[
            C("dp_road", "DP road", "site_feature", ["width_m", "distance_mm"], "plot clearance (4.5 m)"),
            C("internal_road", "Internal road", "site_feature", ["distance_mm"], "plot clearance (1.5 m)"),
        ]),
        C("tree", "Tree", "site_feature", ["position", "distance_mm"], "plot clearance (1.5 m, 4.5 m at doors)"),
        C("compound_wall", "Compound wall", "site_feature", ["polyline_mm", "distance_mm"], "plot clearance (1.5 m)"),
        C("structure", "Adjacent structure", "site_feature", ["polygon_mm", "distance_mm"], "plot clearance (6 m)"),
    ]),
    C("equipment", "Equipment", color="#2563EB", desc="Electrical equipment placed in the substation.", children=[
        C("transformer", "Distribution transformer", "equipment", ["kva", "L_mm", "B_mm", "H_mm", "weight_kg"], "equipment, fit", children=[
            C("transformer_dry", "Dry-type transformer", "equipment"),
            C("transformer_oil", "Oil / ester transformer", "equipment"),
        ]),
        C("switchgear", "HT switchgear", "equipment", children=[
            C("rmu", "Ring main unit", "equipment", ["ways", "L_mm", "B_mm", "H_mm"], "equipment, fit"),
            C("metering", "Metering cubicle / CTPT", "equipment", ["L_mm", "B_mm"], "equipment"),
            C("ht_switchgear", "HT panel / GIS", "equipment", ["L_mm", "B_mm"], "equipment"),
        ]),
        C("ltp", "LT panel / feeder pillar", "equipment", ["ways", "L_mm", "B_mm", "H_mm"], "equipment, fit"),
        C("packaged", "Packaged / unitised substation", "equipment", ["kva", "L_mm", "B_mm"], "equipment, fit"),
        C("aux", "Auxiliaries (ACDB, battery, FRTU, APFC)", "equipment", ["L_mm", "B_mm"], "equipment"),
    ]),
    C("civil", "Civil / structure", color="#7C3AED", desc="Room envelope and structural members.", children=[
        C("substation", "Substation (room)", "substation", ["polygon_mm", "area_m2", "clear_height_mm", "floor"], "geometry, height"),
        C("beam", "Beam", "structure", ["polygon_mm", "bottom_mm"], "structure, height"),
        C("column", "Column", "structure", ["polygon_mm"], "structure"),
        C("platform", "Platform / plinth", "structure", ["height_mm", "type"], "height"),
        C("cutout", "Slab cutout", "opening", ["polygon_mm"], "cutouts"),
        C("staircase", "Staircase", "opening", ["polygon_mm"], "access"),
    ]),
    C("access", "Access & ventilation", color="#EA8A0C", desc="How people, equipment and air get in and out.", children=[
        C("door", "Door", "opening", ["width_mm", "height_mm", "wall"], "doors", children=[
            C("rolling_shutter", "Rolling shutter", "opening", ["width_mm", "height_mm", "wall"], "doors"),
            C("fire_exit", "Fire exit / FRD", "opening", ["width_mm", "wall"], "doors"),
            C("gate", "Gate", "opening", ["width_mm"], "doors"),
        ]),
        C("ventilation", "Ventilation", "opening", ["width_mm", "height_mm", "wall"], "louvres", children=[
            C("louvre", "Louvre", "opening", ["width_mm", "height_mm"], "louvres"),
            C("window", "Window", "opening", ["width_mm"], "louvres"),
        ]),
        C("wayleave", "Wayleave / 24x7 access path", "route", ["polygon_mm", "width_mm"], "site"),
    ]),
    C("cable", "Cable routes", color="#E11D48", desc="Cable entry and routing.", children=[
        C("trench", "Cable trench", "route", ["polyline_mm", "width_mm", "depth_mm", "kind"], "trench", children=[
            C("hdpe", "HDPE pipe entry", "route", ["count", "dia_mm"], "trench"),
        ]),
        C("cable_tray", "Cable tray", "route", ["width_mm", "polyline_mm"], "trench, height"),
        C("cable_entry", "Cable entry / riser", "opening", ["position"], "trench"),
    ]),
])


def walk(node: OClass = ONTOLOGY, depth: int = 1, parent: str | None = None):
    yield node, depth, parent
    for c in node.children:
        yield from walk(c, depth + 1, node.id)


@lru_cache(maxsize=1)
def index() -> dict[str, tuple[OClass, int, str | None]]:
    return {n.id: (n, d, p) for n, d, p in walk()}


def family_of(cid: str) -> str | None:
    idx = index()
    cur = cid
    while cur in idx:
        _, d, p = idx[cur]
        if d == 2:
            return cur
        cur = p
    return None


def ancestors(cid: str) -> list[str]:
    idx = index()
    out = []
    cur = cid
    while cur in idx:
        out.append(cur)
        cur = idx[cur][2]
    return out


def wu_palmer(a: str, b: str) -> float:
    """2*depth(lcs) / (depth(a)+depth(b)); 1.0 for identical classes, 0 for unknown."""
    idx = index()
    if a not in idx or b not in idx:
        return 0.0
    if a == b:
        return 1.0
    anc_a = ancestors(a)
    anc_b = set(ancestors(b))
    lcs = next((x for x in anc_a if x in anc_b), "css")
    da, db, dl = idx[a][1], idx[b][1], idx[lcs][1]
    # same family but different leaf -> moderate; cross-family -> ~0.2-0.4. Square to sharpen.
    return (2.0 * dl / (da + db)) ** 2 if lcs != "css" else 0.0


def leaf_classes() -> list[str]:
    return [n.id for n, d, _ in walk() if d >= 3]


def to_tree(node: OClass = ONTOLOGY, stats: dict | None = None) -> dict:
    out = {"id": node.id, "label": node.label, "vertex": node.vertex, "fields": list(node.fields),
           "uses": node.uses, "desc": node.desc, "color": node.color or None,
           "children": [to_tree(c, stats) for c in node.children]}
    if stats is not None:
        out["count"] = stats.get(node.id, 0)
    return out


# ------------------------------------------------------------ classification of extracted items
EQUIP_ALIASES = {
    "transformer": "transformer", "dt": "transformer", "dtr": "transformer",
    "rmu": "rmu", "metering": "metering", "ctpt": "metering",
    "ltp": "ltp", "lt_panel": "ltp", "feeder_pillar": "ltp", "icog": "ltp", "ltpcc": "ltp",
    "ht_switchgear": "ht_switchgear", "gis": "ht_switchgear", "vcb": "ht_switchgear",
    "packaged_ss": "packaged", "unitised_css": "packaged", "packaged": "packaged", "pss": "packaged",
    "acdb": "aux", "dcdb": "aux", "battery": "aux", "frtu": "aux", "rtu": "aux", "apfc": "aux", "scada": "aux", "aux": "aux",
}


def equipment_class(equipment_type: str | None, label: str | None = None, subtype: str | None = None) -> str:
    t = (equipment_type or "").lower().strip()
    cls = EQUIP_ALIASES.get(t)
    if cls is None:
        s = f"{t} {label or ''}".lower()
        for k, v in EQUIP_ALIASES.items():
            if re.search(rf"\b{k}\b", s):
                cls = v
                break
    cls = cls or "aux"
    if cls == "transformer":
        s = f"{subtype or ''} {label or ''}".lower()
        if "dry" in s:
            return "transformer_dry"
        if "oil" in s or "ester" in s:
            return "transformer_oil"
    return cls


OPENING_ALIASES = {
    "door": "door", "rolling_shutter": "rolling_shutter", "shutter": "rolling_shutter", "fire_exit": "fire_exit",
    "frd": "fire_exit", "gate": "gate", "ventilation": "louvre", "louvre": "louvre", "louver": "louvre",
    "window": "window", "cutout": "cutout", "staircase": "staircase", "cable_riser": "cable_entry",
    "shaft": "cable_entry", "flood_gate": "door", "fence": "compound_wall",
}


def opening_class(t: str | None) -> str | None:
    return OPENING_ALIASES.get((t or "").lower().strip())


def base_equipment_type(cls: str) -> str:
    """Collapse sub-classes to the counting type used in requirements (transformer_dry -> transformer)."""
    return "transformer" if cls.startswith("transformer") else cls
