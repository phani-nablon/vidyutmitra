from substation_retrieval.core import catalogue
from substation_retrieval.core.ontology import equipment_class, leaf_classes, wu_palmer
from substation_retrieval.geometry import polygons as P
from substation_retrieval.geometry.als import Item, RoomGeom, als, align

W = {"footprint": 3, "equipment": 3, "structure": 3, "cutouts": 2, "doors": 2, "height": 2, "trench": 1, "louvres": 0.5, "site": 1}


def test_wu_palmer_orders_similarity():
    assert wu_palmer("transformer", "transformer") == 1.0
    assert wu_palmer("transformer_dry", "transformer_oil") > wu_palmer("transformer_dry", "rmu") > 0
    assert wu_palmer("transformer", "door") == 0.0
    assert len(leaf_classes()) >= 27


def test_equipment_class():
    assert equipment_class("transformer", "DT-1", "dry") == "transformer_dry"
    assert equipment_class("unitised_css") == "packaged"
    assert equipment_class("acdb") == "aux"


def test_catalogue_rating_unique_and_ambiguous():
    r = catalogue.infer_rating("transformer", 2460, 2200, 2400, 5430)
    assert r["kva"] == 1500 and r["unique"]
    amb = catalogue.infer_rating("transformer", None, None, None, None)
    assert amb["kva"] is None
    assert catalogue.parse_weight_kg("10 MT") == 10000


def _room(L, B, shift=(0, 0), rot=0, mirror=False):
    o = P.poly(P.rect(0, 0, L, B))
    eq = P.poly(P.rect(500, 500, 2960, 2700))
    rg = RoomGeom(outline=o, groups={"equipment": [Item("transformer", eq, {"H_mm": 2400, "id": "DT"})]})
    if rot or mirror or shift != (0, 0):
        c = (o.centroid.x, o.centroid.y)
        rg.outline = P.apply_pose(o, rot, mirror, c, *shift)
        rg.groups["equipment"][0].geom = P.apply_pose(eq, rot, mirror, c, *shift)
    return rg


def test_alignment_recovers_rotation_and_mirror():
    q = _room(11000, 8000)
    r = _room(11000, 8000, shift=(3000, -2000), rot=90, mirror=True)
    pose = align(q, r)
    assert pose.iou > 0.97 and pose.containment > 0.97


def test_als_presence_rule_and_identity():
    q = _room(11000, 8000)
    res = als(q, _room(11000, 8000, rot=180), W)
    assert res.score > 0.9
    assert set(res.components) == {"footprint", "equipment"}
    q2 = RoomGeom(outline=P.poly(P.rect(0, 0, 11000, 8000)))
    res2 = als(q2, _room(6000, 4000), W)
    assert "equipment" not in res2.components and res2.components["footprint"] < 0.6
