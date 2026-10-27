"""DXF adapter (first cut; DWG arrives here after conversion).

CAD drawings are already in model units, so no scale calibration or LLM vision pass is needed for the
geometry. The adapter maps layers / block names to classes with configurable keyword rules, converts
units to millimetres and writes one substation in the room mm frame. Documents are marked provisional
until the layer mapping has been validated on real AEML DWG/DXF files.
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core.util import doc_id_for, now_iso
from ...geometry import polygons as P

UNIT_TO_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}

LAYER_RULES = [  # (class, regex on layer or block name)
    ("room", r"\b(room|css|substation|wall)\b"),
    ("transformer", r"\b(dt|dtr|transformer|trafo|tx)\b"),
    ("rmu", r"\brmu\b"),
    ("ltp", r"\b(ltp|lt[\s_-]*panel|feeder[\s_-]*pillar|icog)\b"),
    ("metering", r"\b(ctpt|metering)\b"),
    ("door", r"\bdoor\b"),
    ("rolling_shutter", r"\b(rolling|shutter|rs)\b"),
    ("louvre", r"\b(louv|vent)"),
    ("cutout", r"\bcut[\s_-]*out\b"),
    ("beam", r"\bbeam\b"),
    ("column", r"\b(col|column)\b"),
    ("trench", r"\btrench\b"),
    ("cable_tray", r"\btray\b"),
]


def classify(name: str) -> str | None:
    n = (name or "").lower().replace("_", " ")
    for cls, pat in LAYER_RULES:
        if re.search(pat, n):
            return cls
    return None


def extract_dxf(path: str | Path, sha: str, settings) -> dict:
    try:
        import ezdxf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("DXF support needs `uv pip install ezdxf`") from e
    path = Path(path)
    dxf = ezdxf.readfile(str(path))
    msp = dxf.modelspace()
    k = UNIT_TO_MM.get(int(dxf.header.get("$INSUNITS", 4)), 1.0)
    items: dict[str, list] = {}
    texts = []
    for e in msp:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            texts.append(e.plain_text() if t == "MTEXT" else e.dxf.text)
            continue
        cls = classify(e.dxf.layer) or (classify(e.dxf.name) if t == "INSERT" else None)
        if not cls:
            continue
        pts = None
        if t == "LWPOLYLINE":
            pts = [[p[0] * k, p[1] * k] for p in e.get_points("xy")]
        elif t == "POLYLINE":
            pts = [[v.dxf.location.x * k, v.dxf.location.y * k] for v in e.vertices]
        elif t == "LINE":
            pts = [[e.dxf.start.x * k, e.dxf.start.y * k], [e.dxf.end.x * k, e.dxf.end.y * k]]
        elif t == "INSERT":
            try:
                from ezdxf import bbox
                bb = bbox.extents([e])
                pts = P.rect(bb.extmin.x * k, bb.extmin.y * k, bb.extmax.x * k, bb.extmax.y * k)
            except Exception:
                pts = None
        if pts:
            items.setdefault(cls, []).append({"pts": pts, "name": getattr(e.dxf, "name", None) or e.dxf.layer})
    rooms = [P.poly(i["pts"]) for i in items.get("room", [])]
    rooms = [r for r in rooms if r is not None]
    room = max(rooms, key=lambda r: r.area) if rooms else None
    x0, y0 = (room.bounds[0], room.bounds[3]) if room is not None else (0.0, 0.0)

    def tf(pts):  # DXF y-up -> room frame y-down
        return [[round(p[0] - x0, 1), round(y0 - p[1], 1)] for p in pts]
    sub = {"id": "S1", "name": "CSS", "civil": {"polygon_mm": tf(P.coords(room)) if room is not None else None, "structures": []},
           "electrical": {"equipment": []}, "access": {"openings": []}, "ventilation": {"openings": []},
           "cable": {"routes": [], "cutouts": [], "entries": []}, "clearances": [], "counts": {}}
    if room is not None:
        L, B = P.dims(room)
        sub["civil"].update({"length_mm": L, "width_mm": B, "area_m2": round(room.area / 1e6, 2), "shape": P.shape_class(room)})
    n = 0
    for cls, lst in items.items():
        for it in lst:
            n += 1
            pts = tf(it["pts"])
            if cls in ("transformer", "rmu", "ltp", "metering"):
                g = P.poly(pts)
                d = P.dims(g) if g is not None else None
                sub["electrical"]["equipment"].append({"id": f"E{n}", "label": it["name"], "equipment_type": cls, "polygon_mm": pts,
                                                       "L_mm": d[0] if d else None, "B_mm": d[1] if d else None,
                                                       "centre_mm": [g.centroid.x, g.centroid.y] if g is not None else None})
            elif cls in ("door", "rolling_shutter"):
                sub["access"]["openings"].append({"id": f"O{n}", "type": cls, "label": it["name"], "polygon_mm": pts})
            elif cls == "louvre":
                sub["ventilation"]["openings"].append({"id": f"O{n}", "type": "louvre", "label": it["name"], "polygon_mm": pts})
            elif cls == "cutout":
                sub["cable"]["cutouts"].append({"id": f"CO{n}", "type": "cutout", "polygon_mm": pts})
            elif cls in ("beam", "column"):
                sub["civil"]["structures"].append({"id": f"T{n}", "type": cls, "polygon_mm": pts})
            elif cls in ("trench", "cable_tray"):
                sub["cable"]["routes"].append({"id": f"R{n}", "type": cls, "polyline_mm": pts})
    return {"schema_version": "2.1", "doc_id": doc_id_for(sha),
            "source": {"file_name": path.name, "sha256": sha, "kind": "dxf", "pages": 1, "path": str(path)},
            "provenance": {"method": "dxf", "created_at": now_iso(), "notes": ["layer-keyword mapping; validate on AEML CAD files"]},
            "metadata": {"substation_count": 1}, "layouts": [{"id": "M1", "type": "equipment_layout", "title": "modelspace"}],
            "segments": [], "associations": [], "substations": [sub] if room is not None or sub["electrical"]["equipment"] else [],
            "site": {}, "quality": {"status": "provisional", "issues": ["DXF adapter: layer mapping not yet validated"]},
            "text": " ".join(texts)[:20000]}
