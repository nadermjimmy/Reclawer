"""One matcher for every place that turns a project name into a sheet row (Phase 2).

Pipeline per query (project as written + developer + chat + field + source kind):
1. candidates(): alias hit (developer-aware), rapidfuzz token_set / ratio on normalized and on transliteration keys,
   char n-gram TF-IDF cosine on normalized names and on transliteration keys (so Arabic and Latin meet), the
   developer's own rows, rows this chat updated before, and the group's team-assigned projects.
2. features(): one vector per (query, row) pair, names in FEATURES.
3. score(): the promoted trained model (train_matcher.py, /data/models/matcher-current), else a fixed heuristic.
4. decide(): best row per sheet -> exact / alias / near, or nothing (a new project). A fuzzy hit is 'near' (a person
   or the brain confirms it) unless MATCH_AUTO is set and the probability reaches it.

master.match calls this only under MATCHER=v2 (or alongside the legacy matcher under MATCHER=shadow).
"""
import hashlib, os
import cell_parse as cp, master_schema as ms, models, normalize, textsim


def _env_float(name, default=None):
    v = os.getenv(name, "")
    try:
        return float(v) if v.strip() else default
    except ValueError:
        return default


MATCH_NEAR = _env_float("MATCH_NEAR", 0.6)         # below this the best row isn't offered at all (new project)
MATCH_AUTO = _env_float("MATCH_AUTO")              # unset = off: no fuzzy match is treated as certain
MATCH_TIEBREAK_LOW = _env_float("MATCH_TIEBREAK_LOW", 0.2)
USABLE = ("active", "moved", "new")
KIND = "matcher"

FEATURES = ["exact_key", "alias_hit", "tok_set", "partial", "wratio", "tr_ratio", "tr_tok_set", "tfidf", "tfidf_tr",
            "dev_match", "dev_other", "dev_unknown", "assigned", "assigned_other", "chat_hist", "loc_match",
            "loc_conflict", "sheet_holds_field", "len_ratio", "script_mismatch",
            "src_message", "src_row", "src_pdf_page", "src_image"]

CHAT_SQL = ("(CASE WHEN p.source_kind='message' THEN (SELECT sm.group_jid FROM source_messages sm "
            "WHERE sm.wa_id=p.source_ref) ELSE (SELECT sm.group_jid FROM source_rows sr "
            "JOIN file_occurrences fo ON fo.file_id=sr.file_id JOIN source_messages sm ON sm.wa_id=fo.wa_id "
            "WHERE sr.id=CAST(p.source_ref AS INTEGER) ORDER BY sm.ts LIMIT 1) END)")


def _home(r):
    return r["target_sheet"] if r["state"] == "moved" else r["sheet"]


def dev_compatible(a, b):
    """Two compact developer keys name the same developer ('sodic' / 'sodicdevelopments')."""
    return bool(a and b and len(a) >= 3 and len(b) >= 3 and (a in b or b in a))


def _norm(name):
    return normalize.strip_generic(name) or normalize.normalize(name)


# ---------- index over the usable master rows ----------
class Index:
    def __init__(self, c):
        self.rows = c.execute(f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(USABLE))}) "
                              "ORDER BY id", USABLE).fetchall()
        self.key = [cp.normalize_project_key(r["project"]) for r in self.rows]
        self.norm = [_norm(r["project"]) for r in self.rows]
        self.tr = [normalize.translit_key(r["project"]) for r in self.rows]
        self.dev = [normalize.compact(r["developer"]) for r in self.rows]
        self.sheet = [_home(r) for r in self.rows]
        self.rkey = [normalize.row_key(r["developer"], r["project"], _home(r)) for r in self.rows]
        self.arabic = [normalize.is_arabic(r["project"]) for r in self.rows]
        self.by_key = {}
        for i, k in enumerate(self.key):
            self.by_key.setdefault(k, []).append(i)
        self.devs = sorted({d for d in self.dev if d})
        self.tf = textsim.Index(self.norm, "rows")
        self.tf_tr = textsim.Index(self.tr, "rows-tr")
        # chat history: approved suggestions per (chat, row_key), with the decision time (eval hides later ones)
        self.history = {}
        for r in c.execute(f"SELECT {CHAT_SQL} jid, p.row_key, p.decided_at FROM proposals p "
                           "WHERE p.state='approved' AND p.row_key IS NOT NULL"):
            if r["jid"]:
                self.history.setdefault(r["jid"], []).append((r["row_key"], r["decided_at"] or 0))


_index = {"sig": None, "idx": None}


def _signature(c):
    rows = c.execute("SELECT COUNT(*), COALESCE(MAX(id),0), group_concat(id||state||COALESCE(target_sheet,''), ',') "
                     f"FROM master_rows WHERE state IN ({','.join('?' * len(USABLE))})", USABLE).fetchone()
    appr = c.execute("SELECT COUNT(*), COALESCE(MAX(decided_at),0) FROM proposals WHERE state='approved'").fetchone()
    return hashlib.sha1(repr((tuple(rows), tuple(appr))).encode()).hexdigest()


def index(c):
    sig = _signature(c)
    if _index["sig"] != sig:
        _index.update(sig=sig, idx=Index(c))
    return _index["idx"]


# ---------- queries ----------
class Query:
    def __init__(self, project, developer="", chat_jid=None, assigned=(), field="", source_kind="message",
                 location="", before=None):
        self.project = project or ""
        self.key = cp.normalize_project_key(project)
        self.norm = _norm(project)
        self.tr = normalize.translit_key(project)
        self.dev = normalize.compact(developer)
        self.chat_jid = chat_jid
        self.assigned = set(assigned or ())
        self.field = field
        self.source_kind = source_kind or "message"
        self.location = (location or "").strip().lower()
        self.before = before  # eval replay: only aliases / created rows / chat history from before this time
        self.arabic = normalize.is_arabic(project)


def _aliases(c, q):
    """Project keys an alias points at for this query: {project_key: developer_key ('' = any developer)}."""
    sql = "SELECT project_key, COALESCE(developer_key,'') dk FROM dev_aliases WHERE alias_key=?"
    args = [q.key]
    if q.before:
        sql += " AND confirmed_at<?"
        args.append(q.before)
    out = {}
    for r in c.execute(sql, args):
        if not r["dk"] or not q.dev or dev_compatible(r["dk"], q.dev):
            out[r["project_key"]] = r["dk"]
    return out


class Context:
    """Per-query values shared by candidates() and features()."""

    def __init__(self, c, idx, q):
        self.sims = idx.tf.sims(q.norm)
        self.sims_tr = idx.tf_tr.sims(q.tr) if q.tr else [0.0] * len(idx.rows)
        self.aliases = _aliases(c, q)
        hist = {}
        for rk, ts in idx.history.get(q.chat_jid, []) if q.chat_jid else []:
            if not q.before or ts < q.before:
                hist[rk] = hist.get(rk, 0) + 1
        self.hist = hist
        self.dev_known_rows = bool(q.dev) and any(dev_compatible(q.dev, d) for d in idx.devs)


def _allowed(idx, q, i, sheets):
    r = idx.rows[i]
    if sheets is not None and idx.sheet[i] not in sheets:
        return False
    return not (q.before and r["state"] == "new" and (r["created_at"] or 0) >= q.before)


def candidates(c, idx, q, ctx, sheets=None, k=10):
    from rapidfuzz import fuzz, process
    pool = set(idx.by_key.get(q.key, []))
    for pk, dk in ctx.aliases.items():
        pool |= {i for i in idx.by_key.get(pk, []) if not dk or dev_compatible(dk, idx.dev[i])}
    if q.norm:
        pool |= {i for _, _, i in process.extract(q.norm, idx.norm, scorer=fuzz.token_set_ratio, limit=k,
                                                  score_cutoff=50)}
    if q.tr:
        pool |= {i for _, _, i in process.extract(q.tr, idx.tr, scorer=fuzz.ratio, limit=k, score_cutoff=60)}
    top = lambda s: [i for i in sorted(range(len(s)), key=lambda j: -s[j])[:k] if s[i] > 0.1]
    pool |= set(top(ctx.sims)) | set(top(ctx.sims_tr))
    if q.dev:
        mine = [i for i, d in enumerate(idx.dev) if dev_compatible(q.dev, d)]
        mine.sort(key=lambda i: -max(ctx.sims[i], ctx.sims_tr[i]))
        pool |= set(mine[:30])
    if ctx.hist:
        pool |= {i for i, rk in enumerate(idx.rkey) if rk in ctx.hist}
    if q.assigned:
        pool |= {i for i, key in enumerate(idx.key) if key in q.assigned}
    return [i for i in pool if _allowed(idx, q, i, sheets)]


def features(idx, q, ctx, i):
    from rapidfuzz import fuzz
    r = idx.rows[i]
    rn, rt, rkey = idx.norm[i], idx.tr[i], idx.key[i]
    long_enough = len(q.key) >= 3 and len(rkey) >= 3
    dev_match = dev_compatible(q.dev, idx.dev[i])
    f = {
        "exact_key": float(bool(q.key) and q.key == rkey),
        "alias_hit": float(rkey in ctx.aliases and (not ctx.aliases[rkey] or dev_compatible(ctx.aliases[rkey], idx.dev[i]))),
        "tok_set": fuzz.token_set_ratio(q.norm, rn) / 100 if q.norm and rn else 0.0,
        "partial": fuzz.partial_ratio(q.norm, rn) / 100 if long_enough and q.norm and rn else 0.0,
        "wratio": fuzz.WRatio(q.norm, rn) / 100 if q.norm and rn else 0.0,
        "tr_ratio": fuzz.ratio(q.tr, rt) / 100 if q.tr and rt else 0.0,
        "tr_tok_set": fuzz.token_set_ratio(q.tr, rt) / 100 if q.tr and rt else 0.0,
        "tfidf": ctx.sims[i],
        "tfidf_tr": ctx.sims_tr[i],
        "dev_match": float(dev_match),
        "dev_other": float(bool(q.dev) and ctx.dev_known_rows and not dev_match),
        "dev_unknown": float(not q.dev),
        "assigned": float(bool(q.assigned) and rkey in q.assigned),
        "assigned_other": float(bool(q.assigned) and rkey not in q.assigned),
        "chat_hist": min(ctx.hist.get(idx.rkey[i], 0), 5) / 5,
        "loc_match": float(bool(q.location) and (r["location_code"] or "") == q.location),
        "loc_conflict": float(bool(q.location) and bool(r["location_code"]) and r["location_code"] != q.location),
        "sheet_holds_field": float(not q.field or q.field in ms.PROPOSABLE.get(ms.sheet_kind(idx.sheet[i]), [])
                                   or (q.field == "feedback" and r["kind"] == ms.COMMISSION)),
        "len_ratio": min(len(q.key), len(rkey)) / max(len(q.key), len(rkey), 1),
        "script_mismatch": float(q.arabic != idx.arabic[i]),
    }
    for k in ("message", "row", "pdf_page", "image"):
        f["src_" + k] = float(q.source_kind == k)
    return f


def heuristic(f):
    """Fixed scoring used until a trained model is promoted. Roughly: how sure are we this row is meant."""
    name = max(f["exact_key"], 0.97 * f["alias_hit"],
               f["tok_set"] * (0.95 if f["len_ratio"] >= 0.3 else 0.8),
               # consonant skeletons are short: only near-equal ones mean much, so the ratio is squared
               f["tr_ratio"] ** 2 * (0.92 if f["script_mismatch"] else 0.85),
               f["tr_tok_set"] ** 2 * (0.88 if f["script_mismatch"] else 0.8),
               f["tfidf"], f["tfidf_tr"] * 0.85, f["partial"] * 0.8)
    p = name
    if f["dev_other"]:
        p *= 0.6
    if f["dev_match"]:
        p += (1 - p) * 0.15
    if f["assigned"]:
        p += (1 - p) * 0.2
    elif f["assigned_other"]:
        p *= 0.85
    p += (1 - p) * 0.1 * f["chat_hist"]
    if f["loc_conflict"]:
        p *= 0.9
    if not f["sheet_holds_field"]:
        p *= 0.5
    return min(p, 0.999)


def vector(f):
    return [f[k] for k in FEATURES]


def model():
    """(model, version) of the promoted matcher, or (None, 'heuristic')."""
    m, ver = models.load_current(KIND)
    return (m, ver) if m is not None else (None, "heuristic")


def score_many(feats):
    m, _ = model()
    if m is None or not feats:
        return [heuristic(f) for f in feats]
    try:
        return [float(p) for p in m.predict_proba([vector(f) for f in feats])[:, 1]]
    except Exception:  # an unreadable or incompatible model never stops matching: fall back to the heuristic
        return [heuristic(f) for f in feats]


def scored(c, q, sheets=None):
    """[(row, p, features)] for every candidate, best first."""
    idx = index(c)
    ctx = Context(c, idx, q)
    cand = candidates(c, idx, q, ctx, sheets)
    feats = [features(idx, q, ctx, i) for i in cand]
    ps = score_many(feats)
    out = sorted(zip(cand, ps, feats), key=lambda t: (-t[1], -t[2]["exact_key"], -t[2]["alias_hit"], t[0]))
    return [(idx.rows[i], p, f) for i, p, f in out]


def state_of(p, f):
    if f["exact_key"] and not f["dev_other"]:
        return "exact"
    if f["alias_hit"] and not f["dev_other"]:
        return "alias"
    if MATCH_AUTO is not None and p >= MATCH_AUTO:
        return "alias"  # learned: confident enough to treat like a known name (MATCH_AUTO, off by default)
    return "near"


def decide(c, project, developer, sheets, assigned=None, chat_jid=None, field="", source_kind="message",
           location="", before=None):
    """{sheet: (row, match_state, label, p)} - the best row per sheet above MATCH_NEAR."""
    q = Query(project, developer, chat_jid, assigned, field, source_kind, location, before)
    if not q.key:
        return {}
    out = {}
    for row, p, f in scored(c, q, set(sheets)):
        sheet = _home(row)
        if sheet in out or p < MATCH_NEAR:
            continue
        out[sheet] = (row, state_of(p, f), row["project"], p)
    return out


def row_probability(c, p_row, row, chat_jid=None, assigned=()):
    """Probability that suggestion `p_row` (a proposals row) means sheet row `row`."""
    q = Query(p_row["project"], p_row["developer"] or "", chat_jid, assigned, p_row["field"], p_row["source_kind"],
              p_row["location_code"] or "")
    for r, p, _ in scored(c, q, {_home(row)}):
        if r["id"] == row["id"]:
            return p
    return 0.0
