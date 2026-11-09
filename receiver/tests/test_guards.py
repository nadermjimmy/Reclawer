"""Phase 6: price guards (median/MAD maths, fallbacks) and calibrated auto-approve with every owner exclusion kept."""
import json, time
import pytest


def test_robust_z_maths():
    import guards
    z, med = guards.robust_z(100, [50, 52, 48, 51, 49])
    assert med == 50 and z > 3.5
    z, _ = guards.robust_z(51, [50, 52, 48, 51, 49])
    assert abs(z) < 1
    z, _ = guards.robust_z(55, [50, 50, 50, 50, 50])  # MAD 0 -> floor at 5% of the median
    assert abs(z - 0.6745 * 5 / 2.5) < 1e-9
    assert guards.ppsm("apartment_2", {"area_sqm": 100, "price_egp": 5_000_000}) == 50_000
    assert guards.ppsm("ready_to_move", {"rtm_area_sqm": 100, "rtm_price_egp": 6_000_000}) == 60_000
    assert guards.ppsm("apartment_2", {"status": "sold_out"}) is None


@pytest.fixture(scope="module")
def env():
    import fixtures
    fixtures.run_pipeline()
    import db, master
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    with db.connect() as c:
        c.execute("DELETE FROM proposals")
        zed = c.execute("SELECT id FROM master_rows WHERE project='ZED East'").fetchone()[0]
    yield db, master, zed
    with db.connect() as c:
        c.execute("DELETE FROM proposals")


def _prop(c, master, ref, field, value, ts=None):
    master.add_proposal(c, {"project": "ZED East", "developer": "ORA Developers", "field": field, "value": value,
                            "quote": value, "source_kind": "message", "source_ref": ref,
                            "source_ts": ts or int(time.time())})
    return c.execute("SELECT * FROM proposals WHERE source_ref=? AND field=?", (ref, field)).fetchone()


def test_fallback_below_five_points_then_history(env):
    db, master, zed = env
    import guards
    with db.connect() as c:
        pts, scope = guards.history(c, c.execute("SELECT * FROM master_rows WHERE id=?", (zed,)).fetchone(), "apartment_2")
        assert scope == "none" and len(pts) == 1                     # one cell on the master: fixed band
        assert guards.check(c, zed, "apartment_1", {"area_sqm": 125, "price_egp": 6_950_000})[0]
        ok, why = guards.check(c, zed, "apartment_2", {"area_sqm": 125, "price_egp": 9_900_000})
        assert not ok and "only 1 price point" in why               # +43% EGP/sq m against the one known point
        # five approved points -> the project's own history decides
        for i, (f, v) in enumerate([("apartment_1", "70/3,900,000"), ("apartment_3", "160/8,900,000"),
                                    ("apartment_4", "200/11,100,000"), ("studio", "50/2,800,000")]):
            p = _prop(c, master, f"g{i}", f, v)
            c.execute("UPDATE proposals SET state='approved', row_id=?, decided_at=? WHERE id=?", (zed, int(time.time()), p["id"]))
        pts, scope = guards.history(c, c.execute("SELECT * FROM master_rows WHERE id=?", (zed,)).fetchone(), "apartment_2")
        assert scope == "project" and len(pts) == 5
        assert guards.check(c, zed, "apartment_2", {"area_sqm": 130, "price_egp": 7_300_000})[0]
        ok, why = guards.check(c, zed, "apartment_2", {"area_sqm": 130, "price_egp": 20_000_000})
        assert not ok and "unusual" in why
        assert guards.check(c, zed, "office", {"area_sqm": 50, "price_egp": 9_000_000})[0]  # commercial kept apart


def test_guards_v2_auto_approve_rules(env, monkeypatch):
    db, master, zed = env
    import brain, matcher
    monkeypatch.setattr(brain, "GUARDS_V2", True)
    with db.connect() as c:
        p = _prop(c, master, "gv1", "apartment_2", "126/7,050,000")
        assert p["row_id"] == zed
        monkeypatch.setattr(matcher, "MATCH_AUTO", None)
        assert brain.safe_to_approve(c, p, "high") == (False, "MATCH_AUTO isn't set")  # no threshold: never
        monkeypatch.setattr(matcher, "MATCH_AUTO", 0.9)
        ok, why = brain.safe_to_approve(c, p, "low")                 # the LLM's confidence no longer matters
        assert ok, why
        assert not brain.safe_to_approve(c, p, "high", match_p=0.5)[0]
        # owner exclusions hold whatever the matcher says
        hold = _prop(c, master, "gv2", "apartment_3", "Hold")
        assert brain.safe_to_approve(c, hold, "high", match_p=0.99) == (False, "Hold/Hide changes need a person")
        loc = _prop(c, master, "gv3", "location", "G")
        assert not brain.safe_to_approve(c, loc, "high", match_p=0.99)[0]
        new = dict(p)
        new["row_id"] = None
        assert brain.safe_to_approve(c, new, "high", match_p=0.99) == (False, "new rows always need a person")
        wild = _prop(c, master, "gv4", "apartment_4", "200/40,000,000")
        assert not brain.safe_to_approve(c, wild, "high", match_p=0.99)[0]
    with db.connect() as c:
        master.add_proposal(c, {"project": "Hyde Park New Cairo", "developer": "Hyde Park", "field": "commission",
                                "value": "5", "quote": "5", "source_kind": "message", "source_ref": "gv5",
                                "source_ts": int(time.time())})
        hyde = c.execute("SELECT * FROM proposals WHERE source_ref='gv5'").fetchone()
        assert not brain.safe_to_approve(c, hyde, "high", match_p=0.99)[0]


def test_guards_off_keeps_todays_rule(env, monkeypatch):
    db, master, zed = env
    import brain
    monkeypatch.setattr(brain, "GUARDS_V2", False)
    with db.connect() as c:
        p = c.execute("SELECT * FROM proposals WHERE source_ref='gv1'").fetchone()
        assert brain.safe_to_approve(c, p, "high")[0] and not brain.safe_to_approve(c, p, "medium")[0]
