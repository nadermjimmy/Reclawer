"""Weekly SUGGESTED additions to owner_rules_eg.json, from what people rejected (Phase 3). Never auto-applied:
written to /data/review/rule-suggestions-<date>.json and shown read-only (More > Brain).

- not_project_names: names a person rejected (not an offer / wrong project) at least MIN_REPEAT times, never approved,
  not on the master and not already in the rules.
- locations: of those names, the ones that are also a location code's place name or show up as the location of
  approved rows (a place, not a project).
- blocklist_terms: rejected "not an offer" messages are clustered (char n-gram TF-IDF + agglomerative clustering);
  for clusters of 3+ messages, words found in most of the cluster and in almost no approved message.
"""
import collections, datetime, json, os, re, time
import db, master_schema as ms, normalize, rules, textsim

MIN_REPEAT = 3
REVIEW_DIR = os.path.join(db.DATA_DIR, "review")
WEEK = 7 * 86400


def _texts(c, where, args=()):
    return [r[0] for r in c.execute(
        "SELECT sm.text FROM proposals p JOIN source_messages sm ON sm.wa_id=p.source_ref "
        f"WHERE p.source_kind='message' AND p.decided_by IS NULL AND {where}", args) if r[0]]


def build(c, since_days=90):
    since = int(time.time()) - since_days * 86400
    known = {n.lower() for n in rules.RULES.get("not_project_names", [])} | \
            {n.lower() for n in rules.RULES.get("locations", [])}
    on_master = {r[0] for r in c.execute("SELECT project_key FROM master_rows")}
    approved = {r[0] for r in c.execute("SELECT project_key FROM proposals WHERE state='approved'")}
    names = collections.Counter()
    label = {}
    for r in c.execute("SELECT project, project_key FROM proposals WHERE state='rejected' AND decided_by IS NULL "
                       "AND reject_reason IN ('not_an_offer','wrong_project') AND decided_at>=?", (since,)):
        names[r["project_key"]] += 1
        label.setdefault(r["project_key"], r["project"])
    not_projects = [{"name": label[k], "rejections": n} for k, n in names.most_common()
                    if n >= MIN_REPEAT and k not in on_master and k not in approved and label[k].lower() not in known]
    places = {normalize.compact(v) for v in ms.LOCATION_CODES.values()} | \
             {normalize.compact(w) for v in ms.LOCATION_CODES.values() for w in re.split(r"[/,]", v)}
    locations = [x for x in not_projects if normalize.compact(x["name"]) in places]
    noise = _texts(c, "p.state='rejected' AND p.reject_reason='not_an_offer' AND p.decided_at>=?", (since,))[:500]
    good = _texts(c, "p.state='approved'")[:2000]
    return {"generated_at": int(time.time()), "since_days": since_days, "applied": False,
            "not_project_names": not_projects, "locations": locations,
            "blocklist_terms": _terms(noise, good), "note": "Suggestions only. Copy what you agree with into "
            "owner_rules_eg.json; nothing here changes behaviour."}


def _words(t):
    return set(w for w in normalize.normalize(t).split() if len(w) > 2 and not w.isdigit())


def _terms(noise, good):
    if len(noise) < 3:
        return []
    idx = textsim.Index([normalize.normalize(t) for t in noise], "rules-noise", persist=False)
    if idx.mat is None:
        return []
    from sklearn.cluster import AgglomerativeClustering
    labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                     distance_threshold=0.7).fit_predict(idx.mat.toarray())
    good_df = collections.Counter(w for t in good for w in _words(t))
    out = []
    for lab, members in collections.Counter(labels).items():
        if members < 3:
            continue
        texts = [noise[i] for i, l in enumerate(labels) if l == lab]
        df = collections.Counter(w for t in texts for w in _words(t))
        for w, n in df.most_common(10):
            if n / len(texts) >= 0.6 and good_df[w] <= max(1, len(good) * 0.05):
                out.append({"term": w, "in_rejected": n, "cluster_size": len(texts), "in_approved": good_df[w],
                            "example": texts[0][:200]})
    seen, uniq = set(), []
    for t in sorted(out, key=lambda x: -x["in_rejected"]):
        if t["term"] not in seen and t["term"] not in {x.lower() for x in rules.RULES.get("image_blocklist_terms", [])}:
            seen.add(t["term"])
            uniq.append(t)
    return uniq[:30]


def run(log=lambda *_: None):
    with db.connect() as c:
        out = build(c)
        c.execute("INSERT OR REPLACE INTO meta VALUES('rule_suggestions_at', ?)", (str(int(time.time())),))
    os.makedirs(REVIEW_DIR, exist_ok=True)
    path = os.path.join(REVIEW_DIR, f"rule-suggestions-{datetime.date.today().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"Rule suggestions: {len(out['not_project_names'])} not-a-project names, {len(out['locations'])} locations, "
        f"{len(out['blocklist_terms'])} blocklist terms -> {path} (suggestions only)")
    return out


def maybe_run():
    """Weekly, from the poller."""
    with db.connect() as c:
        r = c.execute("SELECT value FROM meta WHERE key='rule_suggestions_at'").fetchone()
    if r and int(r[0]) > time.time() - WEEK:
        return None
    return run()


def latest():
    if not os.path.isdir(REVIEW_DIR):
        return None
    files = sorted(n for n in os.listdir(REVIEW_DIR) if n.startswith("rule-suggestions-"))
    if not files:
        return None
    with open(os.path.join(REVIEW_DIR, files[-1]), encoding="utf-8") as f:
        return dict(json.load(f), file=files[-1])
