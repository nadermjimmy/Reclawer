"""Master 'App Updates' workbook: parsers, import, proposals, approvals and both exports."""
import datetime, io, json, os
import openpyxl, pytest
from fastapi.testclient import TestClient

import cell_parse as cp

GRID_HEADER = ["DEVELOPER", "PROJECT", "Location", "Ready To Move", "Studio", "Apartemnt 1", "Apartment 2",
               "Apartment 3", "Apartment 4", "S Villa / Dublex", "Town", "Twin", "Standalone", "Office", "Clinic",
               "Shop", "Pharmacy", "Payment Plan", "Date"]
D = datetime.datetime(2026, 10, 29)


def grid_row(dev, proj, loc, apt2=None, pp="10% -8y", rtm=None):
    r = [dev, proj, loc, rtm, None, None, apt2] + [None] * 10 + [pp, D]
    return r


def master_bytes():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    s2 = wb.create_sheet("Sheet 2")
    s2.append(["Developers", "PROJECT", "Voice note", "Commission %", "Sales Name", "Sales Contact", "Date",
               "Feedback", "ID"])
    s2.append(["ORA Developers", "ZED West", "up", 5.0, "Youssef", 1121717000, D, None, None])
    s2.append(["Hyde Park", "Hyde Park New Cairo", None, "4 net", None, None, "Hold", None, None])
    nc = wb.create_sheet("New Cairo")
    nc.append(GRID_HEADER)
    nc.append(grid_row("ORA Developers", "ZED East", "E", "125/6,900,000"))
    nc.append(grid_row("A Plus", "Jiran", "O", "99/3,350,000", pp="15% -9y"))
    nc.append(grid_row("Mystery", "Odd Place", "de"))
    nc.auto_filter.ref = "A1:S4"
    sz = wb.create_sheet("Sokhna&new zayed")
    sz.append(GRID_HEADER[:9] + ["Dublex/S Villa"] + GRID_HEADER[10:])
    sz.append(grid_row("1850 Properties", "Celestia", "z", "110/5,000,000"))
    sz.append(grid_row("A Plus", "Jiran", "o", "90/2,000,000"))  # older copy of a New Cairo row
    sz.append(grid_row("Old Dev", "Old Coast", "n"))           # October/North Coast only here -> review
    sz.append(grid_row("Lux", "L Project", "L"))               # unknown code -> review
    cap = wb.create_sheet("New Capital ")
    cap.append(GRID_HEADER[:18] + [" Date", "Hold"])
    cap.append(grid_row("Aayan", "Dao Towers", "c", "120/6,000,000"))
    sep = wb.create_sheet("Separated update")
    sep.append(["DEVELOPER", "PROJECT", "Apartemnt 1", "Apartment 2", "Apartment 3", "Apartment 4", "Date",
                "commission", "Sales Name", "Number"])
    sep.append(["Retal", "Retal (Bayt Al Watan)", None, "rtm", "136/4,200,000", None, D, 2.5, "Ahmed", 1103010152])
    pod = wb.create_sheet("podcast")
    pod.append(["Developers", "Sales Name", "Sales Contact"])
    pod.append(["ORA", "Mona", 100])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- parsers ----------
def test_unit_cells():
    p = cp.parse_unit_cell("125/6,900,000")
    assert (p["area_sqm"], p["price_egp"], p["ok"]) == (125, 6900000, True)
    assert cp.format_unit_cell(p) == "125/6,900,000"
    assert cp.parse_unit_cell("so")["status"] == "sold_out" and cp.parse_unit_cell("Hold ")["ok"]
    assert cp.parse_unit_cell("Service Ap .")["status"] == "serviced"
    for bad in ("<--service", "10% -8y", "2nd floor", "28/86"):
        assert not cp.parse_unit_cell(bad)["ok"], bad


def test_payment_plan_commission_rtm():
    for raw, dp, y in [("10% -10y", 10, 10), ("10% - 8Y", 10, 8), ("10% -10", 10, 10), ("5% -7,5", 5, 7.5)]:
        p = cp.parse_payment_plan(raw)
        assert (p["down_payment_pct"], p["years"], p["ok"]) == (dp, y, True), raw
    assert cp.format_payment_plan(cp.parse_payment_plan("10% -6y")) == "10% - 6Y"
    assert cp.format_payment_plan(cp.parse_payment_plan("cash")) == "Cash"
    assert not cp.parse_payment_plan("10% - 7Y Villa")["ok"] and not cp.parse_payment_plan("Hold")["ok"]
    c = cp.parse_commission("1,5 net")
    assert (c["pct"], c["net"]) == (1.5, True) and cp.format_commission(c) == "1.5 net"
    assert cp.parse_commission(4.0)["pct"] == 4 and not cp.parse_commission("5 apart / 4 villa")["ok"]
    r = cp.parse_rtm("3 bed/16,400,000")
    assert (r["rtm_bedrooms"], r["rtm_price_egp"], r["rtm_scope"]) == (3, 16400000, "some")
    assert cp.parse_rtm(29200000.0)["rtm_price_egp"] == 29200000
    assert cp.parse_rtm("all project rtm")["rtm_scope"] == "all" and cp.parse_rtm("Hold")["row_status"] == "hold"
    assert cp.parse_rtm("RTM/2,700,000")["rtm_price_egp"] == 2700000
    assert cp.parse_rtm("admin / clinic")["product_tags"] == ["admin", "clinic"]
    assert not cp.parse_rtm("rtm -->>")["ok"]
    assert cp.normalize_project_key("La Vista ") == cp.normalize_project_key("LaVista")


# ---------- import -> propose -> approve -> export ----------
@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    summary = master.import_workbook(master_bytes(), "App Updates.xlsx")
    yield db, master, summary
    with db.connect() as c:  # other test modules count source messages in the shared DB
        c.execute("DELETE FROM source_messages WHERE wa_id IN ('u1','u2','u3')")


def test_import(env):
    db, master, s = env
    assert s["sheets"] == {"Sheet 2": 2, "New Cairo": 3, "Sokhna&new zayed": 4, "New Capital": 1,
                           "Separated update": 1}
    assert s["ignored_sheets"] == ["podcast"] and s["legacy_duplicates"] == 1
    with db.connect() as c:
        issues = {r["project"]: r["issue_type"] for r in c.execute(
            "SELECT i.issue_type, r.project FROM master_issues i JOIN master_rows r ON r.id=i.row_id")}
    assert issues == {"Odd Place": "unknown_location", "Old Coast": "legacy_location",
                      "L Project": "unknown_location"}


def _src(db, wa_id, ts, text):
    import fixtures
    with db.connect() as c:
        c.execute("INSERT OR IGNORE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                  (wa_id, fixtures.UAE, ts, text))


def test_propose_and_decide(env, monkeypatch):
    db, master, _ = env
    import llm, proposals
    _src(db, "u1", 5000, "ZED East 2 bed now 130/7,500,000; Jirian 2br 99/3,350,000; commission ZED West 4.5")
    _src(db, "u2", 5100, "New launch Sunset Bay in New Zayed, 3 bed 160/9,000,000, 10% over 8 years")
    _src(db, "u3", 4000, "older: ZED East 2 bed 128/7,000,000")

    def fake(system, content, schema, max_tokens=16000):
        assert "broker team's project sheet" in system
        ups = []
        for m in json.loads(content).get("messages", []):
            u = lambda **k: ups.append(dict({"source_id": m["source_id"], "developer": "", "location_code": "",
                                             "quote": m["text"]}, **k))
            if m["source_id"] == "u1":
                u(project="ZED East", field="apartment_2", value="130/7,500,000")
                u(project="Jirian", field="apartment_2", value="99/3,350,000")  # same value -> nothing
                u(project="Hyde Park New Cairo", field="commission", value="4.5")
                u(project="Hyde Park New Cairo", field="sales_name", value="Mona")  # never allowed
            if m["source_id"] == "u2":
                u(project="Sunset Bay", field="apartment_3", value="160/9,000,000", location_code="z")
                u(project="Sunset Bay", field="payment_plan", value="10% - 8Y", location_code="z")
            if m["source_id"] == "u3":
                u(project="ZED East", field="apartment_2", value="128/7,000,000")
        return {"updates": ups}

    monkeypatch.setattr(llm, "call_json", fake)
    import jobs
    jobs.run_now("propose", proposals.propose)
    with db.connect() as c:
        rows = {(r["project"], r["field"], r["state"]): dict(r) for r in c.execute("SELECT * FROM proposals")}
    zed = rows[("ZED East", "apartment_2", "proposed")]
    assert zed["old_raw"] == "125/6,900,000" and zed["new_raw"] == "130/7,500,000" and zed["sheet"] == "New Cairo"
    assert rows[("ZED East", "apartment_2", "superseded")]["new_raw"] == "128/7,000,000"  # older quote loses
    assert not any(k[0] == "Jirian" for k in rows), "unchanged value must not be proposed"
    assert not any(k[1] == "sales_name" for k in rows)
    hp = rows[("Hyde Park New Cairo", "commission", "proposed")]
    assert hp["old_raw"] == "4 net" and "lifts Hold/Hide" in hp["warning"]
    sun = rows[("Sunset Bay", "apartment_3", "proposed")]
    assert sun["row_id"] is None and sun["sheet"] == "Sokhna&new zayed" and sun["match_state"] == "new"

    # near match: chat name differs slightly from the sheet; approving saves the alias
    with db.connect() as c:
        n = master.add_proposal(c, {"project": "Zed Eastt", "field": "payment_plan", "value": "5% -10y",
                                    "source_kind": "message", "source_ref": "u1", "source_ts": 5000})
        near = c.execute("SELECT * FROM proposals WHERE project='Zed Eastt'").fetchone()
    assert n == 1 and near["match_state"] == "near" and "Near match" in near["warning"]
    ids = [zed["id"], hp["id"], sun["id"], rows[("Sunset Bay", "payment_plan", "proposed")]["id"], near["id"]]
    done, skipped = master.decide(ids, "approve")
    assert (done, skipped) == (5, [])
    with db.connect() as c:
        assert c.execute("SELECT project_key FROM master_aliases WHERE alias_key='zedeastt'").fetchone()[0] == "zedeast"
        new = c.execute("SELECT * FROM master_rows WHERE state='new'").fetchone()
        assert new["project"] == "Sunset Bay" and new["sheet"] == "Sokhna&new zayed"
        assert master.current(c, new["id"], "payment_plan")[0] == "10% - 8Y"
        # the next chat mention of the alias matches automatically
        hit = master.match(c, "Zed Eastt", "", ["New Cairo"])["New Cairo"]
        assert hit[1] == "alias"


def test_team_export(env):
    db, master, _ = env
    import master_export
    out = openpyxl.load_workbook(io.BytesIO(master_export.team_file()))
    today = datetime.date.today()
    nc = out["New Cairo"]
    assert nc["G2"].value == "130/7,500,000" and nc["R2"].value == "5% - 10Y"  # approved values in place
    assert nc["S2"].value.date() == today and nc["S3"].value == D             # date only on changed rows
    assert nc["R3"].value == "15% - 9Y"                                         # every plan normalised
    s2 = out["Sheet 2"]
    assert s2["D3"].value == 4.5 and s2["G3"].value.date() == today            # Hold replaced by approval date
    assert s2["E2"].value == "Youssef" and s2["F2"].value == 1121717000        # sales columns untouched
    sz = out["Sokhna&new zayed"]
    assert [sz.cell(6, i).value for i in (1, 2, 3, 8)] == [None, "Sunset Bay", "Z", "160/9,000,000"]
    assert sz.cell(6, 18).value == "10% - 8Y"
    assert out["podcast"]["B2"].value == "Mona" and nc.auto_filter.ref == "A1:S4"


def test_sql_export(env):
    import master_export
    wb = openpyxl.load_workbook(io.BytesIO(master_export.sql_file()))
    rows = list(wb["New Cairo"].iter_rows(values_only=True))
    head = rows[0]
    zed = dict(zip(head, next(r for r in rows if r[4] == "ZED East")))
    assert zed["apartment_2_area_sqm"] == 130 and zed["apartment_2_price_egp"] == 7500000
    assert zed["down_payment_pct"] == 5 and zed["years"] == 10 and zed["location_name"] == "East / New Cairo"
    units = list(wb["units"].iter_rows(values_only=True))
    assert any(r[3] == "Sunset Bay" and r[5] == "Apartment 3" and r[7] == 9000000 for r in units)
    s2 = list(wb["Sheet 2"].iter_rows(values_only=True))
    com = [dict(zip(s2[0], r)) for r in s2[1:]]
    hp = next(r for r in com if r["project"] == "Hyde Park New Cairo")
    assert hp["commission_pct"] == 4.5 and hp["commission_net"] is False


def test_issue_resolution_and_pages(env):
    db, master, _ = env
    import app
    client = TestClient(app.app, base_url="https://testserver")
    client.post("/login", data={"password": "pw"})
    with db.connect() as c:
        iid = c.execute("SELECT i.id FROM master_issues i JOIN master_rows r ON r.id=i.row_id "
                        "WHERE r.project='Old Coast'").fetchone()[0]
    r = client.post(f"/master/issues/{iid}", data={"action": "move", "sheet": "New Cairo"}, follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as c:
        row = c.execute("SELECT * FROM master_rows WHERE project='Old Coast'").fetchone()
        assert row["state"] == "moved" and master.match(c, "Old Coast", "", ["New Cairo"])
    for path in ["/master", "/master?show=all", "/updates", "/updates?state=approved", "/updates?match=near", "/"]:
        assert client.get(path).status_code == 200, path
    assert "6b. Propose sheet updates" in client.get("/").text
    team = client.get("/master/export/team")
    assert team.status_code == 200 and team.headers["content-type"].startswith("application/vnd.openxml")
    assert openpyxl.load_workbook(io.BytesIO(team.content))["New Cairo"].cell(5, 2).value == "Old Coast"
    assert client.get("/master/export/sql").status_code == 200
    r = client.post("/updates/decide", data={"id": ["999999"], "action": "approve"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/master/upload", files={"file": ("x.xlsx", b"not a workbook")}, follow_redirects=False)
    assert "error=" in r.headers["location"]


@pytest.mark.skipif(not os.getenv("MASTER_XLSX"), reason="set MASTER_XLSX to the real workbook to run")
def test_real_workbook_counts(env):
    db, master, _ = env
    s = master.import_workbook(open(os.environ["MASTER_XLSX"], "rb").read(), "real.xlsx")
    assert s["sheets"] == {"Sheet 2": 1246, "New Cairo": 724, "Sokhna&new zayed": 328, "New Capital": 295,
                           "Separated update": 92}
