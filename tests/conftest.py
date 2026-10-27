import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def v1_doc(stem: str, installation="indoor", kva=1500, n_dt=2, L=11000, B=8000, height=4500, chamfer=False, floor="ground floor"):
    """Synthetic v1 extraction (px frame, 10 mm/px) used by the tests - no client data in the repo."""
    s = 10.0
    room_px = [100, 100, 100 + L / s, 100 + B / s]
    eq = []
    for i in range(n_dt):
        x = 100 + (750 + i * 3200) / s
        eq.append({"id": f"DT-{i + 1}", "type": "transformer", "kva": kva, "height_mm": 2400, "weight": "5.4 MT",
                   "bbox": [x, 175, x + 246, 395], "footprint_mm": [2460, 2200],
                   "position_in_room_mm": {"x_from_left": 750 + i * 3200, "y_from_top": 750}})
    eq.append({"id": "RMU", "type": "rmu", "bbox": [700, 600, 820, 720], "footprint_mm": [1200, 1200],
               "position_in_room_mm": {"x_from_left": 6000, "y_from_top": 5000}})
    for j in range(2):
        eq.append({"id": f"LTP-{j + 1}", "type": "ltp", "bbox": [200 + j * 250, 700, 390 + j * 250, 785], "footprint_mm": [1900, 850],
                   "position_in_room_mm": {"x_from_left": 1000 + j * 2500, "y_from_top": 6000}})
    poly = None
    if chamfer:
        poly = [[100, 100], [100 + 0.65 * L / s, 100], [100 + L / s, 100 + 0.5 * B / s], [100 + L / s, 100 + B / s], [100, 100 + B / s]]
    return {
        "source_pdf": stem + ".pdf", "stem": stem, "status": "reviewed", "pipeline": {"stage_a": "test", "stage_b": "test"},
        "pages": [{"page": 1, "dpi": 300.0, "layouts": [
            {"id": "p1_L1", "type": "equipment_layout", "title": "EQUIPMENT LAYOUT", "bbox": [50, 50, 1300, 1000], "mm_per_px": s},
            {"id": "p1_L2", "type": "section", "title": "SECTION A-A", "bbox": [1400, 50, 2000, 600]},
            {"id": "p1_L3", "type": "site_plan", "title": "SITE PLAN", "bbox": [0, 1100, 3000, 3000], "mm_per_px": 20.0}],
            "legend": [{"text": "TRENCH", "semantic": "trench", "color_name": "green"}]}],
        "site": {"substation_boundaries": [{"label": "Proposed CSS", "page": 1, "bbox": None,
                                            "geometry": [{"kind": "polygon", "points": [[1000, 1500], [1550, 1500], [1550, 1900], [1000, 1900]]}]}],
                 "access_paths": [{"label": "24x7 access from DP road", "page": 1, "bbox": None,
                                   "geometry": [{"kind": "polygon", "points": [[1550, 1600], [2500, 1600], [2500, 1700], [1550, 1700]]}]}],
                 "trenches": []},
        "classification": {"css_arrangement": "centralized", "installation": installation, "substation_count": 1,
                           "multiple_substations": False, "location_in_building": floor + " of residential building",
                           "evidence": "test", "confidence": "high"},
        "substations": [{"name": "CSS", "page": 1, "equipment_layout_id": "p1_L1", "section_used": "p1_L2", "section_reason": "cuts DT-1",
                         "section_layout_ids": ["p1_L2"], "mm_per_px": s, "dimension_clearances": [],
                         "room": {"bbox": room_px, "polygon": poly, "length_mm": L, "width_mm": B, "clear_height_mm": height,
                                  "plinth_mm": 1000, "height_source": "test"},
                         "equipment": eq,
                         "openings": [{"type": "rolling_shutter", "label": "RS", "bbox": [100 + 4000 / s, 100 + B / s - 5, 100 + 7000 / s, 100 + B / s + 5],
                                       "measured_mm": [3000, 100]},
                                      {"type": "ventilation", "label": "louvre", "bbox": [100, 300, 105, 480], "measured_mm": [1800, 50]},
                                      {"type": "cutout", "label": "cutout", "bbox": [700, 600, 760, 640], "measured_mm": [600, 400]}],
                         "clearances_geometric": [], "clearances_dimensioned": [],
                         "equipment_counts": {"transformer": n_dt, "rmu": 1, "ltp": 2}}],
        "equipment_counts_total": {}, "notes": "synthetic test drawing",
    }


@pytest.fixture(scope="session")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("v1")
    specs = [("A_indoor_2x1500", {}), ("B_indoor_chamfer", {"chamfer": True}),
             ("C_open_sky_990", {"installation": "open_to_sky", "kva": 990, "n_dt": 1, "L": 9000, "B": 6000}),
             ("D_stilt_2x1000", {"kva": 1000, "floor": "stilt floor", "height": 3600}),
             ("E_indoor_3x1500", {"n_dt": 3, "L": 14000})]
    for stem, kw in specs:
        d = root / stem
        d.mkdir()
        (d / f"{stem}.json").write_text(json.dumps(v1_doc(stem, **kw)))
    return root


@pytest.fixture(scope="session")
def services(corpus, tmp_path_factory):
    from substation_retrieval.config import load_settings
    from substation_retrieval.services import Services
    var = tmp_path_factory.mktemp("var")
    s = load_settings(overrides={"paths.var_dir": str(var), "paths.v1_parsed_dir": str(corpus),
                                 "paths.data_dir": str(var / "nopdf"), "llm.provider": "none"})
    sv = Services(s)
    sv.ingest.import_v1(log_fn=lambda m: None)
    yield sv
    sv.close()
