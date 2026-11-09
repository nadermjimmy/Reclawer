"""Learning memory (Phase 3): every PERSON decision (approve / reject with its reason) and every Reopen of a brain
decision is kept as an example, keyed by row_key and developer (never by master_rows.id), so it survives a master
re-upload. Brain decisions are never recorded.

retrieve() returns the most similar past decisions (char n-gram TF-IDF over the normalized source text of the same
source kind, boosted for the same developer, chat and project), balanced between approved and rejected. Under
FEWSHOT=1 they are added to the USER turn of extraction and brain requests; system prompts stay byte-identical so
they keep caching, and batch requests carry them too (they are baked into each request).
"""
import json, os, time
import normalize, profiles, textsim

FEWSHOT_K = int(os.getenv("FEWSHOT_K", "6"))
POOL = 3000  # most recent examples considered per retrieval
CHAT_SQL = ("(CASE WHEN p.source_kind='message' THEN (SELECT sm.group_jid FROM source_messages sm "
            "WHERE sm.wa_id=p.source_ref) ELSE (SELECT sm.group_jid FROM source_rows sr "
            "JOIN file_occurrences fo ON fo.file_id=sr.file_id JOIN source_messages sm ON sm.wa_id=fo.wa_id "
            "WHERE sr.id=CAST(p.source_ref AS INTEGER) ORDER BY sm.ts LIMIT 1) END)")
NOTE = ("How the broker team decided similar suggestions before (approved = read correctly; rejected = should not have "
        "been suggested, with the reason). Use them to judge what counts as an offer and how names map to projects. "
        "Never copy values from them: values come only from the source above.")


def _source_text(c, kind, ref, quote):
    from eval import build_gold
    s = build_gold.source_text(c, kind, ref, limit=1200) or {}
    return (s.get("text") or quote or "")[:1200]


def record(c, pid, decision, reason=None):
    """Store one person decision on proposal `pid` (decision: approved / rejected / reopened)."""
    p = c.execute(f"SELECT p.*, {CHAT_SQL} jid FROM proposals p WHERE p.id=?", (pid,)).fetchone()
    if not p:
        return
    prof = profiles.get(c, p["jid"]) if p["jid"] else None
    text = _source_text(c, p["source_kind"], p["source_ref"], p["quote"])
    out = {"project": p["project"], "developer": p["developer"] or "", "field": p["field"], "value": p["new_raw"],
           "sheet": p["sheet"] or "", "quote": (p["quote"] or "")[:300]}
    c.execute("INSERT INTO learned_examples(proposal_id,source_kind,source_ref,input_text,normalized_text,chat_jid,"
              "chat_developer_key,developer_key,project_key,row_key,field,output_json,decision,reason,created_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (pid, p["source_kind"], p["source_ref"], text, normalize.normalize(text + " " + (p["project"] or "")),
               p["jid"], normalize.compact(prof["developer"]) if prof else "", normalize.compact(p["developer"]),
               p["project_key"], p["row_key"], p["field"], json.dumps(out, ensure_ascii=False), decision,
               reason or p["reject_reason"], int(time.time())))


def _pool(c, kind):
    if kind in (None, "brain"):
        return c.execute("SELECT * FROM learned_examples ORDER BY id DESC LIMIT ?", (POOL,)).fetchall()
    return c.execute("SELECT * FROM learned_examples WHERE source_kind=? ORDER BY id DESC LIMIT ?",
                     (kind, POOL)).fetchall()


def _boosted(ex, sims, developer_key, jid, project_key):
    out = []
    for e, s in zip(ex, sims):
        s = s if s > 0.05 else 0.0
        if developer_key and developer_key in (e["chat_developer_key"], e["developer_key"]):
            s += 0.15  # the same developer's decisions are useful context even when the wording differs
        if jid and e["chat_jid"] == jid:
            s += 0.1
        if project_key and e["project_key"] == project_key:
            s += 0.1
        if s > 0:
            out.append((s, e))
    return out


def _balance(scored, k):
    """Best-first, alternating approved and not-approved so the model sees both kinds; de-duplicated by text."""
    good = [e for s, e in sorted(scored, key=lambda t: -t[0]) if e["decision"] == "approved"]
    bad = [e for s, e in sorted(scored, key=lambda t: -t[0]) if e["decision"] != "approved"]
    out, seen = [], set()
    while (good or bad) and len(out) < k:
        for lst in (good, bad):
            while lst:
                e = lst.pop(0)
                key = (e["normalized_text"][:200], e["field"], e["decision"])
                if key not in seen:
                    seen.add(key)
                    out.append(e)
                    break
            if len(out) >= k:
                break
    return out


def retrieve(c, source_kind, query_text, developer="", k=None, jid=None, project_key=None):
    """Up to k past decisions most like query_text, balanced approved / rejected."""
    return retrieve_multi(c, source_kind, [query_text], developer, k, jid, project_key)


def retrieve_multi(c, source_kind, texts, developer="", k=None, jid=None, project_key=None):
    """Per-request retrieval for a chunk (60 messages, 150 rows): the best examples for each text, merged,
    de-duplicated and capped at k."""
    k = FEWSHOT_K if k is None else k
    ex = _pool(c, source_kind)
    texts = [normalize.normalize(t) for t in texts if (t or "").strip()]
    if not ex or not texts or k <= 0:
        return []
    idx = textsim.Index([e["normalized_text"] for e in ex], f"examples-{source_kind or 'all'}")
    dev = normalize.compact(developer)
    best = {}
    for t in texts:
        for s, e in _boosted(ex, idx.sims(t), dev, jid, project_key):
            if s > best.get(e["id"], (0, None))[0]:
                best[e["id"]] = (s, e)
    return _balance(list(best.values()), k)


def as_prompt(examples):
    out = []
    for e in examples:
        o = json.loads(e["output_json"])
        item = {"source_text": (e["input_text"] or "")[:300], "project": o["project"], "column": o["field"],
                "value": o["value"], "decision": "approved" if e["decision"] == "approved" else "rejected"}
        if e["decision"] != "approved":
            item["reason"] = {"wrong_project": "wrong project", "wrong_value": "value misread",
                              "not_an_offer": "not a developer offer (resale, request, chatter)",
                              "duplicate": "duplicate"}.get(e["reason"] or "", "rejected by a person"
                                                            if e["decision"] == "rejected" else "brain decision undone")
        out.append(item)
    return {"note": NOTE, "examples": out}
