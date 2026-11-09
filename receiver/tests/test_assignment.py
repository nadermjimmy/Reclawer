"""Phase 0: the ops team assigns a group to a developer and its projects; ingestion and the brain use it."""
import io, json, time
import openpyxl, pytest

ORA = "771@g.us"
OTHER = "772@g.us"


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
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (OTHER, "Random chat"))
    yield db, master
    with db.connect() as c:
        for t in ("proposals", "chat_projects", "assignment_log", "chat_profiles", "brain_reviews"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM groups WHERE jid IN (?,?)", (ORA, OTHER))


def test_assign_rules_and_log(env):
    db, _ = env
    import profiles
    with db.connect() as c:
        assert profiles.get(c, ORA)["source"] == "auto"            # filled from the name: only a suggestion
        with pytest.raises(ValueError):
            profiles.assign(c, ORA, "ORA Devs Typo")                 # not a master developer
        projects = {e["project"]: e["project_key"] for e in profiles.developer_projects(c, "ORA Developers")}
        assert {"ZED East", "ZED West"} <= set(projects)
        with pytest.raises(ValueError):
            profiles.assign(c, ORA, "ORA Developers", ["jiran"])     # A Plus's project, not ORA's
        p = profiles.assign(c, ORA, "ORA Developers", [projects["ZED East"]])
        assert p["source"] == "team" and p["projects"] == ["ZED East"] and p["assigned_at"]
        assert "assigned this group to these projects: ZED East" in profiles.describe(p)
        log = [(r["field"], json.loads(r["new"])) for r in c.execute("SELECT * FROM assignment_log WHERE jid=?", (ORA,))]
        assert ("developer", "ORA Developers") in log and ("projects", ["zedeast"]) in log
        profiles.assign(c, OTHER, "Brand New Dev", allow_new=True)    # "Not on the sheet yet"
        assert profiles.get(c, OTHER)["developer"] == "Brand New Dev"


def test_matching_prefers_assignment_and_flags_outside(env):
    db, master = env
    now = int(time.time())
    with db.connect() as c:
        n = master.add_proposal(c, {"project": "Zed E", "developer": "", "field": "apartment_2",
                                    "value": "130/7,500,000", "source_kind": "message", "source_ref": "as1",
                                    "source_ts": now, "chat_jid": ORA})
        p = c.execute("SELECT p.*, r.project rp FROM proposals p JOIN master_rows r ON r.id=p.row_id "
                      "WHERE source_ref='as1'").fetchone()
        assert n == 1 and p["rp"] == "ZED East"                       # found through the team's assignment
        # an exact name outside the assignment still matches, but only as a near match with a warning
        master.add_proposal(c, {"project": "Jiran", "developer": "", "field": "apartment_2", "value": "99/3,900,000",
                                "source_kind": "message", "source_ref": "as2", "source_ts": now, "chat_jid": ORA})
        q = c.execute("SELECT * FROM proposals WHERE source_ref='as2'").fetchone()
        assert q["match_state"] == "near" and master.OUTSIDE_ASSIGNMENT in q["warning"]
        # a group with no assignment behaves as before
        master.add_proposal(c, {"project": "Jiran", "developer": "", "field": "apartment_3", "value": "120/5,000,000",
                                "source_kind": "message", "source_ref": "as3", "source_ts": now, "chat_jid": "none@g.us"})
        assert c.execute("SELECT match_state FROM proposals WHERE source_ref='as3'").fetchone()[0] == "exact"


def test_brain_never_auto_approves_outside_assignment(env):
    db, master = env
    import brain, profiles
    with db.connect() as c:
        q = c.execute("SELECT * FROM proposals WHERE source_ref='as2'").fetchone()
        ok, why = brain.safe_to_approve(c, q, "high", profiles.assigned_keys(c, ORA))
        p = c.execute("SELECT * FROM proposals WHERE source_ref='as1'").fetchone()
        ok2, _ = brain.safe_to_approve(c, p, "high", profiles.assigned_keys(c, ORA))
    assert not ok and "assigned projects" in why and ok2


def test_assignment_survives_master_reupload(env):
    db, master = env
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "again.xlsx")            # same rows, new ids
    with db.connect() as c:
        r = c.execute("SELECT * FROM chat_projects WHERE jid=?", (ORA,)).fetchone()
        assert r["status"] == "ok" and r["project_label"] == "ZED East"
    wb = openpyxl.load_workbook(io.BytesIO(master_bytes()))
    wb["New Cairo"].delete_rows(2)                                   # ZED East removed from the sheet
    buf = io.BytesIO()
    wb.save(buf)
    summary = master.import_workbook(buf.getvalue(), "without-zed.xlsx")
    assert {"jid": ORA, "project": "ZED East"} in summary["assignments_missing"]
    with db.connect() as c:
        assert c.execute("SELECT status FROM chat_projects WHERE jid=?", (ORA,)).fetchone()[0] == "missing"
    master.import_workbook(master_bytes(), "back.xlsx")               # it comes back: re-linked by name
    with db.connect() as c:
        assert c.execute("SELECT status FROM chat_projects WHERE jid=?", (ORA,)).fetchone()[0] == "ok"


def test_api_assignment_unassigned_and_bulk(env):
    import app
    from fastapi.testclient import TestClient
    client = TestClient(app.app, base_url="https://testserver")
    client.post("/api/login", json={"password": "pw"})
    a = client.get(f"/api/chats/{ORA}/assignment").json()
    assert "ORA Developers" in a["developers"] and any(p["project"] == "ZED East" for p in a["projects"])
    assert a["assigned"][0]["project_label"] == "ZED East"
    r = client.put(f"/api/chats/{ORA}/assignment", json={"developer": "Nope"})
    assert r.status_code == 422
    lst = client.get("/api/chats", params={"filter": "unassigned"}).json()
    assert all(not c["assigned"] for c in lst["chats"]) and ORA not in [c["jid"] for c in lst["chats"]]
    r = client.post("/api/chats/assign", json={"jids": [OTHER], "developer": "Hyde Park"})
    assert r.status_code == 200 and r.json()["assigned"] == [OTHER]
    mine = next(c for c in client.get("/api/chats").json()["chats"] if c["jid"] == ORA)
    assert mine["assigned"] and mine["assigned_projects"] == 1
    sheet = client.get("/api/sheet", params={"sheet": "New Cairo", "q": "zed east"}).json()
    assert any("ORA & Byit App" in row["groups"] for row in sheet["rows"])
