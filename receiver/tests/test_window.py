"""Only the recent past is pulled, downloaded and read (INGEST_DAYS); big attachments are skipped; downloads pause
when the disk is low; Free space removes old attachments but keeps what was read from them; file requests are built
one batch at a time."""
import base64, io, os, time
import pytest

CHAT = "991@g.us"
DAY = 86400


def _rec(wid, ts, text="", doc=None, size=None):
    msg = {"conversation": text} if not doc else {"documentMessage": {"fileName": doc, "mimetype": "application/pdf",
                                                                    "caption": text, "fileLength": size or 1000}}
    return {"key": {"remoteJid": CHAT, "id": wid}, "pushName": "A", "messageTimestamp": ts,
            "messageType": "documentMessage" if doc else "conversation", "message": msg}


def _pdf():
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (CHAT, "Window test"))
        c.execute("INSERT OR REPLACE INTO group_scope(jid,in_scope) VALUES(?,1)", (CHAT,))
    yield db
    with db.connect() as c:
        fids = [r[0] for r in c.execute("SELECT o.file_id FROM file_occurrences o WHERE o.wa_id LIKE 'w%'")]
        for f in fids:
            c.execute("DELETE FROM source_rows WHERE file_id=?", (f,))
            c.execute("DELETE FROM file_occurrences WHERE file_id=?", (f,))
            c.execute("DELETE FROM source_files WHERE id=?", (f,))
        c.execute("DELETE FROM media_fetch WHERE wa_id LIKE 'w%'")
        c.execute("DELETE FROM source_messages WHERE group_jid=?", (CHAT,))
        c.execute("DELETE FROM groups WHERE jid=?", (CHAT,))
        c.execute("DELETE FROM group_scope WHERE jid=?", (CHAT,))


def test_pull_only_the_window_and_stop_paging(env, monkeypatch):
    db = env
    import evolution, history, window
    monkeypatch.setattr(window, "DAYS", 7)
    now = int(time.time())
    pages = {1: [_rec("wn1", now - 100, "new offer"), _rec("wn2", now - 2 * DAY, "new too")],
             2: [_rec("wo1", now - 9 * DAY, "old"), _rec("wo2", now - 10 * DAY, "older")],
             3: [_rec("wo3", now - 20 * DAY, "oldest")]}
    calls = []

    def find(jid, page, page_size=100):
        calls.append(page)
        return pages[page], 3

    monkeypatch.setattr(evolution, "find_messages", find)
    assert history.pull_history(lambda *_: None, jids=[CHAT]) == 2
    assert calls == [1, 2]                                      # page 2 was all old: page 3 never asked for
    with db.connect() as c:
        ids = {r[0] for r in c.execute("SELECT wa_id FROM source_messages WHERE group_jid=?", (CHAT,))}
    assert ids == {"wn1", "wn2"}


def test_downloads_window_size_cap_and_disk_guard(env, monkeypatch):
    db = env
    import evolution, history, window
    monkeypatch.setattr(window, "DAYS", 7)
    now = int(time.time())
    with db.connect() as c:
        for rec in (_rec("wd1", now - 50, "price list", doc="list.pdf"),
                    _rec("wd2", now - 40, "brochure", doc="big.pdf", size=80 * 2**20),
                    _rec("wd3", now - 30 * DAY, "old list", doc="old.pdf")):
            history.store_message(c, history.parse_record(rec, "history"))
    fetched = []
    monkeypatch.setattr(evolution, "media_base64", lambda wa, jid=None: fetched.append(wa) or
                        {"base64": base64.b64encode(_pdf()).decode(), "fileName": "x.pdf", "mimetype": "application/pdf"})
    monkeypatch.setattr(window, "low_disk", lambda: True)
    assert history.download_media(lambda *_: None, jids=[CHAT]) == (0, 0) and fetched == []  # disk low: paused
    monkeypatch.setattr(window, "low_disk", lambda: False)
    ok, failed = history.download_media(lambda *_: None, jids=[CHAT])
    assert (ok, failed) == (1, 0) and fetched == ["wd1"]       # old one outside the window, big one skipped
    with db.connect() as c:
        assert c.execute("SELECT status FROM media_fetch WHERE wa_id='wd2'").fetchone()[0] == "too_large"
    assert history.download_media(lambda *_: None, jids=[CHAT]) == (0, 0)  # not retried every sync


def test_free_space_keeps_what_was_read(env, monkeypatch):
    db = env
    import history, window
    monkeypatch.setattr(window, "DAYS", 7)
    old = int(time.time()) - 30 * DAY
    with db.connect() as c:
        history.store_message(c, history.parse_record(_rec("wf1", old, "old file", doc="f.pdf"), "history"))
        fid = history.save_file(c, "wf1", _pdf() + b"%old", "f.pdf", "application/pdf")
        c.execute("INSERT OR IGNORE INTO source_rows(file_id,sheet,row_number,kind,raw_json) VALUES(?,'',1,'pdf_page',"
                  "'{\"text\": \"page text\"}')", (fid,))
        path = c.execute("SELECT path FROM source_files WHERE id=?", (fid,)).fetchone()[0]
        recent = c.execute("SELECT path FROM source_files f JOIN file_occurrences o ON o.file_id=f.id "
                           "WHERE o.wa_id='wd1'").fetchone()[0]
    out = history.free_space(lambda *_: None, jids=[CHAT])  # scoped: the shared fixtures' files stay
    assert out["files"] >= 1 and not os.path.exists(path) and os.path.exists(recent)
    with db.connect() as c:
        f = c.execute("SELECT path, pruned_at FROM source_files WHERE id=?", (fid,)).fetchone()
        assert f["path"] is None and f["pruned_at"]
        assert c.execute("SELECT COUNT(*) FROM source_rows WHERE file_id=?", (fid,)).fetchone()[0] == 1  # text kept
        m = c.execute("SELECT text, raw_json FROM source_messages WHERE wa_id='wf1'").fetchone()
        assert m["text"] == "old file" and m["raw_json"] is None
    from fastapi.testclient import TestClient
    import app
    cl = TestClient(app.app, base_url="https://testserver")
    cl.post("/api/login", json={"password": "pw"})
    assert cl.get(f"/sources/file/{fid}").status_code == 404     # a pruned file answers "not found", no crash
    p = cl.get("/api/pipeline").json()
    assert p["disk"]["free_mb"] >= 0 and p["window"]["days"] == 7


def test_claude_reads_only_the_window(env, monkeypatch):
    db = env
    import proposals, window
    monkeypatch.setattr(window, "DAYS", 7)
    with db.connect() as c:
        ids = {m["source_id"] for u in proposals.text_units(c, [CHAT], skip=set())
               for m in __import__("json").loads(u["content"])["messages"]}
    assert "wn1" in ids and "wf1" not in ids


def test_pdf_requests_built_one_batch_at_a_time(env, monkeypatch):
    db = env
    import history, parse_files, proposals, window, jobs
    monkeypatch.setattr(window, "DAYS", 0)
    now = int(time.time())
    with db.connect() as c:
        for i in range(3):
            wa = f"wb{i}"
            history.store_message(c, history.parse_record(_rec(wa, now - i, "pdf", doc=f"b{i}.pdf"), "history"))
            history.save_file(c, wa, _pdf() + f"%{i}".encode(), f"b{i}.pdf", "application/pdf")
    jobs.run_now("parse", parse_files.parse_all)
    with db.connect() as c:
        all_units = proposals.pdf_units(c, [CHAT], skip=set())
        one = proposals.pdf_units(c, [CHAT], skip=set(), budget=1)
    assert len(all_units) >= 3 and len(one) == 1
