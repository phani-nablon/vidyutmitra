from substation_retrieval.retrieval.query import build_query, present


def test_import_built_graph_and_index(services):
    st = services.index.stats()
    assert st["documents"] == 5 and st["substations"] == 5 and st["version"] >= 1
    g = services.store.stats()["by_label"]
    assert g["substation"] == 5 and g["equipment"] > 10 and g["concept"] > 30


def test_presence_rule():
    q = build_query("2 x 1500 kVA DT")
    p = present(q)
    assert p["equipment"] and not p["footprint"] and not p["height"]


def test_kg_ranks_exact_match_first(services):
    q = build_query("indoor CSS on ground floor, 3 x 1500 kVA DT, 1 RMU, 2 LTP, room 14 x 8 m, clear height 4.5 m, rolling shutter")
    res = services.orchestrator.search(q, 5, "kg")
    assert res["engine"] == "kg"
    assert res["results"][0]["file_name"].startswith("E_indoor_3x1500")


def test_installation_gate(services):
    q = build_query("open to sky substation, 1 x 990 kVA transformer, RMU, 2 LTP")
    for mode in ("kg", "rag"):
        res = services.orchestrator.search(q, 3, mode)
        assert res["results"][0]["file_name"].startswith("C_open_sky"), mode


def test_auto_falls_back_when_kg_disabled(services):
    services.orchestrator.set_flags(kg_enabled=False)
    try:
        res = services.orchestrator.search(build_query("indoor 2 x 1500 kVA DT RMU LTP"), 3, "auto")
        assert res["engine"] == "rag" and any("disabled" in t for t in res["trace"])
    finally:
        services.orchestrator.set_flags(kg_enabled=True)


def test_geometry_fit_and_nalla_clearance(services):
    site = [[0, 0], [11000, 0], [11000, 8000], [0, 8000]]
    q = build_query("indoor 2 x 1500 kVA DT, RMU, 2 LTP", {"site_polygon_mm": site,
                    "site_context": {"features": [{"cls": "nalla", "label": "nalla", "polyline_mm": [[14000, -1000], [14000, 9000]]}]}})
    res = services.orchestrator.search(q, 5, "kg")
    top = res["results"][0]
    assert top["file_name"].startswith(("A_indoor", "B_indoor"))
    ids = {c["id"]: c["status"] for c in top["checks"]}
    assert ids["footprint_fit"] == "pass" and ids["plot_clearance"] == "fail"
    assert top["verdict"] != "reuse"   # a conflict blocks reuse


def test_incremental_delete_bumps_version(services):
    v0 = services.index.version()
    doc_id = services.index.documents()[0]["doc_id"]
    services.ingest.delete(doc_id)
    assert services.index.version() == v0 + 1 and services.index.stats()["documents"] == 4
    assert services.store.partition(doc_id)["vertices"] == []
    services.ingest.rebuild(log_fn=lambda m: None)   # deleted stays deleted
    assert services.index.stats()["documents"] == 4


def test_image_embedder_wiring(services):
    """The DINOv2 path with a stand-in embedder (no torch in CI): embeddings are stored and used by RAG."""
    import numpy as np
    from substation_retrieval.geometry import raster as R

    class Fake:
        name = "fake-dinov2"

        def embed(self, r):
            v = np.array([r[0].sum(), r[1].sum(), r[2].sum() + 1.0], np.float32)
            return v / np.linalg.norm(v)

        def embed_poses(self, r):
            return np.stack([self.embed(p) for p in R._poses(r)])

    services.ingest.image_embedder = Fake()
    services.rag.image_embedder = Fake()
    try:
        services.ingest.rebuild(log_fn=lambda m: None)
        assert all(s["img_embedding"] is not None for s in services.index.substations())
        q = build_query("indoor 2 x 1500 kVA DT", {"site_polygon_mm": [[0, 0], [11000, 0], [11000, 8000], [0, 8000]]})
        res = services.orchestrator.search(q, 3, "rag")
        assert "image_embedding" in res["results"][0]["components"]
    finally:
        services.ingest.image_embedder = None
        services.rag.image_embedder = None
