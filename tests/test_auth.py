"""Sign-in: cookie session, role checks on the API, dev accounts, user management."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(services):
    import substation_retrieval.services as S
    S._SERVICES = services
    services.settings.set("auth.enabled", True)
    from substation_retrieval.api.app import app
    with TestClient(app) as c:
        yield c
    services.settings.set("auth.enabled", False)


def login(c, user, pw):
    return c.post("/api/auth/login", json={"username": user, "password": pw})


def test_api_needs_a_session(client):
    assert client.get("/api/health").status_code == 200          # public
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/mode").json() == {"enabled": True, "dev_accounts": True}
    assert login(client, "admin", "wrong").status_code == 401
    r = client.get("/classic", follow_redirects=False)
    assert r.status_code in (200, 303)                            # 303 to /login once the workbench is built


def test_roles(client):
    from substation_retrieval.api.auth import hash_password, verify_password
    assert verify_password("Admin@123", hash_password("Admin@123")) and not verify_password("x", hash_password("y"))

    assert login(client, "viewer", "Viewer@123").json()["user"]["role"] == "viewer"
    assert client.get("/api/documents").status_code == 200
    assert client.post("/api/search", json={"text": "indoor CSS 2 x 1500 kVA DT"}).status_code == 200
    doc = client.get("/api/workbench/queue").json()["documents"][0]["doc_id"]
    assert client.put(f"/api/workbench/documents/{doc}/marks", json={"item_id": "x", "verdict": "wrong"}).status_code == 403
    assert client.put("/api/engines", json={"retrieval_mode": "rag"}).status_code == 403

    client.post("/api/auth/logout")
    assert client.get("/api/documents").status_code == 401
    login(client, "draftsman", "Editor@123")
    r = client.put(f"/api/workbench/documents/{doc}/marks", json={"item_id": "probe", "verdict": "unsure"}).json()
    assert r["probe"]["by"] == "draftsman"
    client.put(f"/api/workbench/documents/{doc}/marks", json={"item_id": "probe", "verdict": None})
    assert client.get("/api/auth/users").status_code == 403

    login(client, "admin", "Admin@123")
    assert client.post("/api/auth/users", json={"username": "asha", "role": "editor", "password": "short"}).status_code == 400
    assert client.post("/api/auth/users", json={"username": "asha", "role": "editor", "password": "Long-enough-1"}).json()["role"] == "editor"
    assert any(u["username"] == "asha" for u in client.get("/api/auth/users").json())
    assert client.delete("/api/auth/users/asha").status_code == 200
    assert client.delete("/api/auth/users/admin").status_code == 400   # last admin stays


def test_tampered_cookie_is_refused(client):
    login(client, "viewer", "Viewer@123")
    from substation_retrieval.api.auth import COOKIE
    tok = client.cookies.get(COOKIE)
    body, sig = tok.rsplit(".", 1)
    import base64, json
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["r"] = "admin"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode() + "." + sig
    client.cookies.clear()
    client.cookies.set(COOKIE, forged)
    assert client.get("/api/documents").status_code == 401
