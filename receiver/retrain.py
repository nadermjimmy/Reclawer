"""Retrain loop (Phase 7): retrain the matcher and the noise filter from person decisions, re-run the eval, promote a
model only when it does at least as well, and keep a record of every run (model_runs).

When: nightly from the poller when there are >= RETRAIN_MIN_LABELS new person labels since the last run; right
after a master re-upload (once relink has run); or by hand (Pipeline: Retrain).

Promotion (PLAN.md concern 5): a fresh model is promoted when its PRIMARY metric on the newest quarter of the gold
set improves or holds against the current model on the same items, and no SECONDARY metric drops by more than the
larger of 2 points and one standard error. Otherwise the current model stays and the reason is logged.
- matcher: primary = held-out top-1 precision; secondary = coverage at MATCH_AUTO (when set)
- noise:   primary = 1 - false-skip rate at the report threshold (NOISE_SKIP or 0.2); secondary = share of noise
           it would skip
"""
import json, math, os, threading, time
import db, models

MIN_NEW = int(os.getenv("RETRAIN_MIN_LABELS", "25"))
NIGHTLY_SECONDS = 20 * 3600
_busy = threading.Lock()


def label_count(c):
    """Person labels: decisions a person made, plus Reopens of brain decisions."""
    n = c.execute("SELECT COUNT(*) FROM proposals WHERE decided_by IS NULL AND state IN ('approved','rejected') "
                  "AND decided_at IS NOT NULL").fetchone()[0]
    return n + c.execute("SELECT COUNT(*) FROM brain_reviews WHERE action='reopened'").fetchone()[0]


def _meta_get(c, key, default=None):
    r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def _meta_set(c, key, value):
    c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))


def tolerance(p, n):
    se = math.sqrt(p * (1 - p) / n) if n and p is not None else 0.0
    return max(0.02, se)


def should_promote(primary_new, primary_old, n, secondaries=()):
    """(promote, reason). secondaries: [(name, new, old)] where higher is better."""
    if primary_new is None:
        return False, "no held-out score for the new model (too few labels for a time split)"
    if primary_old is None:
        return True, "no current model to beat"
    if primary_new + 1e-9 < primary_old:
        return False, f"primary metric dropped {primary_old:.3f} -> {primary_new:.3f}"
    for name, new, old in secondaries:
        if new is None or old is None:
            continue
        tol = tolerance(old, n)
        if new < old - tol:
            return False, f"{name} dropped {old:.3f} -> {new:.3f} (more than {tol:.3f})"
    return True, f"primary {primary_old:.3f} -> {primary_new:.3f}"


def _matcher(log):
    import train_matcher
    ver, meta = train_matcher.train()
    # the current model (or the fixed heuristic when none is promoted) on the same held-out items is the bar
    ok, why = should_promote(meta["test_top1_precision"], meta["current_test_top1_precision"], meta["test_items"],
                             [("coverage at MATCH_AUTO", meta.get("test_coverage_at_auto"),
                               meta.get("current_coverage_at_auto"))])
    return ver, meta, ok, why


def _at(rates, t):
    return next((r for r in rates or [] if abs(r["threshold"] - t) < 1e-9), None)


def _noise(log):
    import noise
    ver, meta = noise.train()
    t = noise.SKIP if noise.SKIP is not None else noise.DEFAULT_REPORT_THRESHOLD
    t = min((r["threshold"] for r in meta["held_out"] or [{"threshold": t}]), key=lambda x: abs(x - t))
    new, old = _at(meta["held_out"], t), _at(meta["current_held_out"], t)
    inv = lambda r: None if not r or r["false_skip_rate"] is None else 1 - r["false_skip_rate"]
    ok, why = should_promote(inv(new), inv(old) if meta["current_version"] else None, meta["test_items"],
                             [("noise skipped", new and new["noise_skipped"], old and old["noise_skipped"])])
    return ver, meta, ok, why


def run(log=lambda *_: None, force=False, reason="nightly"):
    """Retrain both models if due (or force). Returns {kind: {version, promoted, why}} or None when skipped."""
    if not _busy.acquire(blocking=False):
        log("Retrain already running")
        return None
    try:
        with db.connect() as c:
            n = label_count(c)
            last = int(_meta_get(c, "retrain_labels", "0"))
            _meta_set(c, "retrain_checked_at", int(time.time()))
            _meta_set(c, "retrain_pending", 0)
        if not force and n - last < MIN_NEW:
            log(f"Retrain: {n - last} new label(s) since the last run, waiting for {MIN_NEW}")
            return None
        out = {}
        for kind, fn in (("matcher", _matcher), ("noise", _noise)):
            try:
                ver, meta, ok, why = fn(log)
            except ValueError as e:
                out[kind] = {"version": None, "promoted": False, "why": str(e)}
                log(f"Retrain {kind}: skipped, {e}")
                continue
            if ok:
                models.promote(kind, ver)
            models.prune(kind)
            out[kind] = {"version": ver, "promoted": ok, "why": why}
            log(f"Retrain {kind}: {ver} {'promoted' if ok else 'kept the current model'} ({why})")
            with db.connect() as c:
                c.execute("INSERT INTO model_runs(ts,kind,version,promoted,reason,trigger,labels,meta_json) "
                          "VALUES(?,?,?,?,?,?,?,?)", (int(time.time()), kind, ver, int(ok), why, reason, n,
                                                      json.dumps(meta, default=str)))
        report = None
        try:
            from eval import run_eval
            report = run_eval.run()
            with db.connect() as c:
                _meta_set(c, "last_eval_json", json.dumps(_summary(report), default=str))
            log("Eval re-run: match precision (v2) " + str(report["matching_v2"]["all"]["precision"]))
        except Exception as e:
            log(f"Eval re-run failed: {e}")
        with db.connect() as c:
            _meta_set(c, "retrain_labels", n)
            _meta_set(c, "retrain_at", int(time.time()))
        return out
    finally:
        _busy.release()


def _summary(r):
    """The parts of an eval report the Brain panel shows."""
    keep = ("generated_at", "gold", "matching", "matching_v2", "noise", "extraction", "auto_approve", "noise_filter",
            "matcher_model", "shadow_disagreements")
    out = {k: r.get(k) for k in keep}
    out["cross_script_misses"] = len(r.get("cross_script_misses") or [])
    out["cross_script_misses_v2"] = len(r.get("cross_script_misses_v2") or [])
    return out


def mark_reupload():
    """master.import_workbook: retrain once relink has pointed the labels at the new rows."""
    with db.connect() as c:
        _meta_set(c, "retrain_pending", 1)


def tick(log=None):
    """Called by the poller: after a re-upload, or nightly; weekly rule suggestions. Never raises."""
    import jobs
    try:
        with db.connect() as c:
            pending = _meta_get(c, "retrain_pending", "0") == "1"
            checked = int(_meta_get(c, "retrain_checked_at", "0"))
        if (pending or time.time() - checked > NIGHTLY_SECONDS) and not jobs.running():
            jobs.run_now("retrain", lambda lg: run(lg, force=pending, reason="re-upload" if pending else "nightly"))
        import rule_suggestions
        rule_suggestions.maybe_run()
    except Exception as e:
        if log:
            log(f"Retrain tick failed: {e}")
