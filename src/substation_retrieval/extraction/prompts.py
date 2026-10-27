"""Prompts for the two LLM passes. Coordinates are always normalised 0..1000 to the image sent
(x right, y down) so they survive any render resolution."""

SYSTEM = (
    "You read AEML consumer-substation (CSS) civil/electrical drawings for Adani Electricity Mumbai. "
    "You return strict JSON only. Never invent values: use null when a value is not printed or not visible. "
    "Copy labels exactly as written on the drawing. Dimensions are in millimetres unless the drawing says metres. "
    "Out of scope (do not extract): busbar, fault level, floor loading, interconnections, single-line diagrams."
)

PASS1 = """Pass 1 - whole sheet. Identify every drawing panel (layout) on this page and the sheet-level information.

Return JSON:
{
 "layouts": [ {"id": "L1", "bbox": [x0,y0,x1,y1], "type": one of
     "equipment_layout" (plan with equipment placed in the substation room),
     "section" (vertical section / elevation with heights), "site_plan" (plot / key plan with the proposed CSS marked),
     "css_layout" (civil plan of the room without equipment), "beam_layout", "cutout_layout", "cable_tray_layout",
     "trench_layout", "ms_plate_layout", "wayleave", "detail", "legend", "title_block", "notes", "table", "unknown",
   "title": "text as written", "floor": "basement|stilt|podium|ground|first|second|terrace|null",
   "scale_text": "e.g. 1:100 or null", "section_markers": ["A-A", ...] (cut lines drawn on a plan, or the name of a section)} ],
 "title_block": {"project": , "drawing_no": , "revision": , "date": , "consultant": , "client": },
 "legend": [ {"text": "as written", "kind": "fill|line|hatch|symbol", "color_name": "red|yellow|green|blue|orange|magenta|black|...",
              "semantic": "site_boundary|access_path|wayleave|trench|cable_tray|cutout|beam|column|ms_plate|earthing|nalla|road|tree|other"} ],
 "metadata": {"installation": "indoor|open_to_sky|semi_open|null", "arrangement": "centralized|distributed|null",
              "context": "in_building|standalone_building|outdoor_yard|temporary|null",
              "floor": "basement|stilt|podium|ground|first|second|terrace|null", "location_in_building": "short phrase",
              "voltage_kv": 11 or 33 or null, "substation_count": integer, "multiple_substations": true|false,
              "evidence": "one sentence"},
 "substations": [ {"name": "CSS-1", "equipment_layout": "L?", "section_layouts": ["L?"], "site_plan_layout": "L?|null"} ]
}
Bounding boxes cover the whole panel including its title. "Distributed" means the CSS equipment is split over
separate rooms/floors; give one substations[] entry per physically separate substation room."""

PASS2_PLAN = """Pass 2 - one equipment / civil plan crop (layout "{layout_id}", title "{title}").
Room = the substation. Return JSON (all boxes/points normalised 0..1000 to THIS crop):
{
 "room": {"polygon": [[x,y],...] outline of the substation room (inner wall face), "length_mm": , "width_mm": ,
          "overall_dims_text": "as written", "shape": "rectangular|L_shape|chamfered|irregular"},
 "dimensions": [ {"value_mm": number, "p1": [x,y], "p2": [x,y], "text": "as written"} ]   (8-20 clear straight dimension lines, both ends),
 "equipment": [ {"label": "as written e.g. DT-1, RMU, LTP-2", "equipment_type": "transformer|rmu|metering|ltp|ht_switchgear|packaged|acdb|battery|frtu|apfc",
                 "subtype": "dry|oil|ester|3W|4W|icog|...|null", "kva": number|null (only if printed), "bbox": [x0,y0,x1,y1],
                 "dims_text_mm": [L, B] or null, "height_mm": number|null, "weight_text": "as written|null"} ],
 "openings": [ {"type": "door|rolling_shutter|fire_exit|gate|louvre|window|staircase|cable_riser", "label": "as written",
                "bbox": [x0,y0,x1,y1], "width_mm": number|null, "height_mm": number|null} ],
 "cutouts": [ {"bbox": [...], "label": , "size_text": } ],
 "beams": [ {"bbox": [...], "label": , "depth_mm": number|null} ],
 "columns": [ {"bbox": [...]} ],
 "trenches": [ {"polyline": [[x,y],...], "width_mm": number|null, "depth_mm": number|null, "kind": "HT|LT|HT/LT|null", "label": } ],
 "cable_trays": [ {"polyline": [[x,y],...], "width_mm": number|null} ],
 "clearances": [ {"a": "label", "b": "label or wall_N/S/E/W", "mm": number} ]
}"""

PASS2_SECTION = """Pass 2 - one section / elevation crop (layout "{layout_id}", title "{title}").
Return JSON:
{
 "section_name": "A-A or as written",
 "cuts_through": ["equipment labels the section passes through, e.g. DT-1"],
 "clear_height_mm": finished floor (or platform top) to underside of slab, "slab_to_soffit_mm": , "floor_to_floor_mm": ,
 "beam_bottom_mm": lowest beam bottom above finished floor or null, "plinth_mm": raised platform / plinth height or null,
 "platform_type": "RCC|MS chequered plate on steel|null", "trench_depth_mm": number|null,
 "equipment_heights": [ {"label": "as written", "height_mm": number, "ceiling_clearance_mm": number|null} ],
 "tray_level_mm": number|null,
 "notes": "short"
}"""

PASS2_SITE = """Pass 2 - site / key plan crop (layout "{layout_id}", title "{title}").
Return JSON (normalised 0..1000 to THIS crop):
{
 "substation_polygons": [ [[x,y],...] ] proposed CSS area(s),
 "features": [ {"cls": "nalla|dp_road|internal_road|tree|compound_wall|structure|wayleave|access_path|trench",
                "label": "as written", "polygon": [[x,y],...] or null, "polyline": [[x,y],...] or null,
                "width_m": number|null} ],
 "dimensions": [ {"value_mm": number, "p1": [x,y], "p2": [x,y], "text": "as written"} ],
 "stated_distances": [ {"feature": "nalla|dp_road|...", "mm": number, "text": "as written"} ]
}"""
