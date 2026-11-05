"""Repository browse (VidyutMitra metadata), design-rule editing / loading, and reading a design request."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(services):
    import substation_retrieval.services as S
    S._SERVICES = services
    from substation_retrieval.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def repo(services, tmp_path):
    """A small SubstationRepository in VidyutMitra's layout: one division, one project, two revisions."""
    folder = "drawings/css/andheri/zlp1234_bharti-airtel-limited"
    (tmp_path / folder / "detailed").mkdir(parents=True)
    (tmp_path / folder / "detailed" / "zlp1234_bharti-airtel-limited_DET_v02_20250101.pdf").write_bytes(b"%PDF-1.4 test")
    meta = {"areas": [{"id": "a1", "name": "Andheri", "slug": "andheri", "category": "css"},
                      {"id": "a2", "name": "Chembur", "slug": "chembur", "category": "dss"}],
            "projects": [{"id": "p1", "category": "css", "area": "andheri", "zlp": "ZLP1234", "developer": "Bharti Airtel Limited",
                          "developerSlug": "bharti-airtel-limited", "folder": "zlp1234_bharti-airtel-limited", "createdAt": "2025-01-01T00:00:00Z"},
                         {"id": "p2", "category": "css", "area": "andheri", "zlp": None, "developer": "Shanti Developers",
                          "developerSlug": "shanti-developers", "folder": "unassigned_shanti-developers", "createdAt": "2025-02-01T00:00:00Z"}],
            "drawings": [{"id": "d1", "projectId": "p1", "stage": "detailed", "version": 1, "filename": "zlp1234_bharti-airtel-limited_DET_v01_20241024.dwg",
                          "originalName": "A_indoor_2x1500.pdf", "blobKey": f"{folder}/detailed/missing.dwg", "uploadedAt": "2024-10-24T00:00:00Z",
                          "params": {"areaSqm": 88, "room": {"lengthM": 11, "widthM": 8}, "clearHeightMm": 4500,
                                     "equipment": [{"label": "Dry DT 1500 kVA", "category": "DT", "rating": "1500 kVA", "quantity": 2}]}},
                         {"id": "d2", "projectId": "p1", "stage": "detailed", "version": 2, "filename": "zlp1234_bharti-airtel-limited_DET_v02_20250101.pdf",
                          "originalName": "Airtel v2.pdf", "blobKey": f"{folder}/detailed/zlp1234_bharti-airtel-limited_DET_v02_20250101.pdf",
                          "uploadedAt": "2025-01-01T00:00:00Z", "params": None}]}
    (tmp_path / "_system").mkdir()
    (tmp_path / "_system" / "metadata.json").write_text(json.dumps(meta))
    services.settings.set("repository.path", str(tmp_path))
    yield tmp_path
    services.settings.set("repository.path", None)


def test_repository_hierarchy(client, repo):
    assert client.get("/api/repo/status").json()["projects"] == 2
    cats = {c["key"]: c for c in client.get("/api/repo/categories").json()}
    assert cats["css"]["projects"] == 2 and cats["dss"]["divisions"] == 1
    divs = client.get("/api/repo/css/divisions").json()["divisions"]
    assert divs[0]["slug"] == "andheri" and divs[0]["unassigned"] == 1
    projs = client.get("/api/repo/css/andheri/projects").json()["projects"]
    assert {p["zldp_status"] for p in projs} == {"allocated", "unassigned"}
    assert client.get("/api/repo/css/andheri/projects?q=shanti").json()["projects"][0]["developer"] == "Shanti Developers"

    rec = client.get("/api/repo/projects/p1").json()
    det = next(s for s in rec["stages"] if s["key"] == "detailed")
    assert [r["version"] for r in det["revisions"]] == [2, 1] and det["revisions"][0]["current"]
    assert det["revisions"][1]["doc_id"]                      # linked to the library's extraction by name
    assert rec["parameters"]["area_sqm"] == 88
    assert [s["key"] for s in rec["stages"]] == ["inputs", "tentative", "detailed", "pwd"]

    assert client.get("/api/repo/files/d2").content.startswith(b"%PDF")
    assert client.get("/api/repo/files/d1").status_code == 404   # listed, but the file is not on disk
    assert len(client.get("/api/repo/search?min_area=80&equipment=DT").json()) == 1


def test_repository_missing(client, services, tmp_path):
    services.settings.set("repository.path", str(tmp_path / "nowhere"))
    assert client.get("/api/repo/status").json()["configured"] is False
    assert client.get("/api/repo/categories").status_code == 404
    services.settings.set("repository.path", None)


def test_rules_edit_export_import(client, services, tmp_path):
    d = client.get("/api/rules").json()
    n, sec = len(d["rules"]), d["sections"][0]["id"]
    r = client.post("/api/rules", json={"section": sec, "title": "Test rule", "value": 1200, "unit": "mm"}).json()
    assert r["origin"] == "user"
    assert client.put(f"/api/rules/{r['id']}", json={"value": 1300}).json()["value"] == 1300
    exported = client.get("/api/rules/export")
    assert exported.headers["content-disposition"].startswith("attachment")
    data = json.loads(exported.content)
    assert len(data["rules"]) == n + 1
    assert client.delete(f"/api/rules/{r['id']}").status_code == 200
    assert len(client.get("/api/rules").json()["rules"]) == n
    # importing the exported file brings the rule back
    res = client.post("/api/rules/import", files={"file": ("r.json", json.dumps(data), "application/json")}, data={"keep_user": "true"}).json()
    assert res["rules"] == n + 1
    assert client.post("/api/rules/import", files={"file": ("bad.json", "{}", "application/json")}).status_code == 400
    client.post("/api/rules/reset")
    assert client.get("/api/rules").json()["edited"] is False


def test_understand_network_planning_list(client):
    r = client.post("/api/workbench/understand", json={
        "text": "PFA and place following equipment: 1. 1.5 MVA DT – 3 nos; 2. 2+2 RMU – 1 nos; 3. 1+3 RMU – 1 nos",
        "structured": {"installation": "indoor", "floor": "ground", "clear_height_mm": 4500}}).json()
    eq = [(e["type"], e["count"], e["kva"], e["subtype"]) for e in r["equipment"]]
    assert eq == [("transformer", 3, 1500, None), ("rmu", 1, None, "2+2"), ("rmu", 1, None, "1+3")]
    assert "installation: indoor" in r["asked"] and r["fields"]["clear_height_mm"] == 4500
