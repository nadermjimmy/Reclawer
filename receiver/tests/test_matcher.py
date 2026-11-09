"""Phase 2: the unified matcher (candidates, features, scoring, decide bands), TF-IDF cache, MATCHER modes,
developer-scoped aliases and training."""
import json, os, time
import pytest


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
    yield db, master
    with db.connect() as c:
        for t in ("proposals", "dev_aliases", "master_aliases", "matcher_shadow", "brain_reviews"):
            c.execute(f"DELETE FROM {t}")


GRIDS = ["New Cairo", "Sokhna&new zayed", "New Capital"]


def test_textsim_cache(tmp_path, monkeypatch):
    import models, textsim
    monkeypatch.setattr(models, "MODELS_DIR", str(tmp_path))
    textsim._mem.clear()
    a = textsim.Index(["zed east", "celestia", "jiran"], "t")
    assert [n for n in os.listdir(tmp_path) if n.startswith("tfidf-t-")]
    top = a.top("zed est", 2)
    assert top[0][0] == 0 and top[0][1] > 0.3
    textsim._mem.clear()
    b = textsim.Index(["zed east", "celestia", "jiran"], "t")       # same corpus: loaded, not refitted
    assert b.key == a.key and abs(b.sims("jiran")[2] - 1.0) < 1e-6
    c = textsim.Index(["zed east", "celestia", "jiran", "dao"], "t")  # corpus changed: new fit
    assert c.key != a.key and len(c.sims("dao")) == 4


def test_decide_exact_cross_script_and_developer(env):
    db, master = env
    import matcher
    with db.connect() as c:
        hit = matcher.decide(c, "ZED East", "ORA Developers", GRIDS)
        assert hit["New Cairo"][0]["project"] == "ZED East" and hit["New Cairo"][1] == "exact"
        ar = matcher.decide(c, "سيليستيا", "", GRIDS)                # Arabic spelling of a Latin row
        assert list(ar) == ["Sokhna&new zayed"] and ar["Sokhna&new zayed"][0]["project"] == "Celestia"
        assert ar["Sokhna&new zayed"][1] == "near"
        assert matcher.decide(c, "Totally Unknown Place", "", GRIDS) == {}
        # same name, another developer, when the chat's developer has rows of its own -> only near
        other = matcher.decide(c, "Jiran", "ORA Developers", GRIDS)
        assert all(t[1] == "near" for t in other.values())


def test_match_modes_and_shadow(env, monkeypatch):
    db, master = env
    with db.connect() as c:
        leg = master.match(c, "ZED East", "", GRIDS, mode="legacy")
        v2 = master.match(c, "ZED East", "", GRIDS, mode="v2")
        assert {s: (r["id"], st) for s, (r, st, _) in leg.items()} == {s: (r["id"], st) for s, (r, st, _) in v2.items()}
        shadow = master.match(c, "سيليستيا", "", GRIDS, mode="shadow")
        assert shadow == {}  # legacy decides: no match for the Arabic spelling ...
        rows = c.execute("SELECT * FROM matcher_shadow WHERE project=?", ("سيليستيا",)).fetchall()
        assert [r["sheet"] for r in rows] == ["Sokhna&new zayed"]  # ... the disagreement is logged, nothing else
        assert rows[0]["legacy_key"] is None and "celestia" in rows[0]["v2_key"]


def test_developer_scoped_alias(env):
    db, master = env
    import matcher
    now = int(time.time())
    with db.connect() as c:
        c.execute("INSERT INTO dev_aliases VALUES('elzed','oradevelopers','zedeast','El Zed',?)", (now,))
        hit = matcher.decide(c, "El Zed", "ORA Developers", GRIDS)
        assert hit["New Cairo"][0]["project"] == "ZED East" and hit["New Cairo"][1] == "alias"
        # the same alias in another developer's chat doesn't point at ORA's project
        other = matcher.decide(c, "El Zed", "A Plus", GRIDS)
        assert all(t[1] != "alias" for t in other.values())


def test_auto_band_is_off_by_default(env, monkeypatch):
    db, master = env
    import matcher
    with db.connect() as c:
        ar = matcher.decide(c, "سيليستيا", "", GRIDS)
        assert ar["Sokhna&new zayed"][1] == "near"
        monkeypatch.setattr(matcher, "MATCH_AUTO", 0.5)
        assert matcher.decide(c, "سيليستيا", "", GRIDS)["Sokhna&new zayed"][1] == "alias"


def test_train_and_promote(env, tmp_path, monkeypatch):
    db, master = env
    import matcher, models, train_matcher
    monkeypatch.setattr(models, "MODELS_DIR", str(tmp_path))
    gold = []
    names = [("ZED East", "ORA Developers", "oradevelopers|zedeast|New Cairo"),
             ("زد ايست", "ORA Developers", "oradevelopers|zedeast|New Cairo"),
             ("Celestia", "", "1850properties|celestia|Sokhna&new zayed"),
             ("سيليستيا", "", "1850properties|celestia|Sokhna&new zayed"),
             ("Jiran", "A Plus", "aplus|jiran|New Cairo"),
             ("Dao", "Aayan", "aayan|daotowers|New Capital")] * 4
    for n, (proj, dev, rk) in enumerate(names):
        gold.append({"id": n, "project": proj, "developer": dev, "chat_developer": "", "chat_jid": None,
                     "field": "apartment_2", "source_kind": "message", "decided_at": 10 + n,
                     "label": {"type": "row", "row_key": rk}})
    ver, meta = train_matcher.train(gold)
    assert meta["labels"] == len(names) and meta["positives"] > 0 and meta["curve"]
    assert models.meta("matcher", ver)["features"] == matcher.FEATURES
    assert matcher.model()[1] == "heuristic"         # saved, not promoted
    models.promote("matcher", ver)
    assert matcher.model()[1] == ver
    with db.connect() as c:
        ps = [p for _, p, _ in matcher.scored(c, matcher.Query("ZED East", "ORA Developers"))]
    assert all(0.0 <= p <= 1.0 for p in ps) and ps[0] > 0.5


def test_train_refuses_without_labels():
    import train_matcher
    with pytest.raises(ValueError):
        train_matcher.train([])
