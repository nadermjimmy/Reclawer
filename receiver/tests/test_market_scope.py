"""UAE developers' groups are unticked; developers in both markets are decided from the chat's messages; a person's
choice and the team's assignment are never overridden; undo re-ticks."""
import time
import pytest

G = {"u1@g.us": "Damac & Byit App", "u2@g.us": "Emaar Misr & Byit App", "u3@g.us": "Emaar & Byit App",
     "u4@g.us": "Bloomfields Tatweer Misr", "u5@g.us": "Sobha & Byit App", "u6@g.us": "Nakheel brokers",
     "u7@g.us": "Modon & Byit App", "u8@g.us": "Binghatti & Byit App"}


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db
    now = int(time.time())
    msgs = {"u2@g.us": ["Mivida New Cairo 3 bed 12,000,000 EGP", "التجمع الخامس مقدم 10%"],
            "u3@g.us": ["Dubai Creek Harbour 2BR AED 2.1M", "Downtown Dubai launch, 10% down"],
            "u7@g.us": ["Ras El Hekma Sahel launch", "price in EGP 15M"],
            "u5@g.us": ["Sobha One AED 1.5M"]}
    with db.connect() as c:
        for jid, name in G.items():
            c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (jid, name))
            c.execute("INSERT OR REPLACE INTO group_scope(jid,in_scope,note) VALUES(?,1,NULL)", (jid,))
        for jid, texts in msgs.items():
            for i, t in enumerate(texts):
                c.execute("INSERT OR REPLACE INTO source_messages(wa_id,group_jid,ts,text) VALUES(?,?,?,?)",
                          (f"ms-{jid}-{i}", jid, now - 100, t))
        c.execute("UPDATE group_scope SET note='person' WHERE jid='u8@g.us'")       # a person ticked it
        c.execute("INSERT OR REPLACE INTO chat_profiles(jid,developer,source) VALUES('u6@g.us','Nakheel','team')")
    yield db
    with db.connect() as c:
        for jid in G:
            c.execute("DELETE FROM groups WHERE jid=?", (jid,))
            c.execute("DELETE FROM group_scope WHERE jid=?", (jid,))
            c.execute("DELETE FROM source_messages WHERE group_jid=?", (jid,))
            c.execute("DELETE FROM chat_profiles WHERE jid=?", (jid,))


def _scope(db):
    with db.connect() as c:
        return {r["jid"]: (r["in_scope"], r["note"]) for r in
                c.execute(f"SELECT * FROM group_scope WHERE jid IN ({','.join('?' * len(G))})", list(G))}


def test_preview_changes_nothing(env):
    db = env
    import market_scope
    before = _scope(db)
    out = market_scope.apply(dry_run=True)
    assert _scope(db) == before
    names = {i["name"] for i in out["items"] if i["reason"] and "UAE developer" in i["reason"]}
    assert "Damac & Byit App" in names


def test_untick_rules(env):
    db = env
    import market_scope
    market_scope.apply()
    s = _scope(db)
    assert s["u1@g.us"] == (0, "auto:uae:Damac")                  # UAE-only developer
    assert s["u5@g.us"][0] == 0                                   # Sobha, UAE messages
    assert s["u3@g.us"][0] == 0                                   # Emaar, but this chat talks Dubai
    assert s["u2@g.us"][0] == 1                                   # Emaar Misr: Egyptian chat, kept
    assert s["u7@g.us"][0] == 1                                   # Modon, Sahel / EGP: kept
    assert s["u4@g.us"][0] == 1                                   # 'Bloomfields' isn't the word 'Bloom'
    assert s["u6@g.us"][0] == 1                                   # assigned by the team: never touched
    assert s["u8@g.us"] == (1, "person")                          # a person's choice wins


def test_new_groups_only_and_undo(env):
    db = env
    import market_scope
    with db.connect() as c:
        c.execute("UPDATE group_scope SET in_scope=1, note='person' WHERE jid='u1@g.us'")  # person re-ticks Damac
    market_scope.apply(only_new=True)
    assert _scope(db)["u1@g.us"] == (1, "person")
    market_scope.undo()
    s = _scope(db)
    assert s["u3@g.us"][0] == 1 and s["u5@g.us"][0] == 1


def test_person_toggle_marks_note(env):
    db = env
    from fastapi.testclient import TestClient
    import app
    cl = TestClient(app.app, base_url="https://testserver")
    cl.post("/api/login", json={"password": "pw"})
    assert cl.patch("/api/chats/u4@g.us", json={"in_scope": False}).status_code == 200
    assert _scope(db)["u4@g.us"] == (0, "person")
