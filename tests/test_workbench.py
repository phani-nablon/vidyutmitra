"""Workbench API: review queue sorted by open actions, field edits written to the extraction, submit = approve."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(services):
    import substation_retrieval.services as S
    S._SERVICES = services
    from substation_retrieval.api.app import app
    with TestClient(app) as c:
        yield c


def test_spa_routes_and_classic(client):
    for path in ("/", "/new", "/review", "/review/abc", "/classic"):
        assert client.get(path).status_code == 200


def test_queue_sorted_by_open_actions(client):
    q = client.get("/api/workbench/queue").json()
    docs = q["documents"]
    assert len(docs) >= 4
    counts = [d["open_actions"] for d in docs]
    assert counts == sorted(counts, reverse=True)
    assert q["total_open"] == sum(counts)


def test_review_edit_mark_submit(client):
    doc_id = client.get("/api/workbench/queue").json()["documents"][0]["doc_id"]
    rv = client.get(f"/api/workbench/documents/{doc_id}").json()
    room = rv["groups"][0]["items"][0]
    assert room["kind"] == "room" and any(v["editable"] for v in room["values"])
    eq = next(i for g in rv["groups"] for i in g["items"] if i["kind"] == "equipment")

    # a reviewer flags an item: it becomes an open action
    marks = client.put(f"/api/workbench/documents/{doc_id}/marks", json={"item_id": eq["id"], "verdict": "wrong", "note": "kVA misread"}).json()
    assert marks[eq["id"]]["verdict"] == "wrong"
    rv = client.get(f"/api/workbench/documents/{doc_id}").json()
    assert any(a["kind"] == "flagged" for a in rv["actions"])

    # edits land in the extraction and are logged; room area follows length x width
    r = client.patch(f"/api/workbench/documents/{doc_id}/fields", json={"edits": [
        {"item_id": room["id"], "name": "length_mm", "value": 12000},
        {"item_id": room["id"], "name": "width_mm", "value": "8500"},
        {"item_id": eq["id"], "name": "kva", "value": 2000}]}).json()
    assert len(r["changed"]) == 3
    rv = client.get(f"/api/workbench/documents/{doc_id}").json()
    room_vals = {v["name"]: v.get("value") for v in rv["groups"][0]["items"][0]["values"]}
    assert room_vals["length_mm"] == 12000 and room_vals["area_m2"] == 102.0
    assert len(rv["edits"]) == 3

    # geometry is not editable, and nonsense numbers are refused
    assert client.patch(f"/api/workbench/documents/{doc_id}/fields", json={"edits": [
        {"item_id": room["id"], "name": "polygon_mm", "value": 1}]}).status_code == 400
    assert client.patch(f"/api/workbench/documents/{doc_id}/fields", json={"edits": [
        {"item_id": room["id"], "name": "clear_height_mm", "value": "tall"}]}).status_code == 400

    s = client.post(f"/api/workbench/documents/{doc_id}/submit", json={"edits": []}).json()
    assert s["version"] >= 1
    assert client.get(f"/api/workbench/documents/{doc_id}").json()["status"] == "reviewed"
    client.put(f"/api/workbench/documents/{doc_id}/marks", json={"item_id": eq["id"], "verdict": None, "note": ""})


def test_builder_structured_query(client):
    res = client.post("/api/search", json={"text": "", "structured": {
        "installation": "indoor", "equipment_items": [{"type": "transformer", "count": 2, "kva": 1500}, {"type": "rmu", "count": 1}],
        "length_mm": 11000, "width_mm": 8000, "area_m2": 88}}).json()
    assert res["results"]
    assert res["query"]["counts"] == {"transformer": 2, "rmu": 1} and res["query"]["kva"] == [1500, 1500]
