"""The brain: a second look at open suggestions before a person sees them.

For each project in the open suggestions it gathers the sheet rows that could be meant (similar names, the same
developer's rows, rows this chat has updated before) with their current values, and asks Claude whether each
suggestion updates one of those rows, is a genuinely new project, or is noise. Code then acts on the answer:

- update_existing: the suggestion is pointed at that row (old value recomputed); if the row already holds the
  value it is rejected as a duplicate.
- auto-approve, only when every rule in `safe_to_approve` holds (owner decision): an existing row, high confidence,
  value in sheet format, unit price within +-20%, no Hold/Hide, not commission/feedback/location.
- noise with high confidence: rejected, with the reason; a person can reopen it.
- everything else stays open on Updates with the brain's reason.

Claude never writes to the sheet; every change goes through master.decide_detail like a person's approval.
"""
import difflib, json, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import cell_parse as cp, db, llm, master, master_schema as ms, profiles

MODEL = os.getenv("CLAUDE_BRAIN_MODEL", "claude-opus-5-5")
PRICE_BAND = float(os.getenv("BRAIN_PRICE_BAND", "0.20"))
GROUPS_PER_REQUEST = 10
WORKERS = int(os.getenv("CLAUDE_WORKERS", "6"))
AUTO_FIELDS = set(ms.UNIT_FIELDS) | {"payment_plan", "ready_to_move"}  # never commission, feedback, location

CHAT_SQL = ("(CASE WHEN p.source_kind='message' THEN (SELECT sm.group_jid FROM source_messages sm "
            "WHERE sm.wa_id=p.source_ref) ELSE (SELECT sm.group_jid FROM source_rows sr "
            "JOIN file_occurrences fo ON fo.file_id=sr.file_id JOIN source_messages sm ON sm.wa_id=fo.wa_id "
            "WHERE sr.id=CAST(p.source_ref AS INTEGER) ORDER BY sm.ts DESC LIMIT 1) END)")

SYSTEM = """You check suggested updates to an Egyptian real-estate broker team's project sheet before a person
sees them. Each suggestion was read from a WhatsApp chat (or a file posted there): a project name as written in
the chat, a column, and a value. The code that found a sheet row for it only compares spellings, so it often
fails on Arabic/English spellings, a developer name in front of the project, abbreviations or phase names, and
then wrongly proposes a NEW project.

For every suggestion decide, using the candidate rows given with it (and their current values):
- update_existing: it is about one of the candidate rows. Give that row_id. Same project in another spelling,
  language, with/without developer name or 'phase'/'compound' words counts as the same row. If the project is
  on two sheets, pick the row whose sheet holds this kind of column (prices/payment plans live on the price-grid
  sheets; commission and feedback on Sheet 2 / Separated update).
- new_project: you are confident none of the candidates is this project (a launch, a project the sheet doesn't
  have yet). Give row_id 0.
- noise: the suggestion is not a developer's offer for that project: a resale or a client's own unit, a request
  or question, a different project than the value belongs to, a value misread from the text, or a broker's
  personal note. Give row_id 0.
- unsure: you can't tell from what is given. Give row_id 0.

chat_profile, when given, says whose group the chat is and how that developer shares data. In a developer's own
group the suggestions are about that developer's projects: prefer that developer's rows, and never move a suggestion
onto another developer's row just because a name matches. A developer name that is also a place (Soma Bay,
El Gouna) is not a project and not a reason to pick another developer. Developers that only share starting prices
send one starting price per unit type: that is a normal update, not missing inventory.

confidence: high only when the chat text and the candidate leave no real doubt; otherwise medium or low.
reason: one short sentence a busy admin can read (English), naming the row or why it's noise.
Never invent rows: row_id must be one of the candidates given for that suggestion, or 0."""

SCHEMA = llm.obj({"decisions": {"type": "array", "items": llm.obj({
    "proposal_id": {"type": "integer"},
    "verdict": {"type": "string", "enum": ["update_existing", "new_project", "noise", "unsure"]},
    "row_id": {"type": "integer"},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "reason": llm.STR})}})


# ---------- candidate rows ----------
def _tokens(name):
    return {t for t in re.findall(r"\w+", cp.text(name).lower()) if len(t) > 1}


class Rows:
    """Usable sheet rows, indexed once per review."""

    def __init__(self, c):
        q = f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(master.USABLE))})"
        self.rows = c.execute(q, master.USABLE).fetchall()
        self.by_id = {r["id"]: r for r in self.rows}
        self.tok = {r["id"]: _tokens(r["project"]) | _tokens(r["developer"]) for r in self.rows}
        self.chat_rows = {}
        for r in c.execute(f"SELECT {CHAT_SQL} jid, p.row_id, COUNT(*) n FROM proposals p "
                           "WHERE p.row_id IS NOT NULL AND p.state IN ('approved','proposed') "
                           "GROUP BY 1, 2 ORDER BY n DESC"):
            if r["jid"] and r["row_id"] in self.by_id:
                self.chat_rows.setdefault(r["jid"], []).append(r["row_id"])

    def score(self, key, toks, dev_key, row):
        rk = row["project_key"] or ""
        sm = difflib.SequenceMatcher(None, key, rk)
        s = sm.ratio() if sm.real_quick_ratio() > 0.4 else 0.0
        rt = self.tok[row["id"]]
        if toks and rt:
            s = max(s, len(toks & rt) / len(toks | rt))
        if len(key) >= 4 and len(rk) >= 4 and (key in rk or rk in key):
            s = max(s, 0.8)
        rdev = cp.normalize_project_key(row["developer"])
        if dev_key and rdev and len(dev_key) >= 3 and (dev_key in rdev or rdev in dev_key):
            s += 0.35  # the same developer's rows are always worth showing, even when names look unrelated
        return s

    def candidates(self, project, developer, chat_jid, current_row_id, limit=8, chat_limit=6, dev_limit=15,
                   chat_developer="", assigned=()):
        key, toks, dev_key = cp.normalize_project_key(project), _tokens(project), cp.normalize_project_key(developer)
        scored = sorted(((self.score(key, toks, dev_key, r), r["id"]) for r in self.rows), reverse=True)
        ids = [rid for s, rid in scored[:limit] if s >= 0.3]
        ck = cp.normalize_project_key(chat_developer)
        if ck:  # every row of the chat's developer, best names first
            mine = [rid for s, rid in scored if master.same_developer(ck, self.by_id[rid])][:dev_limit]
            ids = mine + [i for i in ids if i not in mine]
        for rid in self.chat_rows.get(chat_jid, [])[:chat_limit]:  # rows this chat has updated before
            if rid not in ids:
                ids.append(rid)
        if assigned:  # the team's assignment for this group comes first
            ours = [rid for s, rid in scored if self.by_id[rid]["project_key"] in assigned]
            ids = ours + [i for i in ids if i not in ours]
        if current_row_id and current_row_id in self.by_id and current_row_id not in ids:
            ids.insert(0, current_row_id)
        return [self.by_id[i] for i in ids]


# ---------- rules ----------
def _price(parsed, field):
    if field == "ready_to_move":
        return parsed.get("rtm_price_egp")
    return parsed.get("price_egp")


def safe_to_approve(c, p, confidence, assigned=()):
    """Owner's auto-approve rules. Returns (ok, why_not). `assigned`: the group's team-assigned project keys."""
    if confidence != "high":
        return False, "not certain enough"
    if p["row_id"] is None:
        return False, "new rows always need a person"
    if assigned:
        row = c.execute("SELECT project_key FROM master_rows WHERE id=?", (p["row_id"],)).fetchone()
        if not row or row["project_key"] not in assigned:
            return False, "outside this group's assigned projects"
    if p["field"] not in AUTO_FIELDS:
        return False, f"{p['field'].replace('_', ' ')} changes always need a person"
    new = json.loads(p["new_parsed_json"] or "{}")
    if not new.get("ok"):
        return False, "value isn't in the sheet's format"
    if "Hold/Hide" in (p["warning"] or "") or new.get("status") in ("hold", "hide") or new.get("row_status"):
        return False, "Hold/Hide changes need a person"
    _, old = master.current(c, p["row_id"], p["field"])
    a, b = _price(old, p["field"]), _price(new, p["field"])
    if a and b and abs(b - a) / a > PRICE_BAND:
        return False, f"price moves {((b - a) / a) * 100:+.0f}%, more than ±{PRICE_BAND * 100:.0f}%"
    return True, ""


def _retarget(c, p, row):
    """Point a suggestion at an existing row. Returns the refreshed proposal, or None if the row already has it."""
    field = p["field"]
    sheet = master.home_sheet(row)
    if field not in ms.PROPOSABLE.get(ms.sheet_kind(sheet), []) and not (field == "feedback" and row["kind"] == ms.COMMISSION):
        return "wrong_sheet"
    old_raw, old = master.current(c, row["id"], field)
    new = json.loads(p["new_parsed_json"] or "{}")
    if ms.same_value(field, old, new):
        return None
    same_name = cp.normalize_project_key(row["project"]) == p["project_key"]
    warn = [w for w in (p["warning"] or "").split("; ") if w and "Near match" not in w and "choose the sheet" not in w]
    if not same_name:
        warn.insert(0, f"Near match: chat says '{p['project']}', sheet has '{row['project']}'")
    if (row["kind"] == ms.COMMISSION and master.current(c, row["id"], "date")[1].get("row_status")
            and not master.approval_date(c, row["id"]) and not any("Hold/Hide" in w for w in warn)):
        warn.append("Approving writes today's date and lifts Hold/Hide on Sheet 2")
    try:
        c.execute("UPDATE proposals SET row_id=?, sheet=?, old_raw=?, match_state=?, matched_label=?, warning=? "
                  "WHERE id=?", (row["id"], sheet, old_raw, "exact" if same_name else "near", row["project"],
                                 "; ".join(warn), p["id"]))
    except Exception:  # an identical suggestion already targets this row
        return None
    master._supersede(c, p["id"])
    return c.execute("SELECT * FROM proposals WHERE id=?", (p["id"],)).fetchone()


def _record(c, pid, d, action, model=MODEL):
    c.execute("INSERT OR REPLACE INTO brain_reviews VALUES(?,?,?,?,?,?,?,?)",
              (pid, d["verdict"], d.get("row_id") or None, d["confidence"], d["reason"], action, model, int(time.time())))


def _decide(pid, action, reason):
    master.decide_detail([pid], action)
    with db.connect() as c:
        c.execute("UPDATE proposals SET decided_by='brain' WHERE id=? AND state IN ('approved','rejected')", (pid,))


# ---------- review ----------
def _open_suggestions(c, jids, redo=False):
    seen = ("SELECT proposal_id FROM brain_reviews" if not redo else
            "SELECT proposal_id FROM brain_reviews WHERE action<>'kept'")  # redo: look again at what it left open
    q = (f"SELECT p.*, {CHAT_SQL} chat_jid, g.name chat_name FROM proposals p LEFT JOIN groups g ON g.jid={CHAT_SQL} "
         f"WHERE p.state='proposed' AND p.id NOT IN ({seen}) ORDER BY p.project_key, p.id")
    out = c.execute(q).fetchall()
    return [p for p in out if jids is None or p["chat_jid"] in jids]


def _group_payload(c, rows, key, items):
    first = items[0]
    prof = profiles.get(c, first["chat_jid"]) if first["chat_jid"] else None
    assigned = profiles.assigned_keys(c, first["chat_jid"])
    cands = rows.candidates(first["project"], first["developer"], first["chat_jid"], first["row_id"],
                            chat_developer=prof["developer"] if prof else "", assigned=assigned)
    fields = sorted({p["field"] for p in items})
    return {
        "group_id": key,
        "chat": first["chat_name"] or first["chat_jid"] or "",
        "chat_profile": profiles.describe(prof),
        "suggestions": [{"proposal_id": p["id"], "project_as_written": p["project"], "developer": p["developer"] or "",
                         "column": p["field"], "value": p["new_raw"], "quote": (p["quote"] or "")[:400],
                         "source": p["source_kind"],
                         "currently_matched_row": p["row_id"] or 0} for p in items],
        "candidates": [{"row_id": r["id"], "sheet": master.home_sheet(r), "developer": r["developer"] or "",
                        "project": r["project"], "location": r["location_code"] or "",
                        "current_values": {f: master.current(c, r["id"], f)[0] for f in fields}} for r in cands],
    }, ({r["id"] for r in cands}, assigned)


def review(log, jids=None, redo=False):
    """Second look at every open suggestion not reviewed yet (or only those from `jids`).
    Returns counts {reviewed, retargeted, approved, rejected, kept}."""
    totals = {"reviewed": 0, "retargeted": 0, "approved": 0, "rejected": 0, "kept": 0}
    with db.connect() as c:
        if not master.current_import(c):
            return totals
        todo = _open_suggestions(c, set(jids) if jids is not None else None, redo)
        if not todo:
            log("Brain: nothing new to review")
            return totals
        rows = Rows(c)
        groups = {}
        for p in todo:
            groups.setdefault((p["chat_jid"] or "", p["project_key"]), []).append(p)
        payloads = [_group_payload(c, rows, f"g{i}", items) for i, items in enumerate(groups.values())]
    log(f"Brain: reviewing {len(todo)} suggestion(s) in {len(payloads)} project group(s)")
    allowed = {}
    for payload, ids in payloads:
        for s in payload["suggestions"]:
            allowed[s["proposal_id"]] = ids
    chunks = [payloads[i:i + GROUPS_PER_REQUEST] for i in range(0, len(payloads), GROUPS_PER_REQUEST)]

    def ask(chunk):
        body = {"groups": [p for p, _ in chunk]}
        return llm.call_json(SYSTEM, json.dumps(body, ensure_ascii=False), SCHEMA, model=MODEL)["decisions"]

    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(ask, ch) for ch in chunks]
        for f in as_completed(futures):
            try:
                decisions = f.result()
            except Exception as e:
                log(f"Brain: a review request failed: {e}")
                continue
            for d in decisions:
                if d["proposal_id"] in allowed:
                    _apply(d, allowed[d["proposal_id"]], totals, log)
    log(f"Brain: {totals['approved']} approved, {totals['rejected']} rejected, {totals['retargeted']} moved to an "
        f"existing row, {totals['kept']} left for you")
    return totals


def _apply(d, allowed, totals, log):
    allowed_rows, assigned = allowed
    pid = d["proposal_id"]
    with db.connect() as c:
        p = c.execute("SELECT * FROM proposals WHERE id=? AND state='proposed'", (pid,)).fetchone()
        if not p:
            return
        totals["reviewed"] += 1
        verdict, conf = d["verdict"], d["confidence"]
        if verdict == "update_existing" and d["row_id"] in allowed_rows:
            row = c.execute("SELECT * FROM master_rows WHERE id=?", (d["row_id"],)).fetchone()
            if p["row_id"] != row["id"]:
                p2 = _retarget(c, p, row)
                if p2 is None:
                    _record(c, pid, d, "rejected_duplicate")
                    reject, why = True, "the sheet row already has this value"
                elif p2 == "wrong_sheet":
                    _record(c, pid, d, "kept")
                    totals["kept"] += 1
                    return
                else:
                    totals["retargeted"] += 1
                    p, reject, why = p2, False, ""
            else:
                reject, why = False, ""
            if not reject:
                ok, why_not = safe_to_approve(c, p, conf, assigned)
                _record(c, pid, dict(d, reason=d["reason"] if ok else f"{d['reason']} (left for you: {why_not})"),
                        "approved" if ok else "kept")
                action = "approve" if ok else None
            else:
                action = "reject"
        elif verdict == "noise" and conf == "high":
            _record(c, pid, d, "rejected")
            action = "reject"
        else:
            _record(c, pid, d, "kept")
            action = None
    if action:
        _decide(pid, action, d["reason"])
        totals["approved" if action == "approve" else "rejected"] += 1
    else:
        totals["kept"] += 1


def reopen(pid):
    """Undo a brain decision: the suggestion goes back on Updates. Returns False if a person decided it."""
    with db.connect() as c:
        p = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not p or p["decided_by"] != "brain" or p["state"] not in ("approved", "rejected"):
            return False
        c.execute("UPDATE proposals SET state='proposed', decided_at=NULL, decided_by=NULL WHERE id=?", (pid,))
        c.execute("UPDATE brain_reviews SET action='reopened' WHERE proposal_id=?", (pid,))
    return True
