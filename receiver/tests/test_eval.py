"""Phase 1: row keys survive re-uploads, reject reasons, brain flags, gold export and the eval metrics."""
import io, json, os, time
import openpyxl, pytest

CHAT = "661@g.us"


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    with db.connect() as c:
        for t in ("proposals", "brain_reviews", "master_aliases", "llm_usage"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (CHAT, "ORA & Byit App"))
        now = int(time.time())
        for i, t in enumerate(["زد ايست شقة 130/7,500,000", "Jiran 2 bed 99/3,900,000", "عميل عنده شقة ريسيل",
                               "ZED East studio 60/3,100,000", "Sunset Bay launch 160/9,000,000"]):
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                      (f"ev{i}", CHAT, now - 1000 + i, t))
    yield db, master
    with db.connect() as c:
        for t in ("proposals", "brain_reviews", "master_aliases", "llm_usage", "chat_profiles", "chat_projects"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 'ev%'")
        c.execute("DELETE FROM groups WHERE jid=?", (CHAT,))


def _add(c, master, ref, project, field, value, developer="ORA Developers"):
    master.add_proposal(c, {"project": project, "developer": developer, "field": field, "value": value,
                            "quote": value, "source_kind": "message", "source_ref": ref,
                            "source_ts": int(time.time()), "chat_jid": CHAT})
    return c.execute("SELECT * FROM proposals WHERE source_ref=? ORDER BY id DESC", (ref,)).fetchone()


def test_row_keys_and_relink_without_replaying_values(env):
    db, master = env
    with db.connect() as c:
        p = _add(c, master, "ev0", "ZED East", "apartment_2", "130/7,500,000")
        assert p["row_key"] == p["first_row_key"] == "oradevelopers|zedeast|New Cairo" and p["first_match_state"] == "exact"
    master.decide_detail([p["id"]], "approve")
    from test_master import master_bytes
    wb = openpyxl.load_workbook(io.BytesIO(master_bytes()))
    wb["New Cairo"]["G2"] = "140/8,000,000"   # the team changed that cell in the new upload
    buf = io.BytesIO()
    wb.save(buf)
    summary = master.import_workbook(buf.getvalue(), "next.xlsx")
    assert summary["relinked"]["linked"] >= 1
    with db.connect() as c:
        q = c.execute("SELECT * FROM proposals WHERE id=?", (p["id"],)).fetchone()
        row = c.execute("SELECT * FROM master_rows WHERE project='ZED East'").fetchone()
        assert q["row_id"] is None and q["label_row_id"] == row["id"]        # label re-linked, value not replayed
        assert master.current(c, row["id"], "apartment_2")[0] == "140/8,000,000"


def test_reject_reason_and_actor(env):
    db, master = env
    with db.connect() as c:
        p = _add(c, master, "ev2", "زد ايست ريسيل", "apartment_1", "110/3,000,000")
        j = _add(c, master, "ev1", "Jiran", "apartment_2", "99/3,900,000", developer="A Plus")
    master.decide_detail([p["id"]], "reject", reason="not_an_offer")
    master.decide_detail([j["id"]], "reject", actor="brain")
    with db.connect() as c:
        r = c.execute("SELECT decided_by, reject_reason FROM proposals WHERE id=?", (p["id"],)).fetchone()
        b = c.execute("SELECT decided_by FROM proposals WHERE id=?", (j["id"],)).fetchone()
    assert r["decided_by"] is None and r["reject_reason"] == "not_an_offer" and b["decided_by"] == "brain"
    import app
    from fastapi.testclient import TestClient
    client = TestClient(app.app, base_url="https://testserver")
    client.post("/api/login", json={"password": "pw"})
    assert client.post("/api/proposals/reason", json={"ids": [p["id"]], "reason": "nope"}).status_code == 422
    assert client.post("/api/proposals/reason", json={"ids": [p["id"], j["id"]], "reason": "duplicate"}).json()["updated"] == 1


def test_brain_flags_off_keep_items_for_a_person(env, monkeypatch):
    db, master = env
    import brain, llm
    with db.connect() as c:
        p = _add(c, master, "ev3", "ZED East", "studio", "60/3,100,000")
    monkeypatch.setattr(brain, "AUTO_APPROVE", False)
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: {"decisions": [
        {"proposal_id": p["id"], "verdict": "update_existing", "row_id": p["row_id"], "confidence": "high",
         "reason": "ZED East"}]})
    t = brain.review(lambda *_: None, jids=[CHAT])
    with db.connect() as c:
        st = c.execute("SELECT state FROM proposals WHERE id=?", (p["id"],)).fetchone()[0]
        why = c.execute("SELECT reason FROM brain_reviews WHERE proposal_id=?", (p["id"],)).fetchone()[0]
    assert st == "proposed" and t["approved"] == 0 and "auto-approve is off" in why


def test_gold_and_metrics(env, tmp_path):
    db, master = env
    from eval import build_gold, run_eval
    with db.connect() as c:
        s = _add(c, master, "ev4", "Sunset Bay", "apartment_3", "160/9,000,000", developer="")
        c.execute("INSERT INTO llm_usage(ts,purpose,model,input_tokens,output_tokens,cache_read,cache_write,batch) "
                  "VALUES(?,?,?,?,?,?,?,?)", (int(time.time()), "brain", "claude-opus-5-5", 1_000_000, 100_000, 0, 0, 0))
    master.decide_detail([s["id"]], "approve", sheet="Sokhna&new zayed")
    path, meta = build_gold.build(str(tmp_path / "gold"))
    assert meta["by_source_kind"]["message"] == 3 and meta["by_label"] == {"row": 1, "noise": 1, "new_project": 1}
    m, e, meta2 = build_gold.load(path)
    assert {x["label"]["type"] for x in m} == {"row", "noise", "new_project"} and meta2["total"] == 3
    zed = next(x for x in e if x["source_ref"] == "ev0")
    assert zed["source"]["text"].startswith("زد ايست") and zed["decision"] == "approved"
    r = run_eval.run(path)
    assert r["matching"]["message"]["precision"] == 1.0 and r["matching"]["message"]["recall"] == 1.0
    assert r["matching"]["message"]["new_vs_existing_acc"] == 1.0
    assert r["extraction"]["by_source_kind"]["message"]["accepted"] == 1.0   # noise rejection isn't a value error
    assert r["llm"]["usd_last_days"] == 6.0                                      # 1M in x $4 + 0.1M out x $20
    md = run_eval.report_md(r)
    assert "| message | 3 |" in md and "Cross-script misses" in md


def test_cross_script_miss_listed_and_alias_leak_blocked(env):
    db, master = env
    from eval import run_eval
    now = int(time.time())
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO master_aliases(alias_key,project_key,label,confirmed_at,developer_key) "
                  "VALUES('ماونتنفيو','zedeast','x',?, '')", (now + 100,))
        item = {"id": 0, "source_kind": "message", "project": "ماونتن فيو", "developer": "", "chat_jid": None,
                "chat_developer": "", "field": "apartment_2", "sheet": "New Cairo", "decided_at": now,
                "label": {"type": "row", "row_key": "oradevelopers|zedeast|New Cairo"}}
        out, cross, misses = run_eval.matching_metrics(c, [item])
    assert out["message"]["recall"] == 0.0 and cross and cross[0]["chat_says"] == "ماونتن فيو"  # alias came later


def test_llm_replay_respects_budget(env):
    from eval import run_eval
    ex = [{"source_kind": "message", "decision": "approved", "source": {"text": "x" * 10000}, "chat_jid": None,
           "field": "apartment_2", "value": "1/1,000,000"}] * 50
    r = run_eval.llm_replay(ex, 50, max_usd=0.01)
    assert "skipped" in r and r["estimated_usd"] > 0.01
