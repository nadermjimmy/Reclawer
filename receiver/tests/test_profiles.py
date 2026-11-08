"""Developer profiles, developer-aware matching, team rounding and the missing-data insight."""
import time
import pytest

ORA = "991@g.us"


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (ORA, "ORA & Byit App"))
    yield db, master
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("DELETE FROM chat_profiles")
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 'pf%'")
        c.execute("DELETE FROM groups WHERE jid=?", (ORA,))


def test_profile_from_group_name(env):
    db, _ = env
    import profiles
    assert profiles.developer_from_name("Soma bay & Byit App") == "Soma bay"
    assert profiles.developer_from_name("Emeraled & Byit App") == "Emeraled"
    assert profiles.developer_from_name("Resale (gouna - new cairo)") is None
    with db.connect() as c:
        p = profiles.get(c, ORA)
        assert p["developer"] == "ORA Developers" and p["source"] == "auto"   # matched to the master's developer
        p = profiles.put(c, ORA, "ORA Developers", "starting_prices", ["villa", "nope"], "Villas on request")
        assert p["on_request"] == ["villa"] and p["source"] == "team"
        line = profiles.describe(p)
        assert "ORA Developers's own group" in line and "starting prices" in line and "S Villa / Duplex" in line


def test_developer_rows_first_and_rounding(env):
    db, master = env
    with db.connect() as c:
        hit = master.match(c, "Zed", "ORA Developers", ["New Cairo"])["New Cairo"]   # loose, within ORA's rows
        assert hit[0]["project"] == "ZED East" and hit[1] == "near"
        assert master.match(c, "Zed", "", ["New Cairo"]) == {}                         # no developer: too short
        n = master.add_proposal(c, {"project": "ZED East", "developer": "ORA Developers", "field": "apartment_2",
                                    "value": "130/7,512,000", "source_kind": "message", "source_ref": "pf1",
                                    "source_ts": int(time.time())})
        p = c.execute("SELECT new_raw FROM proposals WHERE source_ref='pf1'").fetchone()
        assert n == 1 and p["new_raw"] == "130/7,550,000"                             # up to the next 50,000
        # a value that rounds to what the sheet holds is not a new suggestion
        assert master.add_proposal(c, {"project": "ZED East", "developer": "ORA Developers", "field": "apartment_2",
                                       "value": "125/6,861,000", "source_kind": "message", "source_ref": "pf2",
                                       "source_ts": int(time.time())}) == 0


def test_developer_name_is_not_a_project(env, monkeypatch):
    db, master = env
    import proposals
    with db.connect() as c:
        n0 = c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    n = proposals._store({"m": ["message", "pf3", int(time.time()), "ORA Developers"]},
                         [{"source_id": "m", "developer": "", "project": "ORA Developers", "location_code": "",
                           "field": "apartment_1", "value": "60/3,000,000", "quote": "q"},
                          {"source_id": "m", "developer": "", "project": "ZED East", "location_code": "",
                           "field": "apartment_1", "value": "60/3,000,000", "quote": "q"}])
    with db.connect() as c:
        rows = c.execute("SELECT project, developer FROM proposals WHERE source_ref='pf3'").fetchall()
    assert [r["project"] for r in rows] == ["ZED East"] and rows[0]["developer"] == "ORA Developers"


def test_missing_data_insight(env):
    db, _ = env
    import api
    later = int(time.time()) + 30 * 86400        # a month on: ORA's rows have gone quiet
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES('pf9',?,?,'hello')",
                  (ORA, later - 3600))
        out = api.ins_missing_data(c, later - 7 * 86400, later)
    ora = next(x for x in out if x["developer"] == "ORA Developers")
    assert ora["stale_count"] >= 1 and {p["project"] for p in ora["projects"]} & {"ZED East", "ZED West"}
    rep = ora["reps"][0]
    assert rep["phone"] == "+20 112 171 7000" and rep["wa_link"] == "https://wa.me/201121717000" and rep["name"] == "Youssef"
    assert ora["shares"] == "starting_prices" and "starting prices" in ora["message"]
    assert ora["on_request"] == ["S Villa / Duplex"]
