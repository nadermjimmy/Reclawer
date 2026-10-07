import os
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


def test_private_chats_and_instance_filter(client):
    import db
    with db.connect() as c:
        assert c.execute("SELECT name FROM groups WHERE jid='201000000000@s.whatsapp.net'").fetchone()[0] == "Nada"
        assert not c.execute("SELECT 1 FROM groups WHERE jid='status@broadcast'").fetchone()
    dm = {"event": "messages.upsert", "instance": "byit-ops", "data": {
        "key": {"remoteJid": "201000000000@s.whatsapp.net", "id": "dm1"}, "messageType": "conversation",
        "message": {"conversation": "Unit B-12 available 3,000,000 EGP"}, "messageTimestamp": 3000}}
    assert client.post("/webhook/t", json=dm).json() == {"ignored": "not a group"}
    other = dict(dm, instance="old-instance", data=dict(dm["data"], key={"remoteJid": "1@g.us", "id": "x9"}))
    assert client.post("/webhook/t", json=other).json() == {"ignored": "instance old-instance"}
    with db.connect() as c:
        assert c.execute("SELECT text FROM source_messages WHERE wa_id='dm1'").fetchone()[0].startswith("Unit B-12")
        assert not c.execute("SELECT 1 FROM source_messages WHERE wa_id='x9'").fetchone()


def test_reset_last(client):
    import db
    assert client.post("/reset", data={"confirm": "nope"}, follow_redirects=False).headers["location"] == "/?reset=refused"
    assert client.post("/reset", data={"confirm": "DELETE ALL"}, follow_redirects=False).headers["location"] == "/?reset=done"
    with db.connect() as c:
        for t in ("source_messages", "source_files", "units", "label_mappings", "messages", "groups"):
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert not [f for f in os.listdir(db.MEDIA_DIR) if os.path.isfile(os.path.join(db.MEDIA_DIR, f))]
    assert client.get("/").status_code == 200
