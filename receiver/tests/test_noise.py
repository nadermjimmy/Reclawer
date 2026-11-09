"""Phase 4: the text-message noise filter (training labels, skip path + log, re-queue, flag off = unchanged)."""
import json, time
import pytest

CHAT = "441@g.us"
OFFERS = ["ZED East 3 bed 160/9,900,000 down payment 10% over 8 years", "Jiran studio 60/2,900,000 launch price",
          "Celestia apartment 2 bed 120/6,100,000 installments 10 years", "Dao towers 1 bed 70/3,200,000 cash discount",
          "شقة غرفتين في زد ايست 130/7,500,000 مقدم 10% على 8 سنين", "سعر الفيلا في سيليستيا 250/18,000,000",
          "Town house 210/14,500,000 new phase launch", "تاون هاوس 200/13,900,000 تقسيط 9 سنين"]
NOISE = ["صباح الخير يا جماعة", "مطلوب شقة للعميل في التجمع ميزانية 5 مليون", "ريسيل شقة للبيع بسعر لقطة",
         "Good morning team", "anyone has a client for resale?", "عميل عايز فيلا ريسيل", "تمام شكرا",
         "Please call me back", "ريسيل دوبلكس للبيع عاجل", "who is the sales manager here?"]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import fixtures
    fixtures.run_pipeline()
    import db, models
    from test_master import master_bytes
    import master
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    old = models.MODELS_DIR
    models.MODELS_DIR = str(tmp_path_factory.mktemp("models"))
    now = int(time.time())
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (CHAT, "Noise test"))
        c.execute("INSERT OR REPLACE INTO group_scope VALUES(?,1,NULL)", (CHAT,))
        for i, t in enumerate(OFFERS + NOISE):
            wa = f"nz{i}"
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                      (wa, CHAT, now - 5000 + i, t))
            offer = i < len(OFFERS)
            c.execute("INSERT INTO proposals(sheet,project,project_key,field,new_raw,source_kind,source_ref,state,"
                      "decided_at,reject_reason) VALUES('New Cairo','X','x',?,?,'message',?,?,?,?)",
                      ("apartment_2", f"v{i}", wa, "approved" if offer else "rejected", now - 4000 + i,
                       None if offer else "not_an_offer"))
        # a wrong-value rejection is NOT a noise label
        c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES('nzwv',?,?,?)",
                  (CHAT, now, "ZED East 2 bed 125/6,900,000"))
        c.execute("INSERT INTO proposals(sheet,project,project_key,field,new_raw,source_kind,source_ref,state,"
                  "decided_at,reject_reason) VALUES('New Cairo','X','x','apartment_2','w','message','nzwv','rejected',?,"
                  "'wrong_value')", (now,))
    yield db
    models.MODELS_DIR = old
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("DELETE FROM noise_skips")
        c.execute("DELETE FROM processed_batches WHERE batch_key LIKE 'u:nz%'")
        c.execute("DELETE FROM source_messages WHERE wa_id LIKE 'nz%'")
        c.execute("DELETE FROM group_scope WHERE jid=?", (CHAT,))
        c.execute("DELETE FROM groups WHERE jid=?", (CHAT,))


def test_labels_and_training(env):
    db = env
    import models, noise
    with db.connect() as c:
        lab = noise.labels(c)
    assert sum(y for *_, y, _ in lab) == len(OFFERS) and len(lab) == len(OFFERS) + len(NOISE)  # wrong_value excluded
    ver, meta = noise.train(lab)
    assert meta["offers"] == len(OFFERS) and meta["noise"] == len(NOISE) and models.meta("noise", ver)
    assert noise.model()[0] is None            # saved, not promoted
    models.promote("noise", ver)
    p = noise.p_offer(["ZED East 2 bed 130/7,000,000 10% 8 years", "صباح الخير"])
    assert 0 <= p[1] < p[0] <= 1
    assert min(noise.p_offer(OFFERS)) > max(noise.p_offer(NOISE))  # labels line up with their texts


def test_train_refuses_few_labels():
    import noise
    with pytest.raises(ValueError):
        noise.train([("a", "x", 1, 0)])


def test_flag_off_unchanged_then_skip_log_requeue(env, monkeypatch):
    db = env
    import noise, proposals
    with db.connect() as c:
        c.execute("DELETE FROM processed_batches WHERE batch_key LIKE 'u:nz%'")
        all_ids = {m["source_id"] for u in proposals.text_units(c, [CHAT], skip=set())
                   for m in json.loads(u["content"])["messages"]}
    assert len(all_ids) == len(OFFERS) + len(NOISE) + 1           # NOISE_SKIP unset: nothing skipped
    monkeypatch.setattr(noise, "SKIP", 0.5)
    with db.connect() as c:
        kept = {m["source_id"] for u in proposals.text_units(c, [CHAT], skip=set())
                for m in json.loads(u["content"])["messages"]}
        skips = c.execute("SELECT * FROM noise_skips").fetchall()
        assert skips and {s["wa_id"] for s in skips} == all_ids - kept
        assert all(s["model_version"] and s["score"] < 0.5 for s in skips)
        assert all(c.execute("SELECT 1 FROM processed_batches WHERE batch_key=?", ("u:" + s["wa_id"],)).fetchone()
                   for s in skips)
        assert {f"nz{i}" for i in range(len(OFFERS))} - kept == set(), [(s["wa_id"], s["score"]) for s in skips]
    n = noise.requeue()
    with db.connect() as c:
        assert n == len(skips) and not c.execute("SELECT 1 FROM processed_batches WHERE batch_key LIKE 'u:nz%'").fetchone()
        assert all(r[0] for r in c.execute("SELECT requeued_at FROM noise_skips"))


def test_eval_reports_false_skip(env):
    db = env
    from eval import run_eval
    r = run_eval.run()
    nf = r["noise_filter"]
    assert nf and nf["offers"] >= len(OFFERS) and nf["false_skip_rate"] is not None
    assert "Noise filter" in run_eval.report_md(r)
