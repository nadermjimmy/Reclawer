"""Phase 3: learning memory (person decisions only, survives re-upload, balanced retrieval), few-shot in the user
turn with an unchanged system prompt (live and batch), rule suggestions (never applied)."""
import json, os, time
import pytest

CHAT = "551@g.us"


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    now = int(time.time())
    with db.connect() as c:
        for t in ("proposals", "brain_reviews", "learned_examples"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (CHAT, "ORA & Byit App"))
        msgs = ["زد ايست شقة غرفتين 130/7,500,000", "ZED East 3 bed 160/9,900,000", "عميل عنده شقة ريسيل في زد ايست",
                "مطلوب شقة في التجمع للعميل", "Jiran studio 60/2,900,000", "ريسيل فيلا في جيران للبيع",
                "ريسيل شقة للبيع بسعر لقطة", "ريسيل دوبلكس للبيع عاجل"]
        for i, t in enumerate(msgs):
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                      (f"mm{i}", CHAT, now - 1000 + i, t))
        c.execute("INSERT OR REPLACE INTO group_scope VALUES(?,1,NULL)", (CHAT,))
    yield db, master
    with db.connect() as c:
        for t in ("proposals", "brain_reviews", "learned_examples", "chat_profiles"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 'mm%'")
        c.execute("DELETE FROM groups WHERE jid=?", (CHAT,))
        c.execute("DELETE FROM group_scope WHERE jid=?", (CHAT,))


def _add(c, master, ref, project, field, value, developer="ORA Developers"):
    master.add_proposal(c, {"project": project, "developer": developer, "field": field, "value": value,
                            "quote": value, "source_kind": "message", "source_ref": ref,
                            "source_ts": int(time.time()), "chat_jid": CHAT})
    return c.execute("SELECT * FROM proposals WHERE source_ref=? ORDER BY id DESC", (ref,)).fetchone()


def test_record_person_only_and_survive_reupload(env):
    db, master = env
    import memory
    with db.connect() as c:
        a = _add(c, master, "mm1", "ZED East", "apartment_3", "160/9,900,000")
        b = _add(c, master, "mm2", "زد ايست ريسيل", "apartment_2", "110/3,000,000")
        d = _add(c, master, "mm4", "Jiran", "studio", "60/2,900,000", developer="A Plus")
    master.decide_detail([a["id"]], "approve")
    master.decide_detail([b["id"]], "reject", reason="not_an_offer")
    master.decide_detail([d["id"]], "approve", actor="brain")        # brain decisions are not examples
    with db.connect() as c:
        ex = c.execute("SELECT * FROM learned_examples ORDER BY id").fetchall()
        assert [(e["decision"], e["reason"]) for e in ex] == [("approved", None), ("rejected", "not_an_offer")]
        assert ex[0]["row_key"] == "oradevelopers|zedeast|New Cairo" and "160/9,900,000" in ex[0]["input_text"]
        assert ex[0]["chat_jid"] == CHAT and ex[0]["chat_developer_key"] == "oradevelopers"
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "again.xlsx")              # re-upload: examples are not keyed by row id
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM learned_examples").fetchone()[0] == 2
        got = memory.retrieve(c, "message", "ريسيل شقة زد ايست", "ORA Developers")
        assert {e["decision"] for e in got} == {"approved", "rejected"}  # balanced


def test_balanced_retrieval_and_reason_update(env):
    db, master = env
    import memory
    ids = []
    with db.connect() as c:
        for ref, field in (("mm5", "apartment_1"), ("mm6", "apartment_2"), ("mm7", "apartment_3")):
            ids.append(_add(c, master, ref, "Jiran", field, "100/3,000,000", developer="A Plus")["id"])
    master.decide_detail(ids, "reject")
    from fastapi.testclient import TestClient
    import app as app_mod
    cl = TestClient(app_mod.app, base_url="https://testserver")
    cl.post("/api/login", json={"password": "pw"})
    assert cl.post("/api/proposals/reason", json={"ids": ids, "reason": "not_an_offer"}).json()["updated"] == 3
    with db.connect() as c:
        assert {r[0] for r in c.execute("SELECT reason FROM learned_examples WHERE proposal_id IN (?,?,?)", ids)} == \
            {"not_an_offer"}
        got = memory.retrieve(c, "message", "ريسيل شقة للبيع", "ORA Developers", k=4)
        decisions = [e["decision"] for e in got]
        assert decisions[0] in ("approved", "rejected") and "approved" in decisions and decisions.count("rejected") >= 1
        prompt = memory.as_prompt(got)
        assert prompt["note"].startswith("How the broker team") and all("value" in e for e in prompt["examples"])


def test_fewshot_user_turn_only(env, monkeypatch):
    db, master = env
    import llm, proposals
    sys_before = proposals.SYSTEM
    with db.connect() as c:
        off = proposals.text_units(c, [CHAT], skip=set())
        monkeypatch.setattr(proposals, "FEWSHOT", True)
        on = proposals.text_units(c, [CHAT], skip=set())
    assert proposals.SYSTEM == sys_before
    assert "past_decisions" not in json.loads(off[0]["content"])
    body = json.loads(on[0]["content"])
    assert body["past_decisions"]["examples"] and body["messages"]
    p_off = llm.params(proposals.SYSTEM, off[0]["content"], proposals.SCHEMA)
    p_on = llm.params(proposals.SYSTEM, on[0]["content"], proposals.SCHEMA)
    assert p_off["system"] == p_on["system"]                          # cacheable prefix unchanged
    assert "past_decisions" in p_on["messages"][0]["content"]         # batch requests carry the examples


def test_reopen_records(env):
    db, master = env
    import brain
    with db.connect() as c:
        p = _add(c, master, "mm3", "مطلوب التجمع", "apartment_2", "100/4,000,000", developer="")
    master.decide_detail([p["id"]], "reject", actor="brain")
    assert brain.reopen(p["id"])
    with db.connect() as c:
        assert c.execute("SELECT decision FROM learned_examples WHERE proposal_id=?", (p["id"],)).fetchone()[0] == "reopened"


def test_rule_suggestions_never_applied(env, tmp_path, monkeypatch):
    db, master = env
    import rule_suggestions, rules
    monkeypatch.setattr(rule_suggestions, "REVIEW_DIR", str(tmp_path))
    before = json.dumps(rules.RULES, sort_keys=True)
    out = rule_suggestions.run()
    names = [x["name"] for x in out["not_project_names"]]
    assert not out["applied"] and json.dumps(rules.RULES, sort_keys=True) == before
    assert names == []  # Jiran is on the master; nothing else was rejected MIN_REPEAT times
    with db.connect() as c:
        for i in range(3):
            c.execute("INSERT INTO proposals(sheet,project,project_key,field,new_raw,source_kind,source_ref,state,"
                      "decided_at,reject_reason) VALUES('New Cairo','Tagamoa','tagamoa','apartment_2',?, 'message',"
                      "'mm3','rejected',?, 'not_an_offer')", (f"{i}", int(time.time())))
    out = rule_suggestions.run()
    assert [x["name"] for x in out["not_project_names"]] == ["Tagamoa"]
    assert rule_suggestions.latest()["file"].startswith("rule-suggestions-")
    assert os.listdir(tmp_path)
