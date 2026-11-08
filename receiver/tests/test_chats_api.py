"""Chats, search, single-chat sync and insights (/api, part 2 of the new app)."""
import base64, json, threading, time
import pytest
from fastapi.testclient import TestClient

import fixtures
from fixtures import UAE, XLSX, workbook
from test_master import master_bytes

ARJ = "444@g.us"     # Arabic developer group, not an inventory chat
SYNC = "555@g.us"    # chat synced through the fake Evolution
PRIV = "201555000000@s.whatsapp.net"
NEW_DOC = workbook({"Prices": [["Unit", "Type", "Area", "Price"], ["N-1", "2BR", 130, 7600000]]})
MSG_IDS = [f"ar{i}" for i in range(130)] + ["priv1", "uae_extra"]
SYNC_RECS = [
    {"key": {"remoteJid": SYNC, "id": "s1", "participant": "2010@s.whatsapp.net"}, "pushName": "Karim",
     "messageTimestamp": 40000, "messageType": "conversation",
     "message": {"conversation": "تحديث أسعار ZED East شقة 3 غرف 150/8,000,000"}},
    {"key": {"remoteJid": SYNC, "id": "s2", "participant": "2010@s.whatsapp.net"}, "pushName": "Karim",
     "messageTimestamp": 40010, "messageType": "conversation", "message": {"conversation": "صباح الخير"}},
    {"key": {"remoteJid": SYNC, "id": "s_doc", "participant": "2010@s.whatsapp.net"}, "pushName": "Karim",
     "messageTimestamp": 40020, "messageType": "documentMessage",
     "message": {"documentMessage": {"fileName": "New Zayed prices.xlsx", "mimetype": XLSX, "caption": ""}}},
]


def _cleanup():
    import db, search
    with db.connect() as c:
        for t in ("proposals", "master_cells", "master_rows", "master_sheets", "master_issues", "master_aliases",
                  "master_imports"):
            c.execute(f"DELETE FROM {t}")
        ids = MSG_IDS + [r["key"]["id"] for r in SYNC_RECS]
        marks = ",".join("?" * len(ids))
        fids = [r[0] for r in c.execute(f"SELECT file_id FROM file_occurrences WHERE wa_id IN ({marks})", ids)]
        fids = [f for f in fids if not c.execute(
            f"SELECT 1 FROM file_occurrences WHERE file_id=? AND wa_id NOT IN ({marks})", (f, *ids)).fetchone()]
        c.execute(f"DELETE FROM file_occurrences WHERE wa_id IN ({marks})", ids)
        c.execute(f"DELETE FROM media_fetch WHERE wa_id IN ({marks})", ids)
        c.execute(f"DELETE FROM processed_batches WHERE batch_key IN ({marks})", ["u:" + i for i in ids])
        for f in fids:
            for (rid,) in c.execute("SELECT id FROM source_rows WHERE file_id=?", (f,)).fetchall():
                c.execute("DELETE FROM processed_batches WHERE batch_key=?", (f"ur:{rid}",))
            for t in ("source_rows", "sheet_layouts"):
                c.execute(f"DELETE FROM {t} WHERE file_id=?", (f,))
            c.execute("DELETE FROM source_files WHERE id=?", (f,))
        c.execute(f"DELETE FROM source_messages WHERE wa_id IN ({marks})", ids)
        if search.available(c):
            c.execute(f"DELETE FROM {search.FTS} WHERE wa_id IN ({marks})", ids)
        for jid in (ARJ, SYNC, PRIV):
            for t in ("groups", "group_scope", "chat_syncs"):
                c.execute(f"DELETE FROM {t} WHERE jid=?", (jid,))


def _store(jid, wa_id, ts, text, sender="Omar", file_name=None):
    import db, history
    row = {"wa_id": wa_id, "group_jid": jid, "sender": None, "sender_name": sender, "ts": ts,
           "message_type": "documentMessage" if file_name else "conversation", "text": text,
           "file_name": file_name, "mimetype": XLSX if file_name else None, "has_media": 1 if file_name else 0,
           "raw_json": "{}", "origin": "test", "ingested_at": int(time.time())}
    with db.connect() as c:
        assert history.store_message(c, row)


@pytest.fixture(scope="module")
def client():
    fixtures.run_pipeline()
    import app, db, master
    _cleanup()
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    with db.connect() as c:
        c.execute("INSERT INTO groups VALUES(?,?)", (ARJ, "مطوّر العاصمة - أسعار"))
        c.execute("INSERT INTO groups VALUES(?,?)", (SYNC, "New Zayed developer"))
        c.execute("INSERT INTO group_scope(jid,in_scope) VALUES(?,0)", (ARJ,))
    for i in range(130):
        text = "أَسْعَار جديدة ZED East 2 bed 130/7,500,000" if i == 100 else f"رسالة رقم {i}"
        _store(ARJ, f"ar{i}", 20000 + i * 10, text, sender="كريم")
    _store(PRIV, "priv1", 30000, "Hello, any news on Jiran?", sender="Nada")
    c_ = TestClient(app.app, base_url="https://testserver")
    assert c_.post("/api/login", json={"password": "pw"}).status_code == 200
    yield c_
    _cleanup()  # the DB is shared by every test module


def test_chat_list_and_scope(client):
    res = client.get("/api/chats").json()
    by = {c["jid"]: c for c in res["chats"]}
    assert by[UAE]["in_scope"] and by[UAE]["count"] >= 7 and by[UAE]["kind"] == "group"
    arj = by[ARJ]
    assert arj["count"] == 130 and not arj["in_scope"] and arj["preview"] == "رسالة رقم 129"
    assert by[PRIV]["kind"] == "private" and by[PRIV]["name"].startswith("+201555")
    order = [c["last_ts"] or 0 for c in res["chats"]]
    assert order == sorted(order, reverse=True)  # newest first, chats without messages last
    assert by[SYNC]["count"] == 0  # a group with no messages yet still shows (it can be synced)
    assert ARJ not in {c["jid"] for c in client.get("/api/chats?filter=inventory").json()["chats"]}
    assert {c["jid"] for c in client.get("/api/chats?filter=private").json()["chats"]} >= {PRIV}
    assert UAE not in {c["jid"] for c in client.get("/api/chats?filter=private").json()["chats"]}
    assert [c["jid"] for c in client.get("/api/chats", params={"q": "مطور"}).json()["chats"]] == [ARJ]
    assert client.patch(f"/api/chats/{ARJ}", json={"in_scope": True}).json() == {"jid": ARJ, "in_scope": True}
    assert ARJ in {c["jid"] for c in client.get("/api/chats?filter=inventory").json()["chats"]}
    assert client.patch("/api/chats", json={"jids": [ARJ, "nope@g.us"], "in_scope": False}).json() == {"updated": 1}
    assert client.patch("/api/chats/nope@g.us", json={"in_scope": True}).status_code == 404
    assert client.patch(f"/api/chats/{ARJ}", json={"in_scope": "yes"}).status_code == 422


def test_messages_paging_and_around(client):
    first = client.get(f"/api/chats/{ARJ}/messages", params={"limit": 50}).json()
    ids = [m["id"] for m in first["messages"]]
    assert ids == [f"ar{i}" for i in range(80, 130)] and first["has_more"] and not first["has_newer"]
    assert first["chat"]["name"] == "مطوّر العاصمة - أسعار"
    older = client.get(f"/api/chats/{ARJ}/messages", params={"limit": 50, "before": ids[0]}).json()
    assert [m["id"] for m in older["messages"]] == [f"ar{i}" for i in range(30, 80)] and older["has_more"]
    oldest = client.get(f"/api/chats/{ARJ}/messages", params={"limit": 50, "before": "ar30"}).json()
    assert len(oldest["messages"]) == 30 and not oldest["has_more"]
    around = client.get(f"/api/chats/{ARJ}/messages", params={"limit": 20, "around": "ar10"}).json()
    got = [m["id"] for m in around["messages"]]
    assert "ar10" in got and got == sorted(got, key=lambda x: int(x[2:])) and around["has_newer"]
    assert around["around"] == "ar10" and got[0] == "ar0" and not around["has_more"]
    newer = client.get(f"/api/chats/{ARJ}/messages", params={"limit": 20, "after": got[-1]}).json()
    assert newer["messages"][0]["id"] == f"ar{int(got[-1][2:]) + 1}"
    assert client.get(f"/api/chats/{ARJ}/messages", params={"around": "doc1"}).status_code == 404
    assert client.get("/api/chats/nope@g.us/messages").status_code == 404
    uae = client.get(f"/api/chats/{UAE}/messages").json()["messages"]
    doc = next(m for m in uae if m["id"] == "doc1")
    assert doc["kind"] == "workbook" and doc["file"]["url"].startswith("/sources/file/") and doc["media"] == "ok"
    assert doc["file"]["file_name"] == "Brabus availability.xlsx"
    assert next(m for m in uae if m["id"] == "gone")["media"] == "failed"


def test_chat_suggestions_and_markers(client):
    import db, master
    with db.connect() as c:
        assert master.add_proposal(c, {"project": "ZED East", "field": "apartment_2", "value": "130/7,500,000",
                                       "quote": "130/7,500,000", "source_kind": "message", "source_ref": "ar100",
                                       "source_ts": 21000}) == 1
        assert master.add_proposal(c, {"project": "Jiran", "field": "payment_plan", "value": "20% - 10Y",
                                       "quote": "20% 10 years", "source_kind": "message", "source_ref": "ar101",
                                       "source_ts": 21010}) == 1
    res = client.get(f"/api/chats/{ARJ}/suggestions").json()
    assert res["counts"] == {"proposed": 2, "approved": 0, "rejected": 0} and res["has_master"]
    first = res["items"][0]
    assert first["source"]["chat_jid"] == ARJ and first["source"]["ref"] == "ar101"  # newest source first
    zed = next(i for i in res["items"] if i["field"] == "apartment_2")
    assert zed["delta"]["price_pct"] == 8.7 and zed["source"]["chat_name"] == "مطوّر العاصمة - أسعار"
    assert client.get(f"/api/chats/{ARJ}/proposals?state=approved").json()["items"] == []
    assert client.get(f"/api/chats/{UAE}/suggestions").json()["items"] == []
    page = client.get(f"/api/chats/{ARJ}/messages", params={"around": "ar100", "limit": 10}).json()["messages"]
    assert next(m for m in page if m["id"] == "ar100")["suggestion_ids"] == [zed["id"]]
    listed = {c["jid"]: c for c in client.get("/api/chats?filter=suggestions").json()["chats"]}
    assert listed[ARJ]["open_suggestions"] == 2 and UAE not in listed
    assert client.get("/api/me").json()["counts"]["chats_with_suggestions"] == 1


def test_search_arabic_and_files(client):
    r = client.get("/api/search", params={"q": "اسعار"}).json()
    assert r["chats"][0]["jid"] == ARJ and r["chats"][0]["match_ranges"]
    hit = next(m for m in r["messages"] if m["wa_id"] == "ar100")
    a, b = hit["match_ranges"][0]
    assert hit["snippet"][a:b] == "أَسْعَار" and hit["chat_name"].startswith("مطوّر")  # stored text, tashkeel kept
    no_stem = client.get("/api/search", params={"q": "الأسعار"}).json()["messages"]
    assert not any(m["wa_id"] == "ar100" for m in no_stem)  # no stemming: 'ال' is part of the word
    assert any(m["wa_id"] == "ar100" for m in client.get("/api/search", params={"q": "أسعار zed"}).json()["messages"])
    # prefix match, chat filter, file names, sender, minimum length
    r = client.get("/api/search", params={"q": "رسال", "chat": ARJ, "limit": 500}).json()
    assert len(r["messages"]) == 129 and not r["has_more"] and r["messages"][0]["wa_id"] == "ar129"
    assert client.get("/api/search", params={"q": "رسال", "limit": 5}).json()["has_more"]
    f = client.get("/api/search", params={"q": "brabus avail"}).json()["messages"]
    doc = next(m for m in f if m["wa_id"] == "doc1")
    assert doc["jid"] == UAE and doc["chat_name"] == "UAE Inventory"
    assert client.get("/api/search", params={"q": "jiran"}).json()["messages"][0]["wa_id"] == "priv1"
    assert client.get("/api/search", params={"q": "ر"}).json()["messages"] == []


def test_search_like_fallback(client, monkeypatch):
    import search
    monkeypatch.setattr(search, "available", lambda c: False)
    r = client.get("/api/search", params={"q": "اسعار", "chat": ARJ}).json()
    assert [m["wa_id"] for m in r["messages"]] == ["ar100"]


def _wait(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_sync_chat_end_to_end(client, monkeypatch):
    import db, evolution, llm
    _store(UAE, "uae_extra", 39500, "ZED East 2 bed 200/9,000,000 (other chat, must not be read)")
    pulled, asked = [], []

    def find_messages(jid, page, page_size=100):
        pulled.append(jid)
        return (SYNC_RECS if jid == SYNC else []), 1

    def media(wa_id, jid=None):
        assert wa_id == "s_doc"
        return {"base64": base64.b64encode(NEW_DOC).decode(), "fileName": "New Zayed prices.xlsx", "mimetype": XLSX}

    def fake_llm(system, content, schema, max_tokens=16000, **kw):
        assert "broker team's project sheet" in system
        body = json.loads(content)
        ups = []
        for m in body.get("messages", []):
            asked.append(m["source_id"])
            if "ZED East" in m["text"]:
                ups.append({"source_id": m["source_id"], "developer": "", "project": "ZED East",
                            "location_code": "", "field": "apartment_3", "value": "150/8,000,000",
                            "quote": m["text"]})
        for r in body.get("rows", []):
            asked.append("row:" + r["source_id"])
        return {"updates": ups}

    monkeypatch.setattr(evolution, "find_messages", find_messages)
    monkeypatch.setattr(evolution, "media_base64", media)
    monkeypatch.setattr(llm, "call_json", fake_llm)

    r = client.post(f"/api/chats/{SYNC}/sync")
    assert r.status_code == 422 and r.json()["error"] == "not_in_scope"
    assert client.post("/api/chats/nope@g.us/sync").json()["error"] == "unknown_chat"
    r = client.post(f"/api/chats/{SYNC}/sync?add_to_scope=1")
    assert r.status_code == 202
    job = _wait(client, r.json()["job_id"])
    assert job["status"] == "done", job["log_chunk"]
    log = job["log_chunk"]
    stages = [json.loads(l.split("PROGRESS ", 1)[1]) for l in log.splitlines() if "PROGRESS " in l]
    assert [s["stage"] for s in stages] == [1, 2, 3, 4, 5] and stages[0] == {
        "stage": 1, "of": 4, "label": "Pull latest messages", "done": 0, "total": 4}
    assert job["result"] == {"messages": 3, "files": 1, "files_failed": 0, "suggestions": 1, "failed_batches": 0,
                             "auto_approved": 0, "auto_rejected": 0}
    assert job["progress"]["label"] == "Finished"
    assert set(pulled) == {SYNC}                         # only this chat was pulled
    assert "uae_extra" not in asked and {"s1", "s2"} <= set(asked)  # only this chat's messages were read
    assert any(a.startswith("row:") for a in asked)       # its new file was read and sent for suggestions
    with db.connect() as c:
        assert c.execute("SELECT in_scope FROM group_scope WHERE jid=?", (SYNC,)).fetchone()[0] == 1
        assert c.execute("SELECT parse_status FROM source_files WHERE file_name='New Zayed prices.xlsx'"
                         ).fetchone()[0] == "parsed"
        assert not c.execute("SELECT 1 FROM processed_batches WHERE batch_key='u:uae_extra'").fetchone()
    chat = next(c for c in client.get("/api/chats").json()["chats"] if c["jid"] == SYNC)
    assert chat["last_sync_at"] and chat["in_scope"] and chat["open_suggestions"] == 1 and chat["new_since_sync"] == 0
    sugg = client.get(f"/api/chats/{SYNC}/suggestions").json()["items"]
    assert [s["new_raw"] for s in sugg] == ["150/8,000,000"] and sugg[0]["source"]["ref"] == "s1"
    assert any(m["wa_id"] == "s1" for m in client.get("/api/search", params={"q": "تحديث"}).json()["messages"])
    # second run: nothing new; in scope now, so no confirmation needed
    job = _wait(client, client.post(f"/api/chats/{SYNC}/sync").json()["job_id"])
    assert job["result"]["messages"] == 0 and job["result"]["suggestions"] == 0


def test_sync_busy_and_bridge_error(client, monkeypatch):
    import evolution, jobs
    gate = threading.Event()
    blocker = jobs.start("Blocking test job", lambda log: gate.wait(5))
    try:
        r = client.post(f"/api/chats/{SYNC}/sync")
        assert r.status_code == 409 and r.json()["error"] == "busy" and r.json()["job"]["id"] == blocker
        r = client.post(f"/api/chats/{ARJ}/sync?add_to_scope=1")
        assert r.status_code == 409
    finally:
        gate.set()
    _wait(client, blocker)
    import db
    with db.connect() as c:
        assert c.execute("SELECT in_scope FROM group_scope WHERE jid=?", (ARJ,)).fetchone()[0] == 0  # unchanged

    def down(jid, page, page_size=100):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(evolution, "find_messages", down)
    job = _wait(client, client.post(f"/api/chats/{SYNC}/sync").json()["job_id"])
    assert job["status"] == "failed" and job["result"]["error"] == "bridge_unreachable"


def test_insights_sections(client, monkeypatch):
    import api, db
    with db.connect() as c:
        zed = c.execute("SELECT id FROM proposals WHERE source_ref='ar100'").fetchone()[0]
    assert client.post("/api/proposals/decide", json={"ids": [zed], "action": "approve"}).json()["done"] == [zed]
    res = client.get("/api/insights?days=7").json()
    assert res["errors"] == {} and res["has_master"] and res["days"] == 7
    s = res["sections"]
    assert set(s) == {"summary", "changes", "price_moves", "stale", "throughput", "rows_to_place", "chats", "missing_data"}
    assert s["summary"]["approved"] == 1 and s["summary"]["open"] >= 1 and s["summary"]["rows_to_place"] == 3
    assert s["changes"]["total"] == 1 and s["changes"]["by_sheet_field"][0]["field_label"] == "Apartment 2"
    move = s["price_moves"][0]
    assert move["project"] == "ZED East" and move["pct"] == 8.7 and move["old"]["price"] == 6900000 \
        and move["new"]["area"] == 130 and move["chat_jid"] == ARJ
    stale = {r["project"]: r for r in s["stale"]["rows"]}
    assert "ZED East" not in stale and stale["Hyde Park New Cairo"]["basis"] == "never"  # Hold: no date
    assert s["stale"]["count"] == s["summary"]["stale_count"] and s["stale"]["days"] == 14
    assert len(s["throughput"]) == 7 and sum(d["approved"] for d in s["throughput"]) == 1
    assert s["rows_to_place"]["count"] == 3 and len(s["rows_to_place"]["first"]) == 3
    assert isinstance(s["chats"]["top"], list)
    assert res["exports"]["team"] == "/api/master/export/team"

    def broken(c, since, now):
        raise RuntimeError("boom")

    monkeypatch.setitem(api.INSIGHTS, "chats", broken)
    res = client.get("/api/insights?days=30").json()
    assert "boom" in res["errors"]["chats"] and "chats" not in res["sections"] and "stale" in res["sections"]
    only = client.get("/api/insights?sections=stale").json()
    assert list(only["sections"]) == ["stale"]
