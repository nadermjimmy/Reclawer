import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import fixtures
    fixtures.run_pipeline()
    import app
    return TestClient(app.app, base_url="https://testserver")


def test_old_urls_open_the_app(client):
    for path, target in [("/", "/app"), ("/login", "/app"), ("/projects", "/app/projects"),
                         ("/groups/111@g.us", "/app/groups/111@g.us"), ("/units?status=available", "/app/units?status=available"),
                         ("/review/mappings", "/app/review/mappings"), ("/sources/message/m1", "/app/sources/message/m1")]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == target, path
    r = client.get("/api/me")
    assert r.status_code == 401
    assert client.post("/api/login", json={"password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"password": "pw"}).status_code == 200
    r = client.get("/logout", follow_redirects=False)
    assert r.headers["location"] == "/app" and 'bridge_session=""' in r.headers.get("set-cookie", "")
    client.post("/api/login", json={"password": "pw"})


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


def test_sync_survives_slow_group_call(client):
    import db, evolution, history, jobs, fixtures
    def slow():
        raise TimeoutError("The read operation timed out")
    evolution.fetch_groups = slow
    jobs.run_now("sync", history.sync_groups)
    with db.connect() as c:
        log = c.execute("SELECT log FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert c.execute("SELECT name FROM groups WHERE jid='201000000000@s.whatsapp.net'").fetchone()[0] == "Nada"
    assert "1 groups and 1 private chats found" in log and "live group names" in log
    evolution.fetch_groups = fixtures.FakeEvolution().fetch_groups


def test_feed(client):
    assert client.get("/feed", follow_redirects=False).headers["location"] == "/app/feed"
    chans = client.get("/api/feed/channels").json()
    uae = next(c for c in chans if c["jid"] == "111@g.us")
    assert uae["count"] >= 7 and uae["name"] == "UAE Inventory"
    assert [c["jid"] for c in client.get("/api/feed/channels?scope=ticked").json()] == ["111@g.us"]
    res = client.get("/api/feed/messages", params={"jid": "111@g.us", "limit": 10}).json()
    ts = [m["ts"] for m in res["messages"]]
    assert ts == sorted(ts)  # oldest first, newest at the bottom
    docs = client.get("/api/feed/messages", params={"jid": "111@g.us", "kind": "workbook"}).json()["messages"]
    assert docs and all(m["file_name"].endswith(".xlsx") for m in docs)
    ok = next(m for m in docs if m["file"])
    gone = next(m for m in docs if m["id"] == "gone")
    assert gone["media"] == "failed" and gone["file"] is None
    prev = client.get(f"/api/feed/preview/{ok['file']['id']}").json()
    assert prev["type"] == "sheets" and prev["sheets"][0]["rows"][1][0] in ("Unit No", "Unit")
    older = client.get("/api/feed/messages", params={"jid": "111@g.us", "before": ts[0]}).json()["messages"]
    assert all(m["ts"] < ts[0] for m in older)
    # on-demand fetch: retry a failed one (fake Evolution still says it's gone) and fetch a fresh doc
    assert client.post("/api/feed/media/gone").status_code == 422
    import db
    with db.connect() as c:
        c.execute("DELETE FROM file_occurrences WHERE wa_id='doc3'")
    r = client.post("/api/feed/media/doc3").json()
    assert r["file"]["kind"] == "workbook"
    assert client.get(f"/sources/file/{r['file']['id']}").headers["content-disposition"].startswith("attachment")


def test_reset_last(client):
    import db
    assert client.post("/api/reset", json={"confirm": "nope"}).status_code == 422
    assert client.post("/api/reset", json={"confirm": "DELETE ALL"}).status_code == 200
    with db.connect() as c:
        for t in ("source_messages", "source_files", "units", "label_mappings", "messages", "groups"):
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert not [f for f in os.listdir(db.MEDIA_DIR) if os.path.isfile(os.path.join(db.MEDIA_DIR, f))]
