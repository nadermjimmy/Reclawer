import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import fixtures
    fixtures.run_pipeline()
    import app
    return TestClient(app.app, base_url="https://testserver")


def test_login_required(client):
    r = client.get("/projects", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.post("/login", data={"password": "bad"}, follow_redirects=False).headers["location"] == "/login?error=1"


def test_pages(client):
    import db
    client.post("/login", data={"password": "pw"})
    with db.connect() as c:
        slug = (c.execute("SELECT slug FROM projects LIMIT 1").fetchone() or ["x"])[0]
        unit = (c.execute("SELECT id FROM units LIMIT 1").fetchone() or [0])[0]
        row = (c.execute("SELECT id FROM source_rows LIMIT 1").fetchone() or [0])[0]
        key = (c.execute("SELECT key FROM label_contexts LIMIT 1").fetchone() or [""])[0]
        fact = (c.execute("SELECT id FROM fact_assertions LIMIT 1").fetchone() or [0])[0]
    for path in ["/", "/groups", "/groups/111@g.us", "/projects", f"/projects/{slug}", "/units",
                 "/units?status=available&max_price=1000000", f"/units/{unit}", "/review", "/review/mappings",
                 "/review/mappings?state=all", "/review/images", "/sources", f"/sources/row/{row}",
                 "/sources/message/m1", "/reconciliation", f"/review/fact/{fact}"]:
        r = client.get(path)
        assert r.status_code == 200, path
    r = client.get("/review/context", params={"key": key})
    assert r.status_code == 200
    p = client.get(f"/projects/{slug}").text
    assert "Image withheld / not source-verified" in p and "Not supplied" in p


def test_webhook_still_works(client):
    import db
    body = {"event": "messages.upsert", "data": {"key": {"remoteJid": "111@g.us", "id": "live1"},
            "messageType": "conversation", "message": {"conversation": "Verdana phase 2 sold out"},
            "messageTimestamp": 2000, "pushName": "Sara"}}
    assert client.post("/webhook/t", json=body).json() == {"stored": True}
    assert client.post("/webhook/bad", json=body).status_code == 403
    with db.connect() as c:
        assert c.execute("SELECT origin FROM source_messages WHERE wa_id='live1'").fetchone()[0] == "webhook"
    assert client.get("/health").json()["ok"]
