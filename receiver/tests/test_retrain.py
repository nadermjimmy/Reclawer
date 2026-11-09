"""Phase 7: retrain loop (label threshold, promote vs keep, re-upload trigger) and the brain metrics endpoint."""
import json, time
import pytest


def test_should_promote_rules():
    import retrain
    assert retrain.should_promote(0.9, None, 50) == (True, "no current model to beat")
    assert not retrain.should_promote(None, 0.8, 50)[0]
    assert not retrain.should_promote(0.79, 0.80, 50)[0]                       # primary dropped
    assert retrain.should_promote(0.80, 0.80, 50)[0]                           # held
    # a secondary may wobble within max(2 points, 1 SE) but not beyond
    assert retrain.should_promote(0.85, 0.80, 400, [("coverage", 0.59, 0.60)])[0]
    ok, why = retrain.should_promote(0.85, 0.80, 400, [("coverage", 0.50, 0.60)])
    assert not ok and "coverage dropped" in why
    assert retrain.tolerance(0.5, 25) == pytest.approx(0.1)                    # small sample: SE dominates


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import fixtures
    fixtures.run_pipeline()
    import db, master, models
    from test_master import master_bytes
    master.import_workbook(master_bytes(), "App Updates.xlsx")
    old = models.MODELS_DIR
    models.MODELS_DIR = str(tmp_path_factory.mktemp("models"))
    with db.connect() as c:
        for t in ("proposals", "model_runs", "brain_reviews"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM meta WHERE key LIKE 'retrain_%'")
    yield db, master
    models.MODELS_DIR = old
    with db.connect() as c:
        for t in ("proposals", "model_runs"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM meta WHERE key LIKE 'retrain_%' OR key='last_eval_json'")


def _labels(c, n, start=0):
    now = int(time.time())
    for i in range(start, start + n):
        c.execute("INSERT INTO proposals(sheet,project,project_key,field,new_raw,source_kind,source_ref,state,"
                  "decided_at) VALUES('New Cairo','P','p','apartment_2',?,'message',?,'approved',?)",
                  (str(i), f"rt{i}", now - 1000 + i))


def test_threshold_promote_and_keep(env, monkeypatch):
    db, master = env
    import models, retrain
    trained = []

    def fake_matcher(log):
        ver = models.new_version() + str(len(trained))
        models.save("matcher", ver, {"fake": True}, {"x": 1})
        trained.append(ver)
        return ver, {}, len(trained) == 1, "first" if len(trained) == 1 else "primary metric dropped"

    monkeypatch.setattr(retrain, "_matcher", fake_matcher)
    monkeypatch.setattr(retrain, "_noise", lambda log: (_ for _ in ()).throw(ValueError("not enough labels")))
    with db.connect() as c:
        _labels(c, 5)
    assert retrain.run(force=False) is None and not trained                    # 5 < 25 new labels: waits
    with db.connect() as c:
        _labels(c, 30, start=5)
    out = retrain.run(force=False)
    assert out["matcher"]["promoted"] and models.current_version("matcher") == trained[0]
    assert out["noise"] == {"version": None, "promoted": False, "why": "not enough labels"}
    assert retrain.run(force=False) is None                                     # counter moved on
    out = retrain.run(force=True, reason="manual")
    assert not out["matcher"]["promoted"] and models.current_version("matcher") == trained[0]  # kept
    with db.connect() as c:
        runs = c.execute("SELECT kind, promoted, reason, trigger FROM model_runs ORDER BY id").fetchall()
    assert [(r["promoted"], r["trigger"]) for r in runs] == [(1, "nightly"), (0, "manual")]


def test_reupload_triggers_retrain(env, monkeypatch):
    db, master = env
    import retrain
    from test_master import master_bytes
    calls = []
    monkeypatch.setattr(retrain, "run", lambda log, force=False, reason="": calls.append((force, reason)))
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO meta VALUES('retrain_checked_at', ?)", (str(int(time.time())),))
    retrain.tick()
    assert calls == []                                                          # checked recently, nothing pending
    master.import_workbook(master_bytes(), "again.xlsx")
    retrain.tick()
    assert calls == [(True, "re-upload")]


def test_brain_metrics_endpoint(env):
    db, master = env
    from fastapi.testclient import TestClient
    import app as app_mod
    cl = TestClient(app_mod.app, base_url="https://testserver")
    cl.post("/api/login", json={"password": "pw"})
    r = cl.get("/api/brain/metrics", params={"refresh": True}).json()
    assert set(r["models"]) == {"matcher", "noise"} and r["eval"]["matching_v2"]["all"] is not None
    flags = {f["name"]: f for f in r["flags"]}
    assert flags["MATCHER"]["value"] == "legacy" and flags["NOISE_SKIP"]["value"] is None
    assert flags["TEMPLATES"]["value"] is False and flags["BRAIN_AUTO_APPROVE"]["value"] is True
    assert r["labels"] >= 30 and isinstance(r["cost_by_day"], list) and isinstance(r["runs"], list)
    again = cl.get("/api/brain/metrics").json()
    assert again["eval"] == r["eval"]                                           # cached until refreshed
