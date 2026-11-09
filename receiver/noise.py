"""Noise filter for text messages (Phase 4): char n-gram TF-IDF on the normalized message + logistic regression
(class-weighted). Predicts P(offer): the chance a message carries a developer's offer worth reading.

Labels (only these, so real offers rejected for a wrong value or row never teach it to skip):
- noise: messages whose suggestions a person rejected as "not an offer", or the brain rejected as noise and nobody
  reopened;
- offer: messages with an approved suggestion (an approval wins over a noise label on the same message).

NOISE_SKIP (unset = off): messages with P(offer) below it skip the LLM. Every skip is logged to noise_skips with the
score and model version and the message is marked read, so it can be re-queued (Pipeline: "Re-read skipped").
eval/run_eval.py reports the false-skip rate on gold offers; it must stay under 1% before you set NOISE_SKIP.
"""
import os, time
import db, models, normalize

KIND = "noise"
MIN_PER_CLASS = 5
DEFAULT_REPORT_THRESHOLD = 0.2


def _env_float(name):
    v = os.getenv(name, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


SKIP = _env_float("NOISE_SKIP")


def labels(c, before=None):
    """[(wa_id, text, y, decided_at)] with y=1 for an offer, 0 for noise."""
    sql = ("SELECT p.source_ref wa, sm.text, p.state, p.decided_by, p.reject_reason, p.decided_at, b.verdict, "
           "b.action FROM proposals p JOIN source_messages sm ON sm.wa_id=p.source_ref "
           "LEFT JOIN brain_reviews b ON b.proposal_id=p.id WHERE p.source_kind='message' AND sm.text<>''")
    by = {}
    for r in c.execute(sql):
        if before and (r["decided_at"] or 0) >= before:
            continue
        if r["state"] == "approved":
            y = 1
        elif r["state"] == "rejected" and r["decided_by"] is None and r["reject_reason"] == "not_an_offer":
            y = 0
        elif r["state"] == "rejected" and r["decided_by"] == "brain" and r["verdict"] == "noise" and r["action"] != "reopened":
            y = 0
        else:
            continue
        old = by.get(r["wa"])
        if old is None or y > old[2]:  # an approval wins
            by[r["wa"]] = (r["wa"], r["text"], y, r["decided_at"] or 0)
    return list(by.values())


def _pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    return make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, min_df=1),
                         LogisticRegression(class_weight="balanced", max_iter=2000, C=2.0))


def _rates(ps, ys, thresholds=(0.05, 0.1, 0.2, 0.3, 0.4, 0.5)):
    out = []
    offers = [p for p, y in zip(ps, ys) if y == 1]
    noisy = [p for p, y in zip(ps, ys) if y == 0]
    for t in thresholds:
        fs = sum(p < t for p in offers)
        out.append({"threshold": t, "false_skip_rate": round(fs / len(offers), 4) if offers else None,
                    "false_skips": fs, "noise_skipped": round(sum(p < t for p in noisy) / len(noisy), 4) if noisy else None})
    return out


def train(data=None):
    """Returns (version, meta). Raises ValueError with too few labels. Saved, not promoted."""
    if data is None:
        with db.connect() as c:
            data = labels(c)
    data = sorted(data, key=lambda d: d[3])  # time order: the held-out split is the newest quarter
    ys = [d[2] for d in data]
    if sum(ys) < MIN_PER_CLASS or len(ys) - sum(ys) < MIN_PER_CLASS:
        raise ValueError(f"not enough labels (offers {sum(ys)}, noise {len(ys) - sum(ys)}; need {MIN_PER_CLASS} each)")
    cut = int(len(data) * 0.75)
    held = None
    tr, te = data[:cut], data[cut:]
    if len({d[2] for d in tr}) == 2 and te:
        m = _pipeline().fit([normalize.normalize(d[1]) for d in tr], [d[2] for d in tr])
        held = _rates(list(m.predict_proba([normalize.normalize(d[1]) for d in te])[:, 1]), [d[2] for d in te])
    model = _pipeline().fit([normalize.normalize(d[1]) for d in data], ys)
    ver = models.new_version()
    meta = {"trained_at": int(time.time()), "labels": len(data), "offers": sum(ys), "noise": len(ys) - sum(ys),
            "held_out": held, "test_items": len(te)}
    models.save(KIND, ver, model, meta)
    return ver, meta


def model():
    m, ver = models.load_current(KIND)
    return m, ver


def p_offer(texts):
    """P(offer) per text with the promoted model; 1.0 for every text when there is none."""
    m, _ = model()
    if m is None or not texts:
        return [1.0] * len(texts)
    return [float(p) for p in m.predict_proba([normalize.normalize(t) for t in texts])[:, 1]]


def filter_messages(c, msgs):
    """Under NOISE_SKIP: drop messages scoring below it, log each skip and mark it read. Returns the kept messages."""
    if SKIP is None or not msgs:
        return msgs
    m, ver = model()
    if m is None:
        return msgs
    ps = p_offer([x["text"] for x in msgs])
    now = int(time.time())
    kept = []
    for x, p in zip(msgs, ps):
        if p < SKIP:
            c.execute("INSERT OR REPLACE INTO noise_skips(wa_id,score,model_version,threshold,skipped_at,requeued_at) "
                      "VALUES(?,?,?,?,?,NULL)", (x["wa_id"], p, ver, SKIP, now))
            c.execute("INSERT OR IGNORE INTO processed_batches VALUES(?,?)", ("u:" + x["wa_id"], now))
        else:
            kept.append(x)
    return kept


def requeue(log=lambda *_: None, wa_ids=None):
    """Put skipped messages back in the queue (the next read sends them to Claude). Returns how many."""
    with db.connect() as c:
        q = "SELECT wa_id FROM noise_skips WHERE requeued_at IS NULL"
        rows = [r[0] for r in c.execute(q)]
        if wa_ids is not None:
            rows = [w for w in rows if w in set(wa_ids)]
        for w in rows:
            c.execute("DELETE FROM processed_batches WHERE batch_key=?", ("u:" + w,))
            c.execute("UPDATE noise_skips SET requeued_at=? WHERE wa_id=?", (int(time.time()), w))
    log(f"Re-queued {len(rows)} skipped message(s); the next read sends them to Claude")
    return len(rows)
