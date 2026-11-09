"""Phase 5: Egyptian notation parsing (raw kept verbatim), spreadsheet templates read in code, extraction cache."""
import io, json, time
import openpyxl, pytest

CHAT = "331@g.us"
CHAT_SAME_DEV = "332@g.us"
CHAT_OTHER_DEV = "333@g.us"


# ---------- cell_parse ----------
@pytest.mark.parametrize("raw,area,price", [
    ("١٣٠ م / ٧.٥ مليون ج.م", 130, 7_500_000), ("130 m2/7,5 مليون", 130, 7_500_000),
    ("120م/6500 ألف جنيه", 120, 6_500_000), ("95 sqm / 4.2 million EGP", 95, 4_200_000),
    ("125/6,900,000", 125, 6_900_000)])
def test_eg_unit_cells(raw, area, price):
    import cell_parse as cp
    p = cp.parse_unit_cell(raw)
    assert (p["area_sqm"], p["price_egp"], p["ok"]) == (area, price, True) and p["raw"] == raw  # raw stays verbatim


@pytest.mark.parametrize("raw,dp,years,ok", [
    ("مقدم ١٠٪ على ٨ سنين", 10, 8, True), ("10% down payment over 8 years", 10, 8, True),
    ("8 سنوات 5% مقدم", 5, 8, True), ("١٥٪ مقدم و ٩ سنوات", 15, 9, True), ("10% - 7Y Villa", 10, 7, False)])
def test_eg_payment_plans(raw, dp, years, ok):
    import cell_parse as cp
    p = cp.parse_payment_plan(raw)
    assert (p["down_payment_pct"], p["years"], p["ok"]) == (dp, years, ok)
    if ok:
        assert cp.format_payment_plan(p) == f"{dp}% - {years}Y" and p["raw"] == raw


def test_eg_rtm_and_commission():
    import cell_parse as cp
    assert cp.parse_rtm("3 ghoraf/16,400,000")["rtm_bedrooms"] == 3
    r = cp.parse_rtm("غرفتين/9 مليون")
    assert (r["rtm_bedrooms"], r["rtm_price_egp"]) == (2, 9_000_000)
    c = cp.parse_commission("٤ صافي")
    assert (c["pct"], c["net"], cp.format_commission(c)) == (4, True, "4 net")
    assert cp.parse_commission("٣٫٥")["pct"] == 3.5
    for bad in ("28/86", "2nd floor", "<--service", "Hold"):  # unchanged: still sent to review
        assert not cp.parse_unit_cell(bad)["ok"] or bad == "Hold"


# ---------- templates + cache ----------
def _xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Prices"
    ws.append(["Project", "Apartment 2", "Payment Plan", "Sales Name"])
    ws.append(["ZED East", "131/7,650,000", "10% - 8Y", "Mona"])
    ws.append(["ZED West", "140/8,100,000", "", "Ali"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master, history, parse_files, jobs
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    now = int(time.time())
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        for jid, name in ((CHAT, "ORA & Byit App"), (CHAT_SAME_DEV, "ORA Developers & Byit App"),
                          (CHAT_OTHER_DEV, "A Plus & Byit App")):
            c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (jid, name))
            c.execute("INSERT OR REPLACE INTO group_scope(jid,in_scope) VALUES(?,1)", (jid,))
        c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text,has_media,file_name) "
                  "VALUES('t5f',?,?,?,1,'ORA prices.xlsx')", (CHAT, now - 500, ""))
        history.save_file(c, "t5f", _xlsx(), "ORA prices.xlsx",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        fwd = "ZED East 2 bed apartments now 132/7,700,000 limited units"
        for wa, jid in (("t5a", CHAT), ("t5b", CHAT_SAME_DEV), ("t5c", CHAT_OTHER_DEV)):
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                      (wa, jid, now - 400, fwd))
    jobs.run_now("parse", parse_files.parse_all)
    with db.connect() as c:
        fid = c.execute("SELECT file_id FROM file_occurrences WHERE wa_id='t5f'").fetchone()[0]
    yield db, master, fid
    with db.connect() as c:
        for t in ("proposals", "sheet_templates", "extraction_cache"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM source_rows WHERE file_id=?", (fid,))
        c.execute("DELETE FROM file_occurrences WHERE file_id=?", (fid,))
        c.execute("DELETE FROM sheet_layouts WHERE file_id=?", (fid,))
        c.execute("DELETE FROM source_files WHERE id=?", (fid,))
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 't5%'")
        c.execute("DELETE FROM processed_batches WHERE batch_key LIKE 'u:t5%' OR batch_key LIKE 'ur:%'")
        for jid in (CHAT, CHAT_SAME_DEV, CHAT_OTHER_DEV):
            c.execute("DELETE FROM groups WHERE jid=?", (jid,))
            c.execute("DELETE FROM group_scope WHERE jid=?", (jid,))
            c.execute("DELETE FROM chat_profiles WHERE jid=?", (jid,))


def test_template_confirm_and_read_in_code(env, monkeypatch):
    db, master, fid = env
    import llm, proposals, templates
    from fastapi.testclient import TestClient
    import app as app_mod
    cl = TestClient(app_mod.app, base_url="https://testserver")
    cl.post("/api/login", json={"password": "pw"})
    t = cl.get(f"/api/sources/files/{fid}/template", params={"sheet": "Prices"}).json()
    assert t["headers"][:4] == ["Project", "Apartment 2", "Payment Plan", "Sales Name"] and not t["confirmed"]
    assert t["mapping"] == {"0": "project", "1": "apartment_2", "2": "payment_plan"}  # no sales column, ever
    bad = cl.put(f"/api/sources/files/{fid}/template", json={"sheet": "Prices", "mapping": {"3": "sales_name"}})
    assert bad.status_code == 422
    ok = cl.put(f"/api/sources/files/{fid}/template", json={"sheet": "Prices", "mapping": t["mapping"]}).json()
    assert ok["confirmed"]
    with db.connect() as c:  # flag off: Claude still reads it
        assert all(u.get("local_updates") is None for u in proposals.sheet_units(c, [CHAT], skip=set()))
    monkeypatch.setattr(templates, "TEMPLATES", True)
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: pytest.fail("no LLM call for a confirmed template"))
    written, failed = proposals.run_stage_live(lambda *_: None, "sheets", [CHAT])
    assert written >= 2 and failed == 0
    with db.connect() as c:
        ps = c.execute("SELECT * FROM proposals WHERE source_kind='row' ORDER BY id").fetchall()
        assert {(p["project"], p["field"], p["new_raw"]) for p in ps} >= {("ZED East", "apartment_2", "131/7,650,000"),
                                                                          ("ZED West", "apartment_2", "140/8,100,000")}
        assert not any("Mona" in (p["new_raw"] or "") for p in ps)


def test_extraction_cache_same_developer_only(env, monkeypatch):
    db, master, fid = env
    import extraction_cache as xc, llm, profiles, proposals
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        profiles.assign(c, CHAT, "ORA Developers")
        profiles.assign(c, CHAT_SAME_DEV, "ORA Developers")
        profiles.assign(c, CHAT_OTHER_DEV, "A Plus")
    calls = []

    def fake(system, content, schema, max_tokens=0, model=None, **kw):
        body = json.loads(content)
        calls.append([m["source_id"] for m in body["messages"]])
        return {"updates": [{"source_id": m["source_id"], "developer": "", "project": "ZED East", "location_code": "",
                             "field": "apartment_2", "value": "132/7,700,000", "quote": m["text"]}
                            for m in body["messages"] if "ZED East" in m["text"]]}

    monkeypatch.setattr(llm, "call_json", fake)
    monkeypatch.setattr(xc, "ENABLED", True)
    proposals.run_stage_live(lambda *_: None, "text", [CHAT])
    assert calls == [["t5a"]]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM extraction_cache WHERE source_kind='message'").fetchone()[0] >= 1
    calls.clear()
    proposals.run_stage_live(lambda *_: None, "text", [CHAT_SAME_DEV])   # forwarded into the same developer's group
    assert calls == []
    with db.connect() as c:
        p = c.execute("SELECT * FROM proposals WHERE source_ref='t5b'").fetchone()
        assert p and p["new_raw"] == "132/7,700,000"                     # stored as its own suggestion
    proposals.run_stage_live(lambda *_: None, "text", [CHAT_OTHER_DEV])  # another developer: read again
    assert calls == [["t5c"]]
