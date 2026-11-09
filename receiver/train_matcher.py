"""Train the matcher's scoring model from person decisions (Phase 2).

Pairs: every gold matching item (eval/build_gold.collect) is replayed through matcher.candidates with only what was
known before the person decided (aliases, created rows, chat history). Positive = the candidate whose row_key is
the labelled row; negatives = the other candidates (all of them for a new project; the rejected row for
'wrong project').

Model: logistic regression below 300 positive labels, LightGBM from 300 on; calibrated with CalibratedClassifierCV
(sigmoid) when there are enough labels of both classes. A time split (oldest 75% train / newest 25% test) gives the
precision/coverage curve and the suggested MATCH_AUTO (precision >= 0.98) and MATCH_TIEBREAK_LOW. The saved model is
then refitted on everything. Saved under /data/models/matcher-<version>/; promotion is separate (retrain.py or
--promote).

Run in the container:  python train_matcher.py [--promote]
"""
import argparse, json, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db, matcher, models  # noqa: E402

LGBM_FROM = 300
TARGET_PRECISION = 0.98


def _sheets_for(field):
    import master_schema as ms
    kinds = [k for k, fs in ms.PROPOSABLE.items() if field in fs]
    return {s for s, k in ms.SHEETS.items() if k in kinds}


def pairs(c, matching):
    """[(item_index, features, label, row_key)] and the number of items whose labelled row wasn't a candidate."""
    import profiles
    idx = matcher.index(c)
    out, missed = [], 0
    for n, it in enumerate(matching):
        lab = it["label"]
        if lab["type"] not in ("row", "new_project", "wrong_project"):
            continue
        q = matcher.Query(it["project"], it["developer"] or it.get("chat_developer") or "", it.get("chat_jid"),
                          profiles.assigned_keys(c, it.get("chat_jid")), it["field"], it["source_kind"], "",
                          it["decided_at"])
        if not q.key:
            continue
        ctx = matcher.Context(c, idx, q)
        cand = matcher.candidates(c, idx, q, ctx, _sheets_for(it["field"]))
        gold = lab.get("row_key") if lab["type"] == "row" else None
        found = False
        for i in cand:
            rk = idx.rkey[i]
            if lab["type"] == "wrong_project" and rk != lab.get("not_row_key"):
                continue  # only the rejected row is a known negative
            y = int(gold is not None and rk == gold)
            found |= bool(y)
            out.append((n, matcher.features(idx, q, ctx, i), y, rk))
        missed += gold is not None and not found
    return out, missed


def _fit(X, y):
    n_pos, n_neg = sum(y), len(y) - sum(y)
    if n_pos < 2 or n_neg < 2:
        raise ValueError(f"not enough labels to train (positives {n_pos}, negatives {n_neg})")
    if n_pos >= LGBM_FROM:
        from lightgbm import LGBMClassifier
        base, kind = LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=10,
                                    class_weight="balanced", verbose=-1), "lightgbm"
    else:
        from sklearn.linear_model import LogisticRegression
        base, kind = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000), "logreg"
    folds = min(3, n_pos, n_neg)
    if folds >= 2 and n_pos >= 10:
        from sklearn.calibration import CalibratedClassifierCV
        m = CalibratedClassifierCV(base, method="sigmoid", cv=folds)
        kind += "+calibrated"
    else:
        m = base
    m.fit(X, y)
    return m, kind


def curve(test_items):
    """test_items: [(top_p, top_is_correct)] per query. Precision/coverage at thresholds."""
    pts = []
    for t in [i / 100 for i in range(0, 100, 5)] + [0.97, 0.98, 0.99]:
        sel = [ok for p, ok in test_items if p >= t]
        pts.append({"threshold": t, "coverage": round(len(sel) / len(test_items), 4) if test_items else None,
                    "precision": round(sum(sel) / len(sel), 4) if sel else None, "n": len(sel)})
    return sorted(pts, key=lambda p: p["threshold"])


def _bootstrap_low(oks, reps=500):
    """5th percentile of precision over bootstrap resamples (a lower bound for small samples)."""
    if not oks:
        return None
    rnd = random.Random(1)
    vals = sorted(sum(rnd.choice(oks) for _ in oks) / len(oks) for _ in range(reps))
    return round(vals[int(reps * 0.05)], 4)


def suggest(test_items, pts):
    auto = next((p["threshold"] for p in pts if p["precision"] is not None and p["precision"] >= TARGET_PRECISION
                 and p["n"] >= 5), None)
    correct = sorted(p for p, ok in test_items if ok)
    low = round(correct[int(len(correct) * 0.05)], 2) if correct else None
    sel = [ok for p, ok in test_items if auto is not None and p >= auto]
    return {"MATCH_AUTO": auto, "MATCH_TIEBREAK_LOW": low, "precision_lower_bound_at_auto": _bootstrap_low(sel)}


def _top_per_item(model_fn, prs, items):
    by = {}
    for n, f, y, _ in prs:
        if n in items:
            by.setdefault(n, []).append((f, y))
    out = []
    for n, lst in by.items():
        ps = model_fn([f for f, _ in lst])
        best = max(range(len(lst)), key=lambda i: ps[i])
        out.append((ps[best], bool(lst[best][1])))
    return out


def train(matching=None, out_meta=None):
    """Train on the gold set; returns (version, meta). Raises ValueError when there are too few labels."""
    from eval import build_gold
    with db.connect() as c:
        if matching is None:
            matching, _ = build_gold.collect(c)
        prs, missed = pairs(c, matching)
    if not prs:
        raise ValueError("no gold matching items yet")
    order = sorted({n for n, *_ in prs}, key=lambda n: matching[n]["decided_at"] or 0)
    cut = max(1, int(len(order) * 0.75))
    train_items, test_items = set(order[:cut]), set(order[cut:])
    X = lambda sel: [matcher.vector(f) for n, f, y, _ in prs if n in sel]
    Y = lambda sel: [y for n, f, y, _ in prs if n in sel]
    held = None
    try:
        m_split, _ = _fit(X(train_items), Y(train_items))
        fn = lambda fs: list(m_split.predict_proba([matcher.vector(f) for f in fs])[:, 1])
        held = _top_per_item(fn, prs, test_items)
    except ValueError:
        pass
    m, kind = _fit(X(set(order)), Y(set(order)))
    heur = _top_per_item(lambda fs: [matcher.heuristic(f) for f in fs], prs, test_items)
    pts = curve(held) if held else []
    ver = models.new_version()
    meta = {"trained_at": int(time.time()), "model": kind, "features": matcher.FEATURES,
            "labels": len(order), "pairs": len(prs), "positives": sum(y for *_, y, _ in prs),
            "gold_row_not_in_candidates": missed, "test_items": len(test_items),
            "test_top1_precision": round(sum(ok for _, ok in held) / len(held), 4) if held else None,
            "heuristic_test_top1_precision": round(sum(ok for _, ok in heur) / len(heur), 4) if heur else None,
            "curve": pts, "suggested": suggest(held or [], pts)}
    models.save(matcher.KIND, ver, m, meta)
    if out_meta:
        out_meta.update(meta)
    return ver, meta


def print_curve(meta):
    print(f"Model {meta['model']}: {meta['labels']} labelled items, {meta['pairs']} pairs, "
          f"{meta['gold_row_not_in_candidates']} gold rows not among the candidates")
    print(f"Held-out top-1 precision: {meta['test_top1_precision']} (heuristic: {meta['heuristic_test_top1_precision']})")
    print("threshold  coverage  precision  n")
    for p in meta["curve"]:
        print(f"{p['threshold']:>9.2f}  {p['coverage']}  {p['precision']}  {p['n']}")
    print("Suggested:", json.dumps(meta["suggested"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--promote", action="store_true", help="make this version the one the matcher uses")
    a = ap.parse_args()
    db.init()
    v, meta = train()
    print_curve(meta)
    if a.promote:
        models.promote(matcher.KIND, v)
        print("Promoted", v)
    else:
        print("Saved", v, "(not promoted)")
