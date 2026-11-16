"""Approval model: how likely is the team to approve this suggestion? Learns from every person decision.

Multi-signal and weighted:
- ~35 signals per suggestion: match quality (exact / near / new), whether the row belongs to the group's developer,
  catalog ownership, price per m² against the sheet and against the current value, area, format, source kind,
  age, brain verdict, the accuracy-gate flags, and history priors;
- history priors are Bayesian (Beta-smoothed) approval rates of the same group, developer, field, source kind and
  match type, computed only from decisions made BEFORE each training example (no peeking at the answer);
- training examples are weighted: recent decisions count more (60-day half-life), a Reopen of a brain decision
  counts 1.5x, a 'duplicate' rejection half (it says little about correctness);
- logistic regression while labels are few, gradient-boosted trees (LightGBM) from 300 labels, calibrated so the
  probability means what it says; every prediction comes with the signals that pushed it up or down.

Promotion and retraining go through retrain.py (a new model replaces the current one only if it does at least as
well on the newest decisions). Nothing here approves anything.
"""
import collections, json, math, os, time
import db, models, names

KIND = "acceptance"
MIN_LABELS, MIN_CLASS = 30, 5
LGBM_FROM = 300
HALF_LIFE_DAYS = 60
FLAG_FEATURES = ["aed_in_quote", "uae_group", "other_developer_row", "already_on_sheet", "aed_scale", "near_name",
                 "project_other_developer", "price_too_low", "price_too_high", "price_jump", "area_odd", "bad_format",
                 "lifts_hold", "group_developer_unknown", "exists_on_sheet", "learned_warning", "old_value_changed"]
FEATURES = (["m_exact", "m_near", "m_new", "src_message", "src_row", "src_pdf_page", "src_image", "f_unit",
             "f_commercial", "f_payment_plan", "f_commission", "f_rtm", "f_text", "group_known", "group_team",
             "catalog_owned", "log_price_ratio", "abs_log_price_ratio", "ppsm_z", "age_days", "quote_len",
             "brain_update", "brain_noise", "brain_new", "brain_high",
             "prior_group", "prior_developer", "prior_field", "prior_source", "prior_match", "n_group", "n_developer"]
            + ["flag_" + f for f in FLAG_FEATURES])
LABELS = {"m_exact": "exact project name", "m_near": "only a close project name", "m_new": "a new project",
          "group_known": "the group has a known developer", "group_team": "the team assigned the group",
          "catalog_owned": "the catalog confirms the project is this developer's",
          "abs_log_price_ratio": "size of the price change", "ppsm_z": "price per m² vs the sheet",
          "age_days": "age of the message", "prior_group": "this group's approval history",
          "prior_developer": "this developer's approval history", "prior_field": "this column's approval history",
          "prior_source": "this source type's approval history", "prior_match": "approval history of this match type",
          "brain_update": "the brain's verdict", "brain_noise": "the brain called it noise"}


# ---------- history priors ----------
class Priors:
    """Beta-smoothed approval rates per group / developer / field / source kind / match type."""
    STRENGTH = 4.0

    def __init__(self, c=None):
        self.n = collections.Counter()
        self.ok = collections.Counter()
        self.total = [0, 0]  # approvals, decisions
        if c is not None:
            for d in decisions(c):
                self.add(d)

    @staticmethod
    def keys(d):
        return [("group", d.get("chat_jid") or ""), ("developer", names.key(d.get("row_developer") or d.get("developer")
                                                                           or "")),
                ("field", d["field"]), ("source", d["source_kind"]), ("match", d.get("first_match_state") or
                                                                      d.get("match_state") or "")]

    def add(self, d):
        y = 1 if d["label"] else 0
        for k in self.keys(d):
            self.n[k] += 1
            self.ok[k] += y
        self.total[0] += y
        self.total[1] += 1

    def rate(self, k):
        base = (self.total[0] + 1) / (self.total[1] + 2)
        return (self.ok[k] + self.STRENGTH * base) / (self.n[k] + self.STRENGTH)


def decisions(c, since=None):
    """Person decisions (and Reopens of brain decisions) as training examples, oldest first."""
    from gate import CHAT_SQL
    q = (f"SELECT p.*, {CHAT_SQL} chat_jid, r.developer row_developer, b.verdict brain_verdict, "
         f"b.confidence brain_confidence, b.action brain_action FROM proposals p "
         f"LEFT JOIN master_rows r ON r.id=COALESCE(p.row_id, p.label_row_id) "
         f"LEFT JOIN brain_reviews b ON b.proposal_id=p.id "
         "WHERE (p.decided_at IS NOT NULL AND p.decided_by IS NULL AND p.state IN ('approved','rejected')) "
         "OR b.action='reopened' ORDER BY COALESCE(p.decided_at, p.created_at), p.id")
    out = []
    for p in c.execute(q).fetchall():
        d = dict(p)
        if d["brain_action"] == "reopened" and d["decided_by"] is None and d["state"] == "proposed":
            continue  # reopened, not decided again yet
        d["label"] = 1 if d["state"] == "approved" else 0
        w = {"duplicate": 0.5, None: 0.8}.get(d["reject_reason"], 1.0) if not d["label"] else 1.0
        if d["brain_action"] == "reopened":
            w *= 1.5
        age = max(0.0, (time.time() - (d["decided_at"] or d["created_at"] or time.time())) / 86400)
        d["weight"] = w * 0.5 ** (age / HALF_LIFE_DAYS)
        out.append(d)
    return out


# ---------- signals ----------
def features(p, flags, scope, priors, price_ratio=None, ppsm_z=None, group_known=False, group_team=False,
             catalog_owned=False):
    get = lambda k: p[k] if k in p.keys() else None
    field = p["field"]
    ms_ = get("match_state") or ""
    lr = math.log(price_ratio) if price_ratio else 0.0
    d = {"chat_jid": scope["group"], "developer": scope["developer"], "row_developer": scope["developer"],
         "field": field, "source_kind": p["source_kind"], "first_match_state": get("first_match_state"),
         "match_state": ms_}
    keys = Priors.keys(d)
    f = {
        "m_exact": float(ms_ in ("exact", "alias")), "m_near": float(ms_ == "near"), "m_new": float(ms_ == "new"),
        "src_message": float(p["source_kind"] == "message"), "src_row": float(p["source_kind"] == "row"),
        "src_pdf_page": float(p["source_kind"] == "pdf_page"), "src_image": float(p["source_kind"] == "image"),
        "f_unit": float(field not in ("payment_plan", "commission", "ready_to_move", "feedback", "location")),
        "f_commercial": float(field in ("office", "clinic", "shop", "pharmacy")),
        "f_payment_plan": float(field == "payment_plan"), "f_commission": float(field == "commission"),
        "f_rtm": float(field == "ready_to_move"), "f_text": float(field in ("feedback", "location")),
        "group_known": float(group_known), "group_team": float(group_team), "catalog_owned": float(catalog_owned),
        "log_price_ratio": lr, "abs_log_price_ratio": abs(lr), "ppsm_z": ppsm_z or 0.0,
        "age_days": math.log1p(max(0.0, (time.time() - (get("source_ts") or time.time())) / 86400)),
        "quote_len": math.log1p(len(get("quote") or "")),
        "brain_update": float(get("brain_verdict") == "update_existing"),
        "brain_noise": float(get("brain_verdict") == "noise"), "brain_new": float(get("brain_verdict") == "new_project"),
        "brain_high": float(get("brain_confidence") == "high"),
        "prior_group": priors.rate(keys[0]), "prior_developer": priors.rate(keys[1]), "prior_field": priors.rate(keys[2]),
        "prior_source": priors.rate(keys[3]), "prior_match": priors.rate(keys[4]),
        "n_group": math.log1p(priors.n[keys[0]]), "n_developer": math.log1p(priors.n[keys[1]]),
    }
    for fl in FLAG_FEATURES:
        f["flag_" + fl] = float(fl in flags)
    return f


def vector(f):
    return [f.get(k, 0.0) for k in FEATURES]


# ---------- model ----------
def model():
    m, ver = models.load_current(KIND)
    return (m, ver) if m is not None else (None, None)


def predict(m, f, x=None):
    """(probability, [(signal, push)]) or (None, []) when no model is promoted."""
    if m is None:
        return None, []
    try:
        x = [vector(f) if x is None else x]
        if m.get("scaler") is not None:
            x = m["scaler"].transform(x)
        p = float(m["cal"].predict_proba(x)[0, 1])
        contrib = explain(m, x)
        return round(p, 4), contrib
    except Exception:
        return None, []


def explain(m, x):
    """Signals that moved this prediction, strongest first: [(label, +/- weight)] (log-odds units)."""
    if m["kind"] == "lightgbm":
        vals = m["est"].predict(x, pred_contrib=True)[0][:-1]
    else:
        vals = x[0] * m["est"].coef_[0]  # x is already scaled
    pairs = sorted(zip(FEATURES, vals), key=lambda t: -abs(t[1]))[:4]
    return [(LABELS.get(k, k.replace("flag_", "").replace("_", " ")), round(float(v), 3)) for k, v in pairs
            if abs(v) > 0.05]


def build_examples(c):
    """(X, y, w, ts) from decisions, each with priors computed only from the decisions before it."""
    import gate
    ctx = gate.Context(c)
    ctx.priors = Priors()  # replayed in time order below
    X, y, w, ts = [], [], [], []
    rows = {r["id"]: r for r in c.execute("SELECT * FROM master_rows")}
    for d in decisions(c):
        p = _Row(d)
        row = rows.get(d["row_id"] or d["label_row_id"]) if (d["row_id"] or d["label_row_id"]) else None
        e = gate.evaluate(c, p, ctx, row)
        X.append(vector(e["features"]))
        y.append(d["label"])
        w.append(d["weight"])
        ts.append(d["decided_at"] or d["created_at"] or 0)
        ctx.priors.add(d)
    return X, y, w, ts


class _Row(dict):
    """dict that answers p['x'] and p.keys() like a sqlite Row."""
    def __getitem__(self, k):
        return dict.get(self, k)


def _fit(X, y, w):
    import numpy as np
    n_pos, n_neg = sum(y), len(y) - sum(y)
    if len(y) < MIN_LABELS or n_pos < MIN_CLASS or n_neg < MIN_CLASS:
        raise ValueError(f"not enough decisions yet ({len(y)} labelled: {n_pos} approved, {n_neg} rejected; "
                         f"need {MIN_LABELS} with {MIN_CLASS} of each)")
    from sklearn.calibration import CalibratedClassifierCV
    if len(y) >= LGBM_FROM and min(n_pos, n_neg) >= 30:
        from lightgbm import LGBMClassifier
        est = LGBMClassifier(n_estimators=300, learning_rate=0.04, num_leaves=15, min_child_samples=15,
                             subsample=0.9, colsample_bytree=0.8, verbose=-1)
        est.fit(np.array(X), np.array(y), sample_weight=np.array(w))
        kind, explainer = "lightgbm", est.booster_
        scaler = None
    else:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler().fit(np.array(X))
        X = scaler.transform(np.array(X))  # scaled here, so the sample weights reach the model and the calibration
        est = LogisticRegression(C=0.5, class_weight="balanced", max_iter=3000)
        est.fit(X, np.array(y), sample_weight=np.array(w))
        kind, explainer = "logreg", est
    folds = min(3, n_pos, n_neg)
    if folds >= 2:
        cal = CalibratedClassifierCV(est, method="sigmoid", cv=folds)
        cal.fit(np.array(X), np.array(y), sample_weight=np.array(w))
    else:
        cal = est
    return {"kind": kind, "est": explainer, "cal": cal, "scaler": scaler}


def _precision_at(ps, ys, t):
    sel = [y for p, y in zip(ps, ys) if p >= t]
    return (sum(sel) / len(sel), len(sel)) if sel else (None, 0)


def train(log=lambda *_: None):
    """Train on every person decision; returns (version, meta). Saved, not promoted (retrain.py promotes)."""
    with db.connect() as c:
        X, y, w, ts = build_examples(c)
    order = sorted(range(len(y)), key=lambda i: ts[i])
    cut = int(len(order) * 0.75)
    tr, te = order[:cut], order[cut:]
    held = None
    try:
        m_split = _fit([X[i] for i in tr], [y[i] for i in tr], [w[i] for i in tr])
        ps = [predict(m_split, None, x=X[i])[0] for i in te]
        ys = [y[i] for i in te]
        held = {"n": len(te), "approval_rate": round(sum(ys) / len(ys), 4) if ys else None,
                "precision_at": {str(t): _precision_at(ps, ys, t) for t in (0.8, 0.85, 0.9, 0.95)}}
        # the lowest threshold whose held-out precision is >= 99% (with at least 20 suggestions above it)
        best = next((t / 100 for t in range(50, 100) if (_precision_at(ps, ys, t / 100)[0] or 0) >= 0.99
                     and _precision_at(ps, ys, t / 100)[1] >= 20), None)
        held["ready_threshold_99"] = best
    except ValueError:
        pass
    m = _fit(X, y, w)
    ver = models.new_version()
    meta = {"trained_at": int(time.time()), "model": m["kind"], "labels": len(y), "approved": sum(y),
            "features": FEATURES, "held_out": held}
    models.save(KIND, ver, m, meta)
    log(f"Approval model {ver}: {m['kind']} on {len(y)} decisions" + (f"; held-out {held}" if held else ""))
    return ver, meta
