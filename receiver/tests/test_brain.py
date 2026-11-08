"""Brain: re-targets false new rows onto existing rows, approves only safe updates, rejects noise, can be reopened."""
import json, time
import pytest

CHAT = "888@g.us"


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    now = int(time.time())
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("DELETE FROM brain_reviews")
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (CHAT, "ORA sales"))
        for i, text in enumerate(["زد ايست شقة غرفتين 130/7,500,000", "زد ايست 2 bed now 125/10,000,000",
                                  "Hyde Park commission 5", "عميل عنده شقة ريسيل في زد ايست 110/3,000,000",
                                  "Brand new launch Sunset Hills 3 bed 160/9,000,000", "زد ايست studio ok"]):
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                      (f"b{i}", CHAT, now - 100 + i, text))
    yield db, master
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("DELETE FROM brain_reviews")
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 'b_'")
        c.execute("DELETE FROM groups WHERE jid=?", (CHAT,))


def _add(master, db, wa, project, field, value, developer="ORA Developers", ts=None):
    with db.connect() as c:
        master.add_proposal(c, {"project": project, "developer": developer, "field": field, "value": value,
                                "quote": value, "source_kind": "message", "source_ref": wa,
                                "source_ts": ts or int(time.time()) - 50})
        return c.execute("SELECT * FROM proposals WHERE source_ref=? AND field=? ORDER BY id DESC",
                         (wa, field)).fetchone()


def test_brain(env, monkeypatch):
    db, master = env
    import brain, llm
    p_ok = _add(master, db, "b0", "زد ايست", "apartment_2", "130/7,500,000")      # +8.7% on ZED East
    p_jump = _add(master, db, "b1", "زد ايست", "apartment_3", "125/10,000,000")   # no current price: fills empty
    p_com = _add(master, db, "b2", "Hyde Park New Cairo", "commission", "5", developer="Hyde Park")
    p_noise = _add(master, db, "b3", "زد ايست ريسيل", "apartment_1", "110/3,000,000")
    p_new = _add(master, db, "b4", "Sunset Hills", "apartment_3", "160/9,000,000", developer="")
    assert p_ok["match_state"] == "new" and p_ok["row_id"] is None  # spelling matching alone fails
    seen = {}

    def fake(system, content, schema, max_tokens=0, model=None, **kw):
        assert "check suggested updates" in system and model == brain.MODEL
        out = []
        for g in json.loads(content)["groups"]:
            zed = next((c["row_id"] for c in g["candidates"] if c["project"] == "ZED East"), 0)
            hyde = next((c["row_id"] for c in g["candidates"] if c["project"] == "Hyde Park New Cairo"), 0)
            for s in g["suggestions"]:
                seen[s["proposal_id"]] = [c["project"] for c in g["candidates"]]
                pid = s["proposal_id"]
                if pid in (p_ok["id"], p_jump["id"]):
                    out.append({"proposal_id": pid, "verdict": "update_existing", "row_id": zed, "confidence": "high",
                                "reason": "زد ايست is ZED East"})
                elif pid == p_com["id"]:
                    out.append({"proposal_id": pid, "verdict": "update_existing", "row_id": hyde, "confidence": "high",
                                "reason": "Hyde Park commission"})
                elif pid == p_noise["id"]:
                    out.append({"proposal_id": pid, "verdict": "noise", "row_id": 0, "confidence": "high",
                                "reason": "A client's resale unit, not the developer's offer"})
                else:
                    out.append({"proposal_id": pid, "verdict": "new_project", "row_id": 0, "confidence": "high",
                                "reason": "Not on the sheet yet"})
        return {"decisions": out}

    monkeypatch.setattr(llm, "call_json", fake)
    totals = brain.review(lambda *_: None, jids=[CHAT])
    assert "ZED East" in seen[p_ok["id"]]           # found through the developer, not the spelling
    with db.connect() as c:
        get = lambda p: c.execute("SELECT * FROM proposals WHERE id=?", (p["id"],)).fetchone()
        ok, jump, com, noise, new = map(get, (p_ok, p_jump, p_com, p_noise, p_new))
        zed_row = c.execute("SELECT id FROM master_rows WHERE project='ZED East'").fetchone()[0]
        reasons = {r["proposal_id"]: dict(r) for r in c.execute("SELECT * FROM brain_reviews")}
    assert ok["state"] == "approved" and ok["row_id"] == zed_row and ok["decided_by"] == "brain"
    assert jump["state"] == "approved" and jump["row_id"] == zed_row   # empty cell filled, no price to compare
    assert com["state"] == "proposed" and "commission changes always need a person" in reasons[com["id"]]["reason"]
    assert noise["state"] == "rejected" and noise["decided_by"] == "brain"
    assert new["state"] == "proposed" and new["row_id"] is None
    assert totals["approved"] == 2 and totals["rejected"] == 1 and totals["retargeted"] >= 2
    with db.connect() as c:  # the chat spelling is now an alias: next time it matches without the brain
        assert c.execute("SELECT project_key FROM master_aliases WHERE label='زد ايست'").fetchone()

    # a big price move is moved onto the row but left for a person
    p_big = _add(master, db, "b5", "زد ايست", "apartment_2", "130/12,000,000")
    assert p_big["row_id"] == zed_row and p_big["match_state"] == "alias"

    def fake2(system, content, schema, max_tokens=0, model=None, **kw):
        g = json.loads(content)["groups"][0]
        return {"decisions": [{"proposal_id": s["proposal_id"], "verdict": "update_existing", "row_id": zed_row,
                               "confidence": "high", "reason": "ZED East"} for s in g["suggestions"]]}
    monkeypatch.setattr(llm, "call_json", fake2)
    brain.review(lambda *_: None, jids=[CHAT])
    with db.connect() as c:
        big = c.execute("SELECT * FROM proposals WHERE id=?", (p_big["id"],)).fetchone()
        why = c.execute("SELECT reason FROM brain_reviews WHERE proposal_id=?", (p_big["id"],)).fetchone()[0]
    assert big["state"] == "proposed" and "+60%" in why

    # reopen a brain decision; a person's decision can't be reopened
    assert brain.reopen(noise["id"])
    master.decide([p_new["id"]], "reject")
    assert not brain.reopen(p_new["id"])
    with db.connect() as c:
        assert c.execute("SELECT state FROM proposals WHERE id=?", (noise["id"],)).fetchone()[0] == "proposed"


def test_api_shows_brain_and_reopens(env):
    db, master = env
    import app
    from fastapi.testclient import TestClient
    client = TestClient(app.app, base_url="https://testserver")
    client.post("/api/login", json={"password": "pw"})
    items = client.get("/api/proposals", params={"state": "approved"}).json()["items"]
    brained = [i for i in items if i["decided_by"] == "brain"]
    assert brained and brained[0]["brain"]["verdict"] == "update_existing"
    r = client.post(f"/api/proposals/{brained[0]['id']}/reopen")
    assert r.status_code == 200
    assert client.post(f"/api/proposals/{brained[0]['id']}/reopen").status_code == 422


def test_sheet_view(env):
    import app
    from fastapi.testclient import TestClient
    client = TestClient(app.app, base_url="https://testserver")
    client.post("/api/login", json={"password": "pw"})
    r = client.get("/api/sheet", params={"sheet": "New Cairo"}).json()
    assert r["sheet"] == "New Cairo" and [c["field"] for c in r["columns"]][:3] == ["developer", "project", "location"]
    zed = next(x for x in r["rows"] if x["cells"]["project"]["master"] == "ZED East")
    assert zed["cells"]["apartment_2"]["master"] == "125/6,900,000"
    assert zed["cells"]["apartment_2"].get("approved") or zed["cells"]["apartment_2"].get("pending")
    changed = client.get("/api/sheet", params={"sheet": "New Cairo", "view": "changed"}).json()
    assert changed["total"] <= r["total"] and all(x["changes"] or x["pending"] for x in changed["rows"])
    assert client.get("/api/sheet", params={"sheet": "Sheet 2", "q": "hyde"}).json()["total"] >= 1
