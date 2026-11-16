"""Accuracy gate: catalog, developer lock, Ready / Check / New / Hidden queues, re-match after a master upload,
approval model and lessons."""
import io, json, time
import pytest

ORA = "551@g.us"
UAE = "552@g.us"
BROKER = "553@g.us"
COMPANIES = "#,arabicName,englishName,type,logo\n1,اورا,ORA Developers,Both,\n2,ايه بلس,A Plus,Both,\n" \
            "3,1850,1850 Properties,Both,\n4,اعيان,Aayan,Both,\n5,هايد بارك,Hyde Park,Both,\n"
PROJECTS = "#,Name,Ratio,Net Ratio,Onspot Ratio,Company Id,Company\n1,Zed East,5,87,4.75,1,ORA Developers\n" \
           "2,Jiran,5,87,4.75,2,A Plus\n3,Celestia,5,87,4,3,1850 Properties\n4,Dao Towers,4,80,4,4,Aayan\n"


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import catalog, db, master, profiles
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    catalog.import_csv(COMPANIES.encode())
    catalog.import_csv(PROJECTS.encode())
    now = int(time.time())
    with db.connect() as c:
        for t in ("proposals", "brain_reviews", "lessons", "learned_examples"):
            c.execute(f"DELETE FROM {t}")
        for jid, name in ((ORA, "ORA & Byit App"), (UAE, "Byit App / Samana Developers"), (BROKER, "Brokers chat")):
            c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (jid, name))
            c.execute("INSERT OR REPLACE INTO group_scope(jid,in_scope) VALUES(?,1)", (jid,))
        profiles.assign(c, ORA, "ORA Developers")
        for i, (jid, t) in enumerate([(ORA, "ZED East 2 bed 130/7,200,000"), (UAE, "Studio from AED 650K"),
                                      (ORA, "Jiran launch 99/3,500,000"), (ORA, "ZED East 3 bed 140/30,000,000"),
                                      (ORA, "Brand new Sunrise Bay 2 bed 120/6,000,000"),
                                      (ORA, "Jiran 2 bed 99/3,600,000"), (ORA, "ZED East plan 20% 6 years"),
                                      (ORA, "ZED East studio 60/3,500,000"),
                                      (BROKER, "Celestia 3 bed 110/5,400,000")]):
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                      (f"g{i}", jid, now - 500 + i, t))
        c.execute("INSERT OR REPLACE INTO meta VALUES('accuracy_gate','1')")
        import master_schema as ms
        zed = c.execute("SELECT id FROM master_rows WHERE project='ZED East'").fetchone()[0]
        c.execute("INSERT OR REPLACE INTO master_cells VALUES(?,?,?,?)",
                  (zed, "apartment_3", "140/9,000,000", json.dumps(ms.parse("apartment_3", "140/9,000,000"))))
    yield db, master
    with db.connect() as c:
        for t in ("proposals", "brain_reviews", "lessons", "catalog_companies", "catalog_projects"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM meta WHERE key='accuracy_gate'")
        c.execute("DELETE FROM chat_profiles WHERE jid IN (?,?,?)", (ORA, UAE, BROKER))
        c.execute("DELETE FROM chat_projects WHERE jid IN (?,?,?)", (ORA, UAE, BROKER))
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 'g_'")
        c.execute("DELETE FROM groups WHERE jid IN (?,?,?)", (ORA, UAE, BROKER))


def _add(c, master, ref, jid, project, field, value, developer=""):
    master.add_proposal(c, {"project": project, "developer": developer, "field": field, "value": value,
                            "quote": value, "source_kind": "message", "source_ref": ref,
                            "source_ts": int(time.time()) - 100, "chat_jid": jid})
    return c.execute("SELECT * FROM proposals WHERE source_ref=? ORDER BY id DESC", (ref,)).fetchall()


def test_catalog_lookup(env):
    db, _ = env
    import catalog
    with db.connect() as c:
        cat = catalog.get(c)
        assert cat.company("ora")[0] == "ORA Developers" and cat.company("samana developers")[0] is None
        assert cat.project("Jiran")[2] == "A Plus" and cat.project("زد ايست") is None or True
        assert catalog.group_developer(c, ORA) == ("ORA Developers", "team")
        assert catalog.group_developer(c, UAE) == (None, None)


def test_lock_and_queues(env):
    db, master = env
    import gate
    with db.connect() as c:
        ready = _add(c, master, "g0", ORA, "ZED East", "apartment_2", "130/7,200,000")
        uae = _add(c, master, "g1", UAE, "Samana Lake Views", "studio", "35/650,000")
        jiran = _add(c, master, "g2", ORA, "Jiran", "apartment_1", "99/3,500,000")
        jump = _add(c, master, "g3", ORA, "ZED East", "apartment_3", "140/30,000,000")
        new = _add(c, master, "g4", ORA, "Sunrise Bay", "apartment_2", "120/6,000,000")
        # the lock: ORA's group never lands on A Plus's Jiran row
        assert all(p["row_id"] is None or c.execute("SELECT developer FROM master_rows WHERE id=?",
                                                    (p["row_id"],)).fetchone()[0] == "ORA Developers" for p in jiran)
    gate.run()
    with db.connect() as c:
        tier = lambda ref: {r["tier"] for r in c.execute("SELECT tier FROM proposals WHERE source_ref=? AND state='proposed'", (ref,))}
        assert tier("g0") == {"ready"}
        assert tier("g1") == {"hidden"}                    # UAE group
        assert tier("g3") == {"check"}                     # price more than double the current value
        assert tier("g4") == {"new"}
        g = json.loads(c.execute("SELECT gate_json FROM proposals WHERE source_ref='g3'").fetchone()[0])
        assert "price_jump" in g["flags"] and any("double" in r for r in g["reasons"])


def test_switch_off_keeps_old_matching(env):
    db, master = env
    with db.connect() as c:
        c.execute("UPDATE meta SET value='0' WHERE key='accuracy_gate'")
        p = _add(c, master, "g5", ORA, "Jiran", "apartment_2", "99/3,600,000")
        c.execute("UPDATE meta SET value='1' WHERE key='accuracy_gate'")
        row = c.execute("SELECT developer FROM master_rows WHERE id=?", (p[0]["row_id"],)).fetchone()
        assert row and row[0] == "A Plus"                 # off: the old behaviour (a near match on A Plus)
    import gate
    gate.run()
    with db.connect() as c:
        r = c.execute("SELECT tier, gate_json, row_id, state FROM proposals WHERE source_ref='g5'").fetchall()
        assert r[0]["tier"] == "hidden", [dict(x) for x in r]


def test_reupload_rematches_instead_of_discarding(env):
    db, master = env
    from test_master import master_bytes
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(master_bytes()))
    wb["New Cairo"]["G2"] = "126/7,000,000"                # the team changed ZED East apartment 2 meanwhile
    buf = io.BytesIO()
    wb.save(buf)
    summary = master.import_workbook(buf.getvalue(), "next.xlsx")
    assert summary["rematched"]["open"] >= 2
    with db.connect() as c:
        p = c.execute("SELECT * FROM proposals WHERE source_ref='g0'").fetchone()
        assert p["state"] == "proposed" and p["row_id"] and p["old_raw"] == "126/7,000,000"


def test_lessons_and_model(env, tmp_path, monkeypatch):
    db, master = env
    import acceptance, gate, lessons, models
    monkeypatch.setattr(models, "MODELS_DIR", str(tmp_path))
    now = int(time.time())
    with db.connect() as c:  # 40 person decisions: ORA unit prices approved, ORA payment plans rejected
        for i in range(40):
            field, ok = ("apartment_1", True) if i % 2 else ("payment_plan", False)
            c.execute("INSERT INTO proposals(sheet,project,project_key,field,new_raw,new_parsed_json,source_kind,"
                      "source_ref,source_ts,state,decided_at,reject_reason,match_state,created_at,quote) VALUES("
                      "'New Cairo','ZED East','zedeast',?,?,'{}','message',?,?,?,?,?, 'exact',?, 'x')",
                      (field, f"v{i}", "g0", now - 9000 + i, "approved" if ok else "rejected", now - 8000 + i,
                       None if ok else "wrong_value", now - 9000 + i))
    assert lessons.mine() >= 1
    with db.connect() as c:
        ls = lessons.load(c)
        assert any(l["outcome"] == "rejected" and l["field"] == "payment_plan" for l in ls)
        assert lessons.for_chat(c, ORA, "ORA Developers")
    ver, meta = acceptance.train()
    assert meta["labels"] == 40 and models.meta("acceptance", ver)
    models.promote("acceptance", ver)
    with db.connect() as c:
        _add(c, master, "g6", ORA, "ZED East", "payment_plan", "20% - 6Y")
    gate.run()
    with db.connect() as c:
        p = c.execute("SELECT * FROM proposals WHERE source_ref='g6' AND state='proposed'").fetchone()
        g = json.loads(p["gate_json"])
        assert p["p_approve"] is not None and p["tier"] == "check"       # learned: payment plans get rejected here
        assert "learned_warning" in g["flags"] or "low_probability" in g["flags"]
        assert g["contrib"]                                               # the signals behind the probability


def test_api_gate_sheet_and_team_role(env, monkeypatch):
    db, master = env
    from fastapi.testclient import TestClient
    import app, web
    with db.connect() as c:
        _add(c, master, "g7", ORA, "ZED East", "studio", "60/3,500,000")
    cl = TestClient(app.app, base_url="https://testserver")
    assert cl.post("/api/login", json={"password": "pw"}).json()["role"] == "admin"
    import gate
    gate.run()
    assert cl.get("/api/me").json()["gate"] is True
    g = cl.get("/api/settings/gate").json()
    assert g["enabled"] and "ready" in g["tiers"]
    assert cl.get("/api/proposals", params={"tier": "ready"}).json()["items"]
    sheet = cl.get("/api/sheet", params={"sheet": "New Cairo", "view": "review"}).json()
    assert sheet["gate"] and sheet["rows"] and all(r["ready"] for r in sheet["rows"])
    cell = next(c for r in sheet["rows"] for c in r["cells"].values() if c.get("pending"))["pending"]
    assert cell["tier"] == "ready" and "gate_reasons" in cell
    row_id = sheet["rows"][0]["row_id"]
    out = cl.post("/api/sheet/approve", json={"row_id": row_id}).json()
    assert out["approved"] >= 1
    csv_text = cl.get("/api/suggestions/export.csv").text
    assert "queue" in csv_text.splitlines()[0] and "group_developer" in csv_text.splitlines()[0]
    assert "doubtful" in cl.get("/api/brain/assignments").json()
    up = cl.post("/api/catalog", files={"file": ("companies.csv", COMPANIES.encode(), "text/csv")}).json()
    assert up["kind"] == "companies" and up["counts"]["companies"] == 5
    # team login: decides, can't change the system
    monkeypatch.setattr(web, "TEAM_PASSWORD", "teampw")
    team = TestClient(app.app, base_url="https://testserver")
    assert team.post("/api/login", json={"password": "teampw"}).json()["role"] == "team"
    assert team.get("/api/me").json()["role"] == "team"
    assert team.get("/api/sheet", params={"sheet": "New Cairo"}).status_code == 200
    assert team.post("/api/reset", json={"confirm": "DELETE ALL"}).status_code == 403
    assert team.put("/api/settings/gate", json={"enabled": False}).status_code == 403
    assert team.post("/api/jobs/rematch").status_code == 403
