"""JSON API for the new app (/api) and the SPA route (/app)."""
import io, json, os, threading, time
import openpyxl, pytest
from fastapi.testclient import TestClient

from test_master import master_bytes

MSG = "apimsg1"


def _clean_master():
    import db
    with db.connect() as conn:
        for t in ("proposals", "master_cells", "master_rows", "master_sheets", "master_issues", "master_aliases",
                  "master_imports"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM source_messages WHERE wa_id=?", (MSG,))


@pytest.fixture(scope="module")
def client():
    import fixtures
    fixtures.run_pipeline()
    import app
    _clean_master()  # start without a master, whatever ran before
    c = TestClient(app.app, base_url="https://testserver")
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200
    yield c
    _clean_master()  # the DB is shared by every test module: leave no master, suggestions or extra messages


def test_unauthenticated():
    import fixtures
    fixtures.run_pipeline()
    import app
    anon = TestClient(app.app, base_url="https://testserver")
    for path in ("/api/me", "/api/proposals", "/api/master", "/api/jobs/1"):
        r = anon.get(path, follow_redirects=False)
        assert r.status_code == 401 and r.json() == {"error": "login"}, path
    assert anon.post("/api/proposals/decide", json={}).status_code == 401
    r = anon.get("/projects", follow_redirects=False)  # classic pages still redirect
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert anon.get("/app/updates").status_code in (200, 503)  # the app shell loads and shows its own login
    assert anon.post("/api/login", json={"password": "nope"}).status_code == 401
    r = anon.post("/api/login", json={"password": "pw"})
    assert r.status_code == 200 and "bridge_session" in r.headers["set-cookie"]
    assert anon.get("/api/me").status_code == 200
    assert anon.get("/projects").status_code == 200  # same session as the classic pages
    anon.post("/api/logout")
    anon.cookies.clear()
    assert anon.get("/api/me").status_code == 401


def test_me_before_master(client):
    me = client.get("/api/me").json()
    assert me["has_master"] is False and me["market"]["short"] and me["job"] is None
    assert set(me["counts"]) >= {"open_suggestions", "rows_to_place", "open_issues", "chats_with_suggestions"}
    assert client.get("/api/proposals").json()["has_master"] is False
    assert client.get("/api/master/export/team").status_code == 409


def test_master_upload_issues_export(client):
    r = client.post("/api/master", files={"file": ("x.xlsx", b"not a workbook")})
    assert r.status_code == 422 and r.json()["error"] == "unreadable"
    r = client.post("/api/master", files={"file": ("App Updates.xlsx", master_bytes())})
    body = r.json()
    assert r.status_code == 200 and body["summary"]["sheets"]["New Cairo"] == 3 and body["superseded"] == 0
    info = client.get("/api/master").json()
    assert info["import"]["file_name"] == "App Updates.xlsx" and info["rows_to_place"] == 3
    issues = client.get("/api/master/issues").json()
    assert {i["project"] for i in issues} == {"Odd Place", "Old Coast", "L Project"}
    old = next(i for i in issues if i["project"] == "Old Coast")
    assert old["target_sheet"] == "New Cairo"
    assert client.post(f"/api/master/issues/{old['id']}", json={"action": "move", "sheet": "Nowhere"}).status_code == 422
    assert client.post(f"/api/master/issues/{old['id']}", json={"action": "move", "sheet": "New Cairo"}).json() == \
        {"id": old["id"], "state": "move"}
    assert len(client.get("/api/master/issues").json()) == 2
    assert len(client.get("/api/master/issues?show=all").json()) == 3
    for which in ("team", "sql"):
        r = client.get(f"/api/master/export/{which}")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        openpyxl.load_workbook(io.BytesIO(r.content))
    assert client.get("/api/master/export/other").status_code == 404


def _seed():
    import db, fixtures, master
    with db.connect() as c:
        c.execute("INSERT OR IGNORE INTO source_messages(wa_id,group_jid,ts,text,sender_name) VALUES(?,?,?,?,?)",
                  (MSG, fixtures.UAE, 5000, "ZED East 2 bed 130/7,500,000. Hyde Park commission 4.5", "Omar"))
        base = {"source_kind": "message", "source_ref": MSG, "source_ts": 5000, "quote": "from chat"}
        for it in [dict(project="ZED East", field="apartment_2", value="130/7,500,000"),
                   dict(project="Zed Eastt", field="payment_plan", value="5% -10y"),
                   dict(project="Hyde Park New Cairo", field="commission", value="4.5"),
                   dict(project="Brand New Towers", field="apartment_3", value="150/8,000,000"),
                   dict(project="Sunset Bay", field="apartment_3", value="160/9,000,000", location_code="z"),
                   dict(project="ZED East", field="ready_to_move", value="rtm -->>")]:
            assert master.add_proposal(c, dict(base, **it)) == 1, it
        return {r["project"] + "|" + r["field"]: r["id"] for r in c.execute("SELECT * FROM proposals")}


def test_proposals_list_detail_decide(client):
    ids = _seed()
    res = client.get("/api/proposals").json()
    assert res["total"] == 6 and res["counts"]["proposed"] == 6
    by = {i["project"] + "|" + i["field"]: i for i in res["items"]}
    zed = by["ZED East|apartment_2"]
    assert zed["old_raw"] == "125/6,900,000" and zed["field_label"] == "Apartment 2" and zed["warning_codes"] == []
    assert zed["delta"]["price_pct"] == 8.7 and zed["delta"]["area_from"] == 125
    assert zed["source"]["chat_name"] == "UAE Inventory" and zed["source"]["kind"] == "message"
    assert by["Zed Eastt|payment_plan"]["warning_codes"] == ["near_match"]
    assert by["Hyde Park New Cairo|commission"]["warning_codes"] == ["lifts_hold"]
    assert "lifts Hold/Hide" in by["Hyde Park New Cairo|commission"]["warning"]  # text kept as before
    assert by["Brand New Towers|apartment_3"]["warning_codes"] == ["needs_sheet"]
    assert by["Sunset Bay|apartment_3"]["warning_codes"] == ["new_project"]
    assert "bad_format" in by["ZED East|ready_to_move"]["warning_codes"]
    assert res["warning_counts"]["near_match"] == 1
    # filters
    assert client.get("/api/proposals?warning=needs_sheet").json()["total"] == 1
    assert client.get("/api/proposals?warning=any").json()["total"] == 5
    assert client.get("/api/proposals?chat=111@g.us").json()["total"] == 6
    assert client.get("/api/proposals?chat=nope").json()["total"] == 0
    assert client.get("/api/proposals?q=sunset").json()["total"] == 1
    page = client.get("/api/proposals?limit=4").json()
    assert len(page["items"]) == 4 and page["next_cursor"] == "4"
    assert len(client.get(f"/api/proposals?limit=4&cursor=4").json()["items"]) == 2
    assert client.get("/api/proposals?sort=warnings").json()["items"][0]["warning_codes"]
    # detail: current sheet row + source message with neighbours
    d = client.get(f"/api/proposals/{zed['id']}").json()
    vals = {v["field"]: v["raw"] for v in d["row_values"]}
    assert vals["apartment_2"] == "125/6,900,000" and vals["payment_plan"] == "10% -8y"
    assert [v["field"] for v in d["row_values"]][:2] == ["location", "apartment_2"]  # sheet column order
    ctx = d["source_context"]
    assert ctx["kind"] == "message" and ctx["message"]["is_source"] and ctx["chat"]["name"] == "UAE Inventory"
    assert client.get("/api/proposals/999999").status_code == 404
    # decide: needs_sheet is skipped without a sheet, then approved with a per-item sheet
    new_id, near_id, hold_id = ids["Brand New Towers|apartment_3"], ids["Zed Eastt|payment_plan"], \
        ids["Hyde Park New Cairo|commission"]
    r = client.post("/api/proposals/decide", json={"ids": [new_id], "action": "approve"}).json()
    assert r["done"] == [] and r["skipped"][0]["id"] == new_id and r["skipped"][0]["state"] == "proposed"
    assert client.post("/api/proposals/decide", json={"ids": [new_id], "action": "approve",
                                                      "sheet_by_id": {str(new_id): "Mars"}}).status_code == 422
    r = client.post("/api/proposals/decide", json={
        "ids": [new_id, near_id, hold_id], "action": "approve", "sheet_by_id": {str(new_id): "New Capital"}}).json()
    assert sorted(r["done"]) == sorted([new_id, near_id, hold_id]) and r["skipped"] == []
    assert r["counts"]["approved"] == 3 and r["counts"]["proposed"] == 3
    import db, master
    with db.connect() as c:
        row = c.execute("SELECT * FROM master_rows WHERE project='Brand New Towers'").fetchone()
        assert row["sheet"] == "New Capital" and row["state"] == "new"
        assert c.execute("SELECT 1 FROM master_aliases WHERE alias_key='zedeastt'").fetchone()
    r = client.post("/api/proposals/decide", json={"ids": [ids["Sunset Bay|apartment_3"], near_id],
                                                   "action": "reject"}).json()
    assert r["done"] == [ids["Sunset Bay|apartment_3"]] and r["skipped"] == [
        {"id": near_id, "reason": "already approved", "state": "approved"}]
    assert client.post("/api/proposals/decide", json={"ids": [1], "action": "maybe"}).status_code == 422
    approved = client.get("/api/proposals?state=approved").json()
    assert approved["total"] == 3 and all(i["decided_at"] for i in approved["items"])
    me = client.get("/api/me").json()
    assert me["has_master"] and me["counts"]["open_suggestions"] == 2 and me["counts"]["chats_with_suggestions"] == 1


def test_master_decide_per_item_sheet_direct():
    """master.decide keeps its old (count, reasons) return and accepts {id: sheet}."""
    import db, master
    with db.connect() as c:
        n = master.add_proposal(c, {"project": "Another New One", "field": "studio", "value": "50/2,000,000",
                                    "source_kind": "message", "source_ref": MSG, "source_ts": 5001})
        pid = c.execute("SELECT id FROM proposals WHERE project='Another New One'").fetchone()[0]
    assert n == 1
    assert master.decide([pid], "approve") == (0, ["Another New One: choose a sheet for the new project"])
    assert master.decide([pid], "approve", {pid: "New Cairo"}) == (1, [])
    assert master.decide([pid], "approve") == (0, [])  # already decided: silently ignored, as before


def test_jobs_start_and_poll(client, monkeypatch):
    import web
    steps = client.get("/api/steps").json()
    keys = [s["key"] for s in steps]
    assert keys[0] == "chat_routine" and "propose_updates" in keys
    assert next(s for s in steps if s["key"] == "parse_files")["redo_label"]
    gate = threading.Event()

    def fake(log, redo=False):
        log("PROGRESS " + json.dumps({"stage": "Reading", "done": 1, "total": 2}))
        gate.wait(5)
        log(f"redo={redo}")
        log("RESULT " + json.dumps({"suggestions": 4}))

    monkeypatch.setattr(web, "STEPS", [("parse_files", "Fake read", "test step", fake)])
    assert client.post("/api/jobs/nope").status_code == 404
    r = client.post("/api/jobs/parse_files", json={"redo": True})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    busy = client.post("/api/jobs/parse_files")
    assert busy.status_code == 409 and busy.json()["error"] == "busy" and busy.json()["job"]["id"] == job_id
    assert client.get("/api/me").json()["job"]["id"] == job_id
    deadline = time.time() + 5
    while "PROGRESS" not in client.get(f"/api/jobs/{job_id}").json()["log_chunk"] and time.time() < deadline:
        time.sleep(0.05)
    first = client.get(f"/api/jobs/{job_id}").json()
    assert first["status"] == "running" and first["progress"] == {"stage": "Reading", "done": 1, "total": 2}
    gate.set()
    while client.get(f"/api/jobs/{job_id}").json()["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    done = client.get(f"/api/jobs/{job_id}", params={"since": first["next_offset"]}).json()
    assert done["status"] == "done" and done["result"] == {"suggestions": 4}
    assert "redo=True" in done["log_chunk"] and "PROGRESS" not in done["log_chunk"]
    assert client.get("/api/jobs").json()[0]["id"] == job_id
    assert client.get("/api/jobs/999999").status_code == 404


def test_legacy_ports(client):
    import db
    with db.connect() as c:
        slug = c.execute("SELECT slug FROM projects LIMIT 1").fetchone()[0]
        unit = c.execute("SELECT id FROM units LIMIT 1").fetchone()[0]
        row = c.execute("SELECT id FROM source_rows WHERE kind='sheet_row' LIMIT 1").fetchone()[0]
    assert client.get("/api/projects").json()
    p = client.get(f"/api/projects/{slug}").json()
    assert p["project"]["slug"] == slug and {f["type"] for f in p["key_facts"]} and "units" in p
    assert client.get("/api/units").json()["total"] >= 1
    assert client.get(f"/api/units/{unit}").json()["snapshots"]
    assert "issues" in client.get("/api/review/issues").json()
    assert "mappings" in client.get("/api/review/mappings?state=all").json()
    assert isinstance(client.get("/api/review/images").json(), list)
    assert isinstance(client.get("/api/review/facts").json(), list)
    src = client.get("/api/sources").json()
    assert src["files"] and src["failed"][0]["wa_id"] == "gone"
    assert client.get(f"/api/sources/rows/{row}").json()["kind"] == "row"
    assert client.get("/api/sources/messages/m1").json()["message"]["id"] == "m1"
    assert client.get("/api/reconciliation").json()["report"]["adds_up"]
    assert client.get("/api/pipeline").json()["counts"]["messages"] >= 7
    assert client.post("/api/reset", json={"confirm": "nope"}).status_code == 422


def test_spa_route(client):
    import api
    r = client.get("/app/updates")
    if not os.path.exists(os.path.join(api.DIST, "index.html")):
        assert r.status_code == 503 and "hasn't been built" in r.text
        pytest.skip("frontend/dist not built; SPA fallback checked for the not-built case only")
    assert r.status_code == 200 and '<div id="root">' in r.text
    assert client.get("/app").status_code == 200
    assert client.get("/app/more/pipeline").text == r.text  # client-side routes fall back to index.html
    assert client.get("/app/assets/missing.js").status_code == 404
    assert "import" not in client.get("/app/%2E%2E/app.py").text  # no files outside dist
