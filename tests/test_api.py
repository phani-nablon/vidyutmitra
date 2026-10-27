import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(services):
    import substation_retrieval.services as S
    S._SERVICES = services
    from substation_retrieval.api.app import app
    with TestClient(app) as c:
        yield c


def test_ui_and_health(client):
    assert client.get("/").status_code == 200
    h = client.get("/api/health").json()
    assert h["index"]["documents"] >= 4 and "kg" in h["engines"]


def test_chat_search_and_followup(client):
    r = client.post("/api/chat", json={"message": "indoor CSS 2 x 1500 kVA DT, RMU, 2 LTP, room 11 x 8 m"}).json()
    assert r["search"]["results"] and r["search"]["results"][0]["overlay"]["room"]
    f = client.post("/api/chat", json={"message": "why is #2 lower?", "session_id": r["session_id"]}).json()
    assert f["intent"] == "followup"


def test_graph_ontology_and_toggles(client):
    d = client.get("/api/documents").json()[0]["doc_id"]
    g = client.get(f"/api/documents/{d}/graph").json()
    assert any(n["label"] == "substation" for n in g["nodes"])
    assert client.get("/api/ontology").json()["tree"]["id"] == "css"
    assert client.put("/api/engines", json={"retrieval_mode": "rag"}).json()["retrieval_mode"] == "rag"
    assert client.put("/api/engines", json={"retrieval_mode": "auto"}).json()["retrieval_mode"] == "auto"
    assert client.put("/api/engines", json={"retrieval_mode": "bogus"}).status_code == 400
