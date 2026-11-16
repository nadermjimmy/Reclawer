"""Lessons memory: what the team's decisions teach, as weighted, explainable statements.

Mined from every person decision (and Reopen of a brain decision) per scope: a group, a developer, a column, a
source type, a group+column, a developer+column, and each accuracy-gate flag. A lesson exists when the evidence is
real: at least MIN_SUPPORT decisions and a Bayesian (Beta) reject or approve rate clearly above the team's average.
Its weight = how far it is from the average x log(evidence), so a 9-of-10 pattern outweighs a 3-of-4 one.

Used three ways:
1. the accuracy gate moves a suggestion from Ready to Check when a strong reject lesson matches it (and shows why);
2. extraction (when the gate is on) tells Claude, in the user turn, the lessons for that group's developer, so the
   same mistakes aren't read again;
3. More > Brain lists them, strongest first.
Optionally (LESSONS_LLM=1) Claude rewrites the strongest reject lessons as one practical sentence each, from the
evidence and example quotes; the counts and weights stay computed, never invented.
"""
import collections, json, math, os, time
import db, master_schema as ms, names

MIN_SUPPORT = 5
REASON_TEXT = {"wrong_project": "wrong project", "wrong_value": "wrong value", "not_an_offer": "not an offer",
               "duplicate": "duplicate", None: "no reason given"}
LLM = os.getenv("LESSONS_LLM", "0") == "1"


def _field_label(f):
    return ms.UNIT_LABELS.get(f, (f or "").replace("_", " "))


def _scopes(d, group_name, flags):
    dev = names.key(d.get("row_developer") or d.get("developer") or "")
    out = [("group", d.get("chat_jid") or "", None), ("developer", dev, None), ("field", d["field"], None),
           ("source", d["source_kind"], None), ("group_field", d.get("chat_jid") or "", d["field"]),
           ("developer_field", dev, d["field"])]
    out += [("flag", f, None) for f in flags]
    return [s for s in out if s[1]]


def _describe(scope, value, field, outcome, n, hits, reason, group_names, dev_names):
    who = {"group": f"in {group_names.get(value, value)}", "developer": f"for {dev_names.get(value, value)}",
           "field": f"for {_field_label(value)}", "source": f"from {value.replace('_', ' ')}s",
           "group_field": f"for {_field_label(field)} in {group_names.get(value, value)}",
           "developer_field": f"for {_field_label(field)} of {dev_names.get(value, value)}",
           "flag": f"when '{value.replace('_', ' ')}'"}[scope]
    if outcome == "rejected":
        return f"Suggestions {who} were rejected {hits} of {n} times (mostly: {REASON_TEXT.get(reason, reason)})."
    return f"Suggestions {who} were approved {hits} of {n} times."


def mine(log=lambda *_: None):
    """Recompute every lesson from the decisions. Returns how many lessons exist."""
    import acceptance
    with db.connect() as c:
        ds = acceptance.decisions(c)
        groups = {r["jid"]: r["name"] for r in c.execute("SELECT jid, name FROM groups")}
        devs = {names.key(r[0]): r[0] for r in c.execute("SELECT DISTINCT developer FROM master_rows WHERE developer<>''")}
        stats = collections.defaultdict(lambda: {"n": 0, "ok": 0, "w": 0.0, "reasons": collections.Counter(),
                                                 "examples": []})
        total = sum(d["label"] for d in ds), len(ds)
        for d in ds:
            flags = json.loads(d.get("gate_json") or "{}").get("flags", [])
            for s in _scopes(d, groups, flags):
                st = stats[s]
                st["n"] += 1
                st["ok"] += d["label"]
                if not d["label"]:
                    st["reasons"][d["reject_reason"]] += 1
                    if len(st["examples"]) < 3 and d.get("quote"):
                        st["examples"].append({"project": d["project"], "value": d["new_raw"],
                                               "quote": (d["quote"] or "")[:200]})
        base_ok = (total[0] + 1) / (total[1] + 2) if total[1] else 0.5
        now = int(time.time())
        rows = []
        for (scope, value, field), st in stats.items():
            n = st["n"]
            if n < MIN_SUPPORT:
                continue
            p_rej = (n - st["ok"] + 1) / (n + 2)
            p_ok = (st["ok"] + 1) / (n + 2)
            if p_rej >= 0.6 and p_rej > (1 - base_ok) + 0.15:
                reason = st["reasons"].most_common(1)[0][0] if st["reasons"] else None
                weight = -(p_rej - (1 - base_ok)) * math.log1p(n)
                rows.append((scope, value, field, "rejected", reason, n, n - st["ok"], p_rej, weight, st["examples"]))
            elif p_ok >= 0.9 and n >= 8 and p_ok > base_ok + 0.05:
                rows.append((scope, value, field, "approved", None, n, st["ok"], p_ok, (p_ok - base_ok) * math.log1p(n),
                             []))
        c.execute("DELETE FROM lessons")
        for scope, value, field, outcome, reason, n, hits, rate, weight, ex in rows:
            text = _describe(scope, value, field, outcome, n, hits, reason, groups, devs)
            c.execute("INSERT OR REPLACE INTO lessons(scope,scope_value,field,outcome,reason,support,hits,rate,weight,"
                      "text,examples_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (scope, value, field or "", outcome, reason or "", n, hits, round(rate, 4), round(weight, 4),
                       text, json.dumps(ex, ensure_ascii=False), now))
    log(f"Lessons: {len(rows)} from {total[1]} decisions")
    if LLM and rows:
        try:
            refine_with_claude(log)
        except Exception as e:
            log(f"Lessons: Claude rewrite skipped ({e})")
    return len(rows)


def load(c):
    return [dict(r) for r in c.execute("SELECT * FROM lessons ORDER BY ABS(weight) DESC")]


def matching(lessons, scope):
    """Lessons that apply to a suggestion's scope {group, developer, field, source, flags}, strongest first."""
    out = []
    for l in lessons:
        s, v, f = l["scope"], l["scope_value"], l["field"]
        ok = ((s == "group" and v == scope["group"]) or (s == "developer" and v == scope["developer"])
              or (s == "field" and v == scope["field"]) or (s == "source" and v == scope["source"])
              or (s == "group_field" and v == scope["group"] and f == scope["field"])
              or (s == "developer_field" and v == scope["developer"] and f == scope["field"])
              or (s == "flag" and v in scope.get("flags", ())))
        if ok:
            out.append(l)
    return out


def for_chat(c, jid, developer=""):
    """Lesson sentences to give Claude when reading this chat (user turn), strongest first, at most 6."""
    dev = names.key(developer or "")
    ls = [l for l in load(c) if l["outcome"] == "rejected" and
          ((l["scope"] in ("group", "group_field") and l["scope_value"] == jid) or
           (dev and l["scope"] in ("developer", "developer_field") and l["scope_value"] == dev))]
    return [l["text"] for l in ls[:6]]


SCHEMA = {"type": "object", "properties": {"lessons": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
    "required": ["id", "text"], "additionalProperties": False}}}, "required": ["lessons"], "additionalProperties": False}
SYSTEM = """You turn statistics about a real-estate broker team's rejected suggestions into short, practical
guidance for the AI that reads WhatsApp messages and proposes sheet updates. For each lesson write ONE sentence
(max 30 words) saying what to do differently, grounded only in the counts and example quotes given. Keep the
numbers. No names of salespeople. English."""


def refine_with_claude(log=lambda *_: None, top=15):
    import llm
    with db.connect() as c:
        ls = [l for l in load(c) if l["outcome"] == "rejected"][:top]
    if not ls:
        return 0
    body = [{"id": l["id"], "statement": l["text"], "examples": json.loads(l["examples_json"] or "[]")} for l in ls]
    out = llm.call_json(SYSTEM, json.dumps({"lessons": body}, ensure_ascii=False), SCHEMA,
                        model=os.getenv("CLAUDE_TIEBREAK_MODEL", "claude-haiku-5-5"), max_tokens=4000,
                        purpose="lessons")["lessons"]
    with db.connect() as c:
        for o in out:
            if any(l["id"] == o["id"] for l in ls) and o["text"].strip():
                c.execute("UPDATE lessons SET text=? WHERE id=?", (o["text"].strip()[:400], o["id"]))
    log(f"Lessons: Claude rewrote {len(out)} lesson(s)")
    return len(out)
