"""Layout boxes from the model: unusable ones must be skipped or repaired, never reach cv2.imwrite as an empty crop."""
import numpy as np
import pytest

from substation_retrieval.extraction import pipeline as PL


def test_norm_box_accepts_normal_and_unit_scale():
    assert PL.norm_box([100, 200, 600, 700]) == [100, 200, 600, 700]
    assert PL.norm_box([0.1, 0.2, 0.6, 0.7]) == pytest.approx([100, 200, 600, 700])


@pytest.mark.parametrize("b", [None, [], [1, 2, 3], ["a", 1, 2, 3], [0, 0, 0, 0], [5, 5, 6, 6], [float("nan"), 0, 500, 500]])
def test_norm_box_rejects_unusable(b):
    assert PL.norm_box(b) is None


def test_pixel_boxes_are_rescaled_not_dropped():
    boxes = [[1211, 56, 1754, 308], [20, 40, 900, 700]]
    sc = PL.page_scale(boxes, (1819, 1286))
    assert sc == pytest.approx((1.819, 1.286))
    nb = PL.norm_box(boxes[0], scale=sc)
    assert nb == pytest.approx([1211 / 1.819, 56 / 1.286, 1754 / 1.819, 308 / 1.286])
    assert PL.norm_box(boxes[1], scale=sc) is not None


def test_normalised_boxes_keep_unit_scale():
    assert PL.page_scale([[100, 100, 900, 900]], (1819, 1286)) == (1.0, 1.0)


def test_norm_box_clamps_and_orders():
    assert PL.norm_box([900, 900, -50, 100]) == [0, 100, 900, 900]


class _Pg:
    def __init__(self, W=1000, H=800):
        self.image = np.zeros((H, W, 3), np.uint8)
        self.size_px = (W, H)


def test_crop_empty_raises_badcrop():
    ex = PL.Extractor.__new__(PL.Extractor)
    with pytest.raises(PL.BadCrop):
        ex._crop(_Pg(), {"id": "L", "bbox_px": [5000, 5000, 6000, 6000]})
    with pytest.raises(PL.BadCrop):
        ex._crop(_Pg(), {"id": "L", "bbox_px": [100, 100, 100, 100]})


def test_crop_ok():
    ex = PL.Extractor.__new__(PL.Extractor)
    crop, box = ex._crop(_Pg(), {"id": "L", "bbox_px": [100, 100, 400, 300]})
    assert crop.size and box[2] > box[0] and box[3] > box[1]


def test_artifacts_img_ignores_empty(tmp_path):
    a = PL.Artifacts(tmp_path)
    assert a.img("x.jpg", np.zeros((0, 5, 3), np.uint8)) is None


def test_legend_swatch_colour_drives_segmentation():
    from substation_retrieval.extraction import segment as S
    img = np.full((200, 300, 3), 255, np.uint8)
    img[10:30, 10:30] = (30, 160, 60)                    # legend swatch, a green the named table would not catch exactly
    img[100:150, 120:220] = (30, 160, 60)                # the drawn area
    rgb = S.swatch_rgb(img, [10, 10, 30, 30])
    assert rgb == (30, 160, 60)
    segs, _ = S.refine(img, (0, 0), {}, [{"semantic": "cutout", "rgb": list(rgb), "text": "cut-out"}])
    assert any(sg["source"] == "legend_colour" and sg["legend"] == "cut-out" for sg in segs)


def test_legend_rgb_ignores_white_and_missing():
    from substation_retrieval.extraction import segment as S
    img = np.full((50, 50, 3), 255, np.uint8)
    assert S.swatch_rgb(img, [5, 5, 20, 20]) is None
    assert S.swatch_rgb(img, None) is None


def test_prior_palette_only_without_legend_and_with_seed():
    from substation_retrieval.extraction import segment as S
    img = np.full((200, 300, 3), 255, np.uint8)
    img[50:150, 100:200] = (230, 30, 30)                 # red area
    seed = {"site_boundary": [[90, 40, 210, 160]]}
    segs, _ = S.refine(img, (0, 0), seed, [])
    assert any(sg["source"] == "prior_colour" for sg in segs)
    segs, _ = S.refine(img, (0, 0), {}, [])              # no LLM box: colour alone makes nothing
    assert not segs
    segs, _ = S.refine(img, (0, 0), seed, [{"semantic": "trench", "color_name": "red", "text": "trench"}])
    assert not any(sg["source"] == "prior_colour" for sg in segs)   # legend says red = trench, so no prior for red
    segs, _ = S.refine(img, (0, 0), seed, [], palette={})
    assert all(sg["source"] == "llm" for sg in segs)


def test_learned_prior_resolves_shared_colours_and_drops_rare():
    data = {"trench": {"colours": [{"rgb": [0, 0, 255], "share": 0.75}, {"rgb": [0, 255, 0], "share": 0.44}]},
            "beam": {"colours": [{"rgb": [0, 255, 0], "share": 0.67}, {"rgb": [0, 0, 255], "share": 0.33}]},
            "substation_boundary": {"colours": [{"rgb": [255, 0, 0], "share": 0.57}, {"rgb": [0, 0, 255], "share": 0.29}]}}
    from substation_retrieval.extraction import segment as S
    pr = S.learned_prior(data)
    assert pr["trench"] == [((0, 0, 255), 0.75)]          # green goes to beam (0.67 > 0.44); blue's beam share is below 0.4
    assert pr["beam"] == [((0, 255, 0), 0.67)]
    assert pr["site_boundary"] == [((255, 0, 0), 0.57)]


def test_hatched_vector_fills_merge_into_few_segments():
    from substation_retrieval.extraction import segment as S
    img = np.full((300, 400, 3), 255, np.uint8)
    paths = [{"closed": True, "fill": (0, 170, 0), "pts": [[20 + 6 * i, 50], [24 + 6 * i, 50], [24 + 6 * i, 150], [20 + 6 * i, 150]]}
             for i in range(30)]
    legend = [{"semantic": "cable_tray", "color_name": "green", "text": "tray"}]
    segs, _ = S.refine(img, (0, 0), {}, legend, paths)
    vec = [g for g in segs if g["source"] == "vector"]
    assert 1 <= len(vec) <= 3


def test_legend_text_picks_the_class_and_every_line_is_used():
    from substation_retrieval.extraction import segment as S
    assert S.legend_class({"text": "600mm WIDE HT CABLE TRAY", "semantic": "trench"}) == "cable_tray"      # text beats a vague tag
    assert S.legend_class({"text": "Cut-out for cable entry", "semantic": None}) == "cutout"
    assert S.legend_class({"text": "WAYLEAVE FOR STAFF & VEHICLES", "semantic": "access_path"}) == "wayleave"
    assert S.legend_class({"text": "ACCESS FOR CABLE, STAFF & VEHICLE FROM DP ROAD TO CSS", "semantic": None}) == "access_path"
    assert S.legend_class({"text": "H.T. CABLE TRENCH", "semantic": "trench"}) == "trench"
    assert S.legend_class({"text": "TYPICAL", "semantic": None}) is None
    img = np.full((200, 300, 3), 255, np.uint8)
    img[20:60, 20:120] = (0, 0, 255)            # HT trench, blue
    img[100:140, 20:120] = (255, 0, 0)          # LT trench, red
    legend = [{"text": "HT cable trench", "semantic": "trench", "rgb": [0, 0, 255]},
              {"text": "LT cable trench", "semantic": "trench", "rgb": [255, 0, 0]}]
    segs, _ = S.refine(img, (0, 0), {}, legend)
    assert {g["legend"] for g in segs if g["cls"] == "trench"} == {"HT cable trench", "LT cable trench"}


def test_prior_not_used_when_legend_mentions_class_with_unusable_colour():
    from substation_retrieval.extraction import segment as S
    img = np.full((200, 300, 3), 255, np.uint8)
    img[50:150, 100:200] = (255, 0, 255)         # magenta = prior colour for cutout
    seed = {"cutout": [[90, 40, 210, 160]]}
    legend = [{"text": "cut-out", "semantic": "cutout", "color_name": "black"}]       # legend lists it, but in black
    segs, _ = S.refine(img, (0, 0), seed, legend)
    assert all(g["source"] == "llm" for g in segs)


def test_legend_classes_are_limited_to_the_layout_kind_and_text_is_not_a_mask():
    from substation_retrieval.extraction import segment as S
    img = np.full((300, 400, 3), 255, np.uint8)
    img[40:100, 20:200] = (0, 255, 0)                 # a big green area: trees on a site plan / a tray on a room plan
    img[200:216, 20:140] = (0, 255, 0)                # green TEXT: a word the PDF text layer knows about
    legend = [{"text": "HT cable tray", "semantic": "cable_tray", "rgb": [0, 255, 0]}]
    site, _ = S.refine(img, (0, 0), {}, legend, allowed=S.SITE_CLASSES)
    assert not [g for g in site if g["cls"] == "cable_tray"]                       # trays are not read on a site plan
    room, _ = S.refine(img, (0, 0), {}, legend, allowed=S.ROOM_CLASSES, text_boxes=[(15, 195, 145, 220, "VENTILATION")])
    got = [g for g in room if g["cls"] == "cable_tray"]
    assert len(got) == 1 and max(p[1] for p in got[0]["polygon_px"]) < 120         # the area, not the word
    wayleave, _ = S.refine(img, (0, 0), {}, legend, allowed=S.SITE_CLASSES | S.WAYLEAVE_EXTRA)
    assert [g for g in wayleave if g["cls"] == "cable_tray"]                       # a wayleave drawing carries the tray legend
