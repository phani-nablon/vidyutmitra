"""Plain-language explanations for the UI: ontology classes, their fields, and the type of every extracted value.

Value types (what an extracted value *is*): string, integer, number, boolean, enum, point, polygon, polyline, bbox,
mask, list, object. `vtype(name, value)` names the type of a stored value so the Review tab can show it next to
the value; the Ontology tab shows the same types for each class field.
"""
from __future__ import annotations

from typing import Any

from .ontology import OClass, family_of

TYPES: dict[str, str] = {
    "string": "Text read from the drawing, such as a label or a note.",
    "integer": "Whole number, such as a kVA rating or a count.",
    "number": "Decimal number with a unit (mm, m, m², kg).",
    "boolean": "Yes / no.",
    "enum": "One value from a fixed set, for example wall = N, S, E or W.",
    "point": "One position, [x, y].",
    "polygon": "Closed outline: a list of [x, y] points.",
    "polyline": "Open line: a list of [x, y] points (trench, road, wall).",
    "bbox": "Rectangle, [x0, y0, x1, y1].",
    "mask": "Painted region of one class on the drawing. Stored as polygons in page pixels and rasterised on request.",
    "list": "A list of values.",
    "object": "A group of named values.",
}

# field name -> (type, unit, meaning)
FIELDS: dict[str, tuple[str, str, str]] = {
    "polygon_mm": ("polygon", "mm", "Outline in millimetres. Room frame (0,0 = room top-left corner) for things inside a substation, site frame for plot features."),
    "polyline_mm": ("polyline", "mm", "Centre line in millimetres (site or room frame)."),
    "polygon_px": ("polygon", "px", "Outline in pixels of the original page image. Used to draw it on the drawing."),
    "polyline_px": ("polyline", "px", "Line in pixels of the original page image."),
    "bbox_px": ("bbox", "px", "Rectangle in pixels of the original page image: where the item was read."),
    "area_m2": ("number", "m²", "Floor area of the room."),
    "distance_mm": ("number", "mm", "Shortest distance from the proposed substation to the feature."),
    "width_m": ("number", "m", "Width of a road or route in metres."),
    "position": ("point", "mm", "Single position, for items drawn as a symbol."),
    "centre_mm": ("point", "mm", "Centre of the item in the room frame."),
    "kva": ("integer", "kVA", "Rating printed on the drawing. If it is missing and only one catalogue rating fits the footprint, it is inferred and marked as catalogue."),
    "kva_source": ("enum", "", "Where the rating came from: drawing_text or catalogue_unique."),
    "L_mm": ("number", "mm", "Length of the footprint (long side)."),
    "B_mm": ("number", "mm", "Breadth of the footprint (short side)."),
    "H_mm": ("number", "mm", "Height of the unit."),
    "weight_kg": ("number", "kg", "Weight printed on the drawing."),
    "ways": ("integer", "", "Number of switch or fuse positions, for example 3 for a 3-way RMU."),
    "clear_height_mm": ("number", "mm", "Usable height from finished floor to the underside of slab or beam, read from the section."),
    "slab_to_soffit_mm": ("number", "mm", "Height from slab to the underside of the slab above."),
    "beam_bottom_mm": ("number", "mm", "Level of the beam underside above finished floor."),
    "bottom_mm": ("number", "mm", "Level of the underside above finished floor."),
    "plinth_mm": ("number", "mm", "Height of the raised plinth under the equipment."),
    "trench_depth_mm": ("number", "mm", "Depth of the cable trench."),
    "ceiling_clearance_mm": ("number", "mm", "Gap between the top of the equipment and the ceiling or beam above it."),
    "length_mm": ("number", "mm", "Room length."),
    "width_mm": ("number", "mm", "Width of the item (room, door, louvre, route)."),
    "height_mm": ("number", "mm", "Height of the item."),
    "depth_mm": ("number", "mm", "Depth of the item (beam depth, trench depth)."),
    "dia_mm": ("number", "mm", "Pipe diameter."),
    "count": ("integer", "", "How many."),
    "floor": ("string", "", "Floor the substation sits on, for example ground or stilt."),
    "shape": ("string", "", "Description of the room shape, for example rectangular or chamfered."),
    "wall": ("enum", "", "Wall the opening sits on: N, S, E or W. N is the top of the plan."),
    "type": ("enum", "", "Kind of item, from a fixed list in the schema."),
    "kind": ("enum", "", "HT, LT, vehicle or staff."),
    "label": ("string", "", "Text written next to the item on the drawing."),
    "name": ("string", "", "Name of the substation as drawn."),
    "note": ("string", "", "Free text note read from the drawing."),
    "subtype": ("string", "", "Detail such as the rating and cooling written on the drawing."),
    "equipment_type": ("enum", "", "Normalised equipment type: transformer, rmu, ltp, metering and so on."),
    "ontology_class": ("enum", "", "Ontology class the item was typed as."),
    "cls": ("enum", "", "Ontology class or mask class."),
    "height_source": ("string", "", "Where the clear height was read from, for example the section view."),
    "mm_per_px": ("number", "mm/px", "Drawing scale: millimetres in the real world per page pixel."),
    "catalogue": ("object", "", "Catalogue match: candidate ratings for this footprint and whether one is unique."),
    "platform": ("object", "", "Raised platform: type text and height."),
    "meets_norm": ("boolean", "", "True when the distance is at or above the clearance norm."),
    "norm_mm": ("number", "mm", "Minimum distance required by the clearance rules."),
    "mm": ("number", "mm", "Measured distance."),
    "relation": ("enum", "", "How the two items are arranged, for example side by side."),
}

# keyword in an ontology `uses` entry -> what that score group does
USES: dict[str, str] = {
    "geometry": "Room outline and size comparison between a requirement and a historic design.",
    "site": "Site context: what is around the substation.",
    "plot clearance": "Distance from the proposed substation to this feature, checked against the clearance norm shown in brackets.",
    "equipment": "Pairing of equipment between a requirement and a historic design (type, count, rating).",
    "fit": "Whether the equipment footprint fits in the room.",
    "height": "Clear height and beam-bottom check against equipment height.",
    "structure": "Beams and columns that limit where equipment can go.",
    "doors": "Match of doors and shutters (count, width, wall).",
    "louvres": "Match of ventilation openings.",
    "cutouts": "Slab cutouts used for cable entry.",
    "access": "Access routes and stairs.",
    "trench": "Cable trench and entry routing.",
}

FAMILY_HOW: dict[str, str] = {
    "site": "Read from the site plan layout. Polygons usually come from the legend colours on the plan and are stored as masks.",
    "electrical": "Read from the equipment layout: each box and its label. The rating comes from the drawing text or the equipment catalogue; the footprint is converted from pixels to mm with the calibrated drawing scale.",
    "civil": "Room outline from the equipment layout; heights from the dimensions on the section view; beams and cutouts from their own layouts.",
    "access": "Doors and louvres are boxes on the room walls, with width and height from the dimension text. The wall (N, S, E, W) is worked out from the position in the room.",
    "cable": "Trenches and routes are colour-coded lines on the layout; widths and depths come from dimension text.",
}

HELP: dict[str, str] = {
    "css": "Consumer substation (CSS): the HT/LT substation a consumer builds inside their premises to the utility's layout rules. Every drawing in the library is one or more CSS designs.",
    "site": "Everything outside the substation room: plot, roads, water bodies, trees, walls and neighbouring buildings. These drive the plot clearance checks.",
    "site_boundary": "The proposed substation area marked on the site plan (red). Every plot clearance is measured from this outline.",
    "nalla": "Open drain or water body near the plot. The substation must keep a minimum distance from it.",
    "road": "A road next to the plot. DP road and internal road have different clearances.",
    "dp_road": "Road shown in the Development Plan, normally the public road the plot fronts onto.",
    "internal_road": "Road inside the compound.",
    "tree": "A tree on the plot. The clearance is larger when the tree is near a door.",
    "compound_wall": "Boundary wall of the plot.",
    "structure": "A neighbouring building or structure.",
    "electrical": "Equipment inside the substation room: transformers, HT switchgear, LT panels and auxiliaries. Each is drawn as a footprint box with a label and, usually, a rating or size.",
    "transformer": "Distribution transformer that steps the HT supply (11 kV or 33 kV) down to the LT supply. Its kVA rating is read from the drawing text, or inferred from the catalogue when only one rating fits the footprint.",
    "transformer_dry": "Dry-type transformer: air-cooled, no oil. The usual choice for indoor rooms.",
    "transformer_oil": "Oil or ester filled transformer. Needs oil containment and more fire separation, so it is mostly outdoors or in its own bay.",
    "switchgear": "HT switching equipment on the incoming supply.",
    "rmu": "Ring main unit: compact HT switchgear that connects the substation to the utility's HT ring. 'ways' is the number of switch or fuse positions, for example 3.",
    "metering": "Metering cubicle with the current and potential transformers (CT/PT) used for HT metering.",
    "ht_switchgear": "Other HT panels, including gas-insulated switchgear (GIS).",
    "ltp": "LT panel or feeder pillar: the low-voltage board that takes the transformer output to the consumer's loads.",
    "packaged": "Packaged or unitised substation: transformer, HT and LT gear delivered as one enclosed unit.",
    "aux": "Auxiliaries: ACDB, battery and DC supply, FRTU / RTU for remote control, APFC for power-factor correction.",
    "civil": "The building envelope of the substation: room, beams, columns, platform and slab openings. Drives the room size, clear height and structure checks.",
    "substation": "The substation room as drawn on the equipment layout: outline, area, clear height and floor. Everything else in the room attaches to it.",
    "beam": "A beam above the room. Its underside limits the usable height.",
    "column": "A structural column inside or touching the room.",
    "platform": "Raised floor or plinth under the equipment, with its height.",
    "cutout": "Opening in the floor slab for cables to pass through.",
    "staircase": "Staircase serving the substation level.",
    "access": "How people, equipment and air get in and out: doors, shutters, louvres and access paths.",
    "door": "Door in a room wall, with width, height and the wall it is on.",
    "rolling_shutter": "Rolling shutter used as the large access opening for equipment.",
    "fire_exit": "Fire exit or fire-rated door.",
    "gate": "Gate in the compound or at the substation entrance.",
    "ventilation": "Ventilation opening in a wall.",
    "louvre": "Louvre: a ventilated opening in a wall, with its size from the dimension text.",
    "window": "Window opening in a wall.",
    "wayleave": "24x7 access path to the substation for utility staff and vehicles, with a minimum width.",
    "cable": "Cable entry and routing into and inside the substation.",
    "trench": "Cable trench in the floor or outside, tagged HT or LT, with width and depth.",
    "hdpe": "HDPE pipes that carry cables into the substation: how many and what diameter.",
    "cable_tray": "Cable tray carrying cables along a wall or ceiling.",
    "cable_entry": "Place where cables come into the room: riser or entry point.",
}


def vtype(name: str, value: Any) -> tuple[str, str]:
    """(type, unit) of an extracted value, from its field name first and its Python shape second."""
    if name in FIELDS:
        t, u, _ = FIELDS[name]
        if t != "object" or isinstance(value, dict):
            return t, u
    n = name.lower()
    unit = "px" if n.endswith("_px") else "mm" if n.endswith("_mm") else "m²" if n.endswith("_m2") else "m" if n.endswith("_m") \
        else "kg" if n.endswith("_kg") else ""
    if isinstance(value, bool):
        return "boolean", ""
    if isinstance(value, int):
        return "integer", unit
    if isinstance(value, float):
        return "number", unit
    if isinstance(value, dict):
        return "object", ""
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(p, (list, tuple)) for p in value):
            return ("polygon" if "polygon" in n else "polyline" if "polyline" in n or "line" in n else "list"), unit
        if len(value) == 4 and all(isinstance(p, (int, float)) for p in value) and "bbox" in n:
            return "bbox", unit
        if len(value) == 2 and all(isinstance(p, (int, float)) for p in value):
            return "point", unit
        return "list", unit
    return "string", unit


def class_info(c: OClass, depth: int, instances: int = 0) -> dict:
    """Everything the Ontology tab shows for one class."""
    fam = family_of(c.id) or (c.id if depth == 2 else None)
    uses = []
    for part in [u.strip() for u in (c.uses or "").split(",") if u.strip()]:
        key = next((k for k in USES if part.lower().startswith(k)), None)
        uses.append({"key": part, "desc": USES.get(key, "")})
    fields = []
    for f in c.fields:
        t, u, d = FIELDS.get(f, ("string", "", ""))
        fields.append({"name": f, "type": t, "unit": u, "desc": d})
    return {"id": c.id, "label": c.label, "family": fam, "vertex": c.vertex or "",
            "what": HELP.get(c.id) or c.desc, "how": FAMILY_HOW.get(fam or "", ""), "fields": fields, "uses": uses,
            "children": [x.label for x in c.children], "instances": instances}
