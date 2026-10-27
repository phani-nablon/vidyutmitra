"""Extraction v2 on a synthetic PDF with a scripted LLM (no network)."""
import pymupdf

from substation_retrieval.config import load_settings
from substation_retrieval.core.util import sha256_file
from substation_retrieval.extraction import scale as SC
from substation_retrieval.extraction.pipeline import Extractor


class FakeLLM:
    model = "fake"
    available = True

    def complete(self, system, user, images=None, json_mode=True, cache_key=None, max_tokens=None):
        if "Pass 1" in user:
            return {"layouts": [{"id": "L1", "bbox": [50, 50, 600, 700], "type": "equipment_layout", "title": "EQUIPMENT LAYOUT"},
                                {"id": "L2", "bbox": [650, 50, 950, 700], "type": "section", "title": "SECTION A-A"}],
                    "legend": [], "metadata": {"installation": "indoor", "arrangement": "centralized", "floor": "ground",
                                               "substation_count": 1, "multiple_substations": False},
                    "substations": [{"name": "CSS-1", "equipment_layout": "L1", "section_layouts": ["L2"]}]}
        if "section" in user.lower() and "Pass 2 - one section" in user:
            return {"section_name": "A-A", "cuts_through": ["DT-1"], "clear_height_mm": 4500, "plinth_mm": 1000,
                    "equipment_heights": [{"label": "DT-1", "height_mm": 2400, "ceiling_clearance_mm": 2100}]}
        return {"room": {"polygon": [[100, 100], [900, 100], [900, 900], [100, 900]], "length_mm": 11000, "width_mm": 11000},
                "dimensions": [{"value_mm": 11000, "p1": [100, 950], "p2": [900, 950]},
                               {"value_mm": 11000, "p1": [950, 100], "p2": [950, 900]},
                               {"value_mm": 5500, "p1": [100, 960], "p2": [500, 960]}],
                "equipment": [{"label": "DT-1", "equipment_type": "transformer", "bbox": [150, 150, 330, 310],
                               "dims_text_mm": [2460, 2200], "height_mm": None, "kva": None, "weight_text": "5430 kg"},
                              {"label": "RMU", "equipment_type": "rmu", "bbox": [500, 500, 590, 590]}],
                "openings": [{"type": "rolling_shutter", "label": "RS", "bbox": [400, 890, 650, 910], "width_mm": 3000}],
                "cutouts": [{"bbox": [500, 600, 560, 640]}], "beams": [], "columns": [], "trenches": [], "cable_trays": []}

    def describe(self):
        return {"provider": "fake"}


def _pdf(path):
    doc = pymupdf.open()
    pg = doc.new_page(width=842, height=595)
    pg.draw_rect(pymupdf.Rect(60, 60, 500, 400), color=(0, 0, 0))
    pg.insert_text((70, 80), "EQUIPMENT LAYOUT  DT-1 RMU", fontsize=9)
    doc.save(str(path))


def test_scale_calibration_needs_two_fits():
    dims = [{"value_mm": 1000, "p1": [0, 0], "p2": [100, 0]}, {"value_mm": 2000, "p1": [0, 0], "p2": [0, 201]}]
    cal = SC.calibrate(dims)
    assert cal and abs(cal["mm_per_px"] - 10) < 0.1 and cal["fits"] == 2
    assert SC.calibrate(dims[:1]) is None


def test_llm_pipeline_end_to_end(tmp_path):
    s = load_settings(overrides={"paths.var_dir": str(tmp_path / "var")})
    pdf = tmp_path / "t.pdf"
    _pdf(pdf)
    ex = Extractor(s, FakeLLM())
    doc = ex.extract(pdf, sha256_file(pdf))
    sub = doc["substations"][0]
    assert doc["provenance"]["method"] == "llm_v2"
    assert sub["section_layout_id"] and sub["civil"]["clear_height_mm"] == 4500
    dt = next(e for e in sub["electrical"]["equipment"] if e["equipment_type"] == "transformer")
    assert dt["kva"] == 1500 and dt["kva_source"] == "catalogue_unique"   # rating inferred from L x B x H
    assert sub["civil"]["polygon_mm"] and sub["mm_per_px"]
    assert sub["access"]["openings"][0]["type"] == "rolling_shutter"
    assert (tmp_path / "var" / "artifacts" / doc["doc_id"]).exists()
