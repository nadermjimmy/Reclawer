"""Reading order: text first, then spreadsheets, PDFs, images; later sources only fill gaps.
PDFs and images go to Claude natively on the file model; the full run goes through the batch queue stage by stage."""
import base64, io, json, os, time
import pytest

CHAT = "777@g.us"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _pdf(pages):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import fixtures
    fixtures.run_pipeline()
    import db, master, history, parse_files, jobs
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    now = int(time.time())
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (CHAT, "Developer prices"))
        c.execute("INSERT OR REPLACE INTO group_scope(jid,in_scope) VALUES(?,1)", (CHAT,))
        for wa, ts, text in [("ro1", now - 3600, "ZED East 2 bed 130/7,500,000"),
                             ("ro2", now - 3500, "Celestia brochure attached"),
                             ("ro3", now - 3400, "")]:
            c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text,has_media) VALUES(?,?,?,?,?)",
                      (wa, CHAT, ts, text, int(not text)))
        # a PDF (3 pages) posted with ro2 and an image posted as ro3
        for wa, name, mime, data in [("ro2", "Celestia.pdf", "application/pdf", _pdf(3)),
                                     ("ro3", "prices.png", "image/png", PNG)]:
            history.save_file(c, wa, data, name, mime)
    jobs.run_now("parse", parse_files.parse_all)
    yield db, master
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        c.execute("DELETE FROM ingest_runs")
        c.execute("DELETE FROM ingest_items")
        fids = [r[0] for r in c.execute("SELECT file_id FROM file_occurrences WHERE wa_id IN ('ro2','ro3')")]
        for fid in fids:
            c.execute("DELETE FROM source_rows WHERE file_id=?", (fid,))
            c.execute("DELETE FROM file_occurrences WHERE file_id=?", (fid,))
            c.execute("DELETE FROM source_files WHERE id=?", (fid,))
        c.execute("DELETE FROM source_messages WHERE wa_id IN ('ro1','ro2','ro3')")
        c.execute("DELETE FROM groups WHERE jid=?", (CHAT,))
        c.execute("DELETE FROM group_scope WHERE jid=?", (CHAT,))
        c.execute("DELETE FROM processed_batches WHERE batch_key LIKE 'u:ro%' OR batch_key LIKE 'up:%' "
                  "OR batch_key LIKE 'ui:%'")


def _fake(seen):
    """Text says ZED East apartment 2; the PDF repeats ZED East apt 2 (must be dropped: text filled it) and adds
    Celestia apartment 3 (a gap: kept); the image repeats Celestia apt 3 (dropped: the PDF filled it)."""
    def fake(system, content, schema, max_tokens=0, model=None, **kw):
        if isinstance(content, str):
            body = json.loads(content)
            seen.append(("text", model))
            return {"updates": [{"source_id": m["source_id"], "developer": "", "project": "ZED East",
                                 "location_code": "", "field": "apartment_2", "value": "130/7,500,000", "quote": m["text"]}
                                for m in body.get("messages", []) if "ZED East" in m["text"]]}
        kinds = [b["type"] for b in content]
        seen.append((kinds[0], model))
        if kinds[0] == "document":
            assert content[0]["source"]["media_type"] == "application/pdf"
            assert base64.b64decode(content[0]["source"]["data"]).startswith(b"%PDF")
            return {"updates": [
                {"source_id": "p1", "developer": "", "project": "ZED East", "location_code": "",
                 "field": "apartment_2", "value": "131/7,900,000", "quote": "p1"},
                {"source_id": "p2", "developer": "", "project": "Celestia", "location_code": "",
                 "field": "apartment_3", "value": "150/9,000,000", "quote": "p2"}]}
        assert content[0]["source"]["media_type"] == "image/png"
        return {"updates": [{"source_id": "img", "developer": "", "project": "Celestia", "location_code": "",
                             "field": "apartment_3", "value": "151/9,100,000", "quote": "img"}]}
    return fake


def _props(db):
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT project, field, new_raw, source_kind, state FROM proposals "
                                           "WHERE project IN ('ZED East','Celestia') ORDER BY id")]


def test_live_reading_order_and_gaps(env, monkeypatch):
    db, master = env
    import llm, proposals
    seen = []
    monkeypatch.setattr(llm, "call_json", _fake(seen))
    written, failed = proposals.run_live(lambda *_: None, jids=[CHAT])
    assert failed == 0
    stages = [s for s, _ in seen]
    assert stages == ["text", "document", "image"]                         # text, then PDF, then image
    assert seen[0][1] == llm.MODEL and seen[1][1] == seen[2][1] == llm.FILE_MODEL
    props = {(p["project"], p["field"], p["source_kind"]): p["new_raw"] for p in _props(db)}
    assert props[("ZED East", "apartment_2", "message")] == "130/7,500,000"
    assert ("ZED East", "apartment_2", "pdf_page") not in props          # text filled it within 14 days
    assert props[("Celestia", "apartment_3", "pdf_page")] == "150/9,000,000"  # a gap the PDF filled
    assert ("Celestia", "apartment_3", "image") not in props             # the PDF filled it first


def test_old_text_does_not_block_a_newer_file(env):
    db, master = env
    with db.connect() as c:
        c.execute("UPDATE proposals SET source_ts=? WHERE source_kind='message'", (int(time.time()) - 20 * 86400,))
        n = master.add_proposal(c, {"project": "ZED East", "field": "apartment_2", "value": "132/8,000,000",
                                    "source_kind": "image", "source_ref": "1", "source_ts": int(time.time())})
    assert n == 1  # text older than 14 days: the newer image may refresh the cell


def test_batched_run_goes_stage_by_stage(env, monkeypatch):
    db, master = env
    import llm, proposals, jobs
    with db.connect() as c:  # start from a clean slate for this chat
        c.execute("DELETE FROM proposals")
        c.execute("DELETE FROM processed_batches WHERE batch_key LIKE 'u:ro%' OR batch_key LIKE 'up:%' "
                  "OR batch_key LIKE 'ui:%' OR batch_key LIKE 'u:%'")
    seen, batches = [], {}
    fake = _fake(seen)

    def submit(reqs):
        bid = f"b{len(batches) + 1}"
        batches[bid] = [(cid, p) for cid, p in reqs]
        return bid

    def results(bid):
        for cid, p in batches[bid]:
            try:
                yield cid, fake(p["system"], p["messages"][0]["content"], None, model=p["model"]), None
            except Exception as e:  # requests for other chats in the shared test DB
                yield cid, {"updates": []}, None

    monkeypatch.setattr(llm, "batch_submit", submit)
    monkeypatch.setattr(llm, "batch_status", lambda bid: ("ended", None))
    monkeypatch.setattr(llm, "batch_results", results)
    monkeypatch.setenv("INGEST_MODE", "batch")
    job_id = jobs.run_now("propose", proposals.propose)
    with db.connect() as c:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "waiting"
        assert c.execute("SELECT stage, status FROM ingest_runs ORDER BY id DESC").fetchone()[1] == "waiting"
    for _ in range(6):
        proposals.poll()
    with db.connect() as c:
        run = c.execute("SELECT * FROM ingest_runs ORDER BY id DESC").fetchone()
        job = c.execute("SELECT status, log FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert run["status"] == "done" and job["status"] == "done", job["log"]
    kinds = [s for s, _ in seen]
    assert kinds.index("text") < kinds.index("document") < kinds.index("image")
    # every batch holds one stage only
    stage_of = {"t": "text", "s": "sheets", "p": "pdfs", "i": "images"}
    for reqs in batches.values():
        assert len({stage_of[cid.split("_", 1)[1][0]] for cid, _ in reqs}) == 1
    props = {(p["project"], p["field"], p["source_kind"]) for p in _props(db)}
    assert ("ZED East", "apartment_2", "pdf_page") not in props and ("Celestia", "apartment_3", "pdf_page") in props
    assert "RESULT" in job["log"]


def test_groups_are_read_by_default():
    import db, history
    with db.connect() as c:
        c.execute("DELETE FROM group_scope WHERE jid IN ('901@g.us','201999@s.whatsapp.net')")

    class Evo:
        def find_chats(self):
            return [{"remoteJid": "901@g.us", "name": "New dev group"},
                    {"remoteJid": "201999@s.whatsapp.net", "pushName": "Someone"}]

        def fetch_groups(self):
            return []
    import evolution
    old = evolution.find_chats, evolution.fetch_groups
    evolution.find_chats, evolution.fetch_groups = Evo().find_chats, Evo().fetch_groups
    try:
        history.sync_groups(lambda *_: None)
    finally:
        evolution.find_chats, evolution.fetch_groups = old
    with db.connect() as c:
        scope = dict(c.execute("SELECT jid, in_scope FROM group_scope WHERE jid IN ('901@g.us','201999@s.whatsapp.net')"))
        c.execute("DELETE FROM group_scope WHERE jid IN ('901@g.us','201999@s.whatsapp.net')")
        c.execute("DELETE FROM groups WHERE jid IN ('901@g.us','201999@s.whatsapp.net')")
    assert scope == {"901@g.us": 1, "201999@s.whatsapp.net": 0}


def _auth_error():
    import anthropic
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
    return anthropic.AuthenticationError("invalid x-api-key", body=None, response=httpx.Response(
        401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")))


def test_rejected_key_does_not_stop_the_sync(env, monkeypatch):
    db, master = env
    import llm, proposals, jobs

    def reject(*a, **k):
        raise _auth_error()
    lines = []
    log = type("L", (), {"job_id": None, "__call__": lambda self, x: lines.append(str(x))})()
    with db.connect() as c:
        c.execute("DELETE FROM processed_batches WHERE batch_key LIKE 'u:ro%'")
        c.execute("DELETE FROM ingest_runs")
    monkeypatch.setattr(llm, "batch_submit", reject)
    proposals.start_batched(log)  # must return, not raise
    assert any("ANTHROPIC_API_KEY" in l for l in lines)
    with db.connect() as c:
        assert c.execute("SELECT status FROM ingest_runs ORDER BY id DESC").fetchone()[0] == "failed"
    calls = []

    def reject_live(*a, **k):
        calls.append(1)
        raise _auth_error()
    monkeypatch.setattr(llm, "call_json", reject_live)
    lines.clear()
    written, failed = proposals.run_stage_live(log, "text", jids=[CHAT])
    assert written == 0 and failed == 1 and sum("ANTHROPIC_API_KEY" in l for l in lines) == 1
