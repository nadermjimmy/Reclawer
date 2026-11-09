"""Master 'App Updates' workbook: import, project matching, current values, proposals and approvals.

The uploaded workbook is the source of truth for "old" values. Chats only ever create proposals; a value
changes only when the owner approves it. Current value of a cell = latest approved proposal, else the
master cell.
"""
import datetime, difflib, hashlib, io, json, os, time
import openpyxl
import cell_parse as cp, db, master_schema as ms, normalize, profiles

USABLE = ("active", "moved", "new")  # row states that ingestion may match and update


# ---------- import ----------
def import_workbook(data, file_name):
    """Store the uploaded master and replace the previous one. Returns the summary dict."""
    sha = hashlib.sha256(data).hexdigest()
    os.makedirs(db.MASTER_DIR, exist_ok=True)
    path = os.path.join(db.MASTER_DIR, f"{sha}.xlsx")
    with open(path, "wb") as f:
        f.write(data)
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    now = int(time.time())
    summary = {"sheets": {}, "ignored_sheets": [], "unknown_headers": {}, "issues": 0, "legacy_duplicates": 0}
    with db.connect() as c:
        stamp_row_keys(c)  # labels keep a stable row identity before the old rows go
        for t in ("master_cells", "master_rows", "master_sheets", "master_issues"):
            c.execute(f"DELETE FROM {t}")
        # earlier proposals stay as history; pending ones can't be applied to the new master any more
        c.execute("UPDATE proposals SET state='superseded' WHERE state='proposed'")
        c.execute("UPDATE proposals SET row_id=NULL")
        imp = c.execute("INSERT INTO master_imports(file_name,sha256,path,imported_at) VALUES(?,?,?,?)",
                        (file_name, sha, path, now)).lastrowid
        for ws in wb.worksheets:
            kind = ms.sheet_kind(ws.title)
            if not kind:
                summary["ignored_sheets"].append(ws.title)
                continue
            sheet = ws.title.strip()
            cols = {}
            for cell in ws[1]:
                field = ms.field_of(cp.text(cell.value))
                if field and field not in cols:
                    cols[field] = cell.column
                elif cell.value not in (None, "") and not field:
                    summary["unknown_headers"].setdefault(sheet, []).append(cp.text(cell.value).strip())
            c.execute("INSERT INTO master_sheets VALUES(?,?,?,?,?)", (imp, sheet, ws.title, kind, json.dumps(cols)))
            n = 0
            for r in range(2, ws.max_row + 1):
                vals = {f: ws.cell(r, col).value for f, col in cols.items()}
                dev, proj = cp.text(vals.get("developer")).strip(), cp.text(vals.get("project")).strip()
                if not dev and not proj:
                    continue
                n += 1
                loc = ms.location_code(vals.get("location")) if kind == ms.GRID else ""
                rid = c.execute("INSERT INTO master_rows(import_id,sheet,kind,row_number,developer,project,"
                                "project_key,location_code,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                (imp, sheet, kind, r, dev, proj, cp.normalize_project_key(proj), loc, now)).lastrowid
                for f, v in vals.items():
                    if v is None or f in ("developer", "project"):
                        continue
                    c.execute("INSERT INTO master_cells VALUES(?,?,?,?)",
                              (rid, f, cp.text(v), json.dumps(ms.parse(f, v), ensure_ascii=False)))
            summary["sheets"][sheet] = n
        _location_checks(c, imp, summary)
        summary["assignments_missing"] = profiles.relink_projects(c)  # team assignments, re-checked by name
        summary["relinked"] = relink(c)  # human labels and brain reviews point at the new rows again
        c.execute("UPDATE master_imports SET summary_json=? WHERE id=?", (json.dumps(summary), imp))
        c.execute("INSERT OR REPLACE INTO meta VALUES('retrain_pending','1')")  # retrain.tick picks it up
    return summary


def _location_checks(c, imp, summary):
    """Decision 6: rows whose location doesn't belong to their sheet, or whose code is unknown -> review."""
    by_sheet = {}
    for r in c.execute("SELECT sheet, project_key FROM master_rows WHERE kind=?", (ms.GRID,)):
        by_sheet.setdefault(r["sheet"], set()).add(r["project_key"])
    for r in c.execute("SELECT * FROM master_rows WHERE kind=?", (ms.GRID,)).fetchall():
        code, home = r["location_code"], ms.sheet_for_location(r["location_code"])
        if not ms.known_location(code):
            reason = f"Location code '{code}' is not a known code" if code else "No location code"
            _issue(c, imp, r["id"], "unknown_location", reason)
        elif home != r["sheet"]:
            if r["project_key"] in by_sheet.get(home, set()):
                c.execute("UPDATE master_rows SET state='legacy_duplicate', note=? WHERE id=?",
                          (f"Also on {home}; that row is the current one", r["id"]))
                summary["legacy_duplicates"] += 1
            else:
                _issue(c, imp, r["id"], "legacy_location",
                       f"Location '{code}' ({ms.LOCATION_CODES[code]}) belongs on {home}, but the project is only on "
                       f"{r['sheet']}", target=home)
    summary["issues"] = c.execute("SELECT COUNT(*) FROM master_issues WHERE import_id=?", (imp,)).fetchone()[0]


def _issue(c, imp, row_id, kind, reason, target=None):
    c.execute("UPDATE master_rows SET state='needs_review', target_sheet=? WHERE id=?", (target, row_id))
    c.execute("INSERT INTO master_issues(import_id,row_id,issue_type,reason) VALUES(?,?,?,?)",
              (imp, row_id, kind, reason))


# ---------- stable row identity (survives re-uploads) ----------
def row_key_of(row):
    return normalize.row_key(row["developer"], row["project"], home_sheet(row))


def stamp_row_keys(c):
    """Give every proposal / brain review that points at a row its row_key. Rows that lost their row_id earlier
    (re-uploads before keys existed) get one from the sheet's project name they were matched to."""
    for t, col in (("proposals", "row_id"), ("brain_reviews", "row_id")):
        for r in c.execute(f"SELECT x.rowid rid, m.* FROM {t} x JOIN master_rows m ON m.id=x.{col} "
                           "WHERE x.row_key IS NULL").fetchall():
            c.execute(f"UPDATE {t} SET row_key=? WHERE rowid=?", (row_key_of(r), r["rid"]))
    for p in c.execute("SELECT id, developer, matched_label, sheet FROM proposals WHERE row_key IS NULL "
                       "AND row_id IS NULL AND state IN ('approved','rejected') AND matched_label<>''").fetchall():
        c.execute("UPDATE proposals SET row_key=? WHERE id=?",
                  (normalize.row_key(p["developer"], p["matched_label"], p["sheet"]), p["id"]))


def relink(c):
    """After an import: point labels at the new rows by row_key. Returns counts {linked, unlinked}.
    Proposals get `label_row_id`, never `row_id`: an approval made against the old master must not be replayed on
    top of the new upload (the team may have changed that cell since); `row_id` stays NULL as before."""
    keys = {}
    for r in c.execute(f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(USABLE))})", USABLE):
        keys.setdefault(row_key_of(r), r["id"])
    linked = unlinked = 0
    for t in ("proposals", "brain_reviews"):
        for x in c.execute(f"SELECT rowid rid, row_key FROM {t} WHERE row_key IS NOT NULL").fetchall():
            rid = keys.get(x["row_key"])
            col = "label_row_id" if t == "proposals" else "row_id"
            if rid:
                c.execute(f"UPDATE {t} SET {col}=? WHERE rowid=?", (rid, x["rid"]))
                linked += t == "proposals"
            else:
                unlinked += t == "proposals"
    return {"linked": linked, "unlinked": unlinked}


def resolve_issue(issue_id, action, sheet=""):
    """action: keep (stays on its sheet) / move (to `sheet`) / drop (ignored by matching and export)."""
    with db.connect() as c:
        i = c.execute("SELECT * FROM master_issues WHERE id=?", (issue_id,)).fetchone()
        if not i:
            return
        if action == "keep":
            c.execute("UPDATE master_rows SET state='active', target_sheet=NULL WHERE id=?", (i["row_id"],))
        elif action == "move" and sheet in ms.GRID_SHEETS:
            c.execute("UPDATE master_rows SET state='moved', target_sheet=? WHERE id=?", (sheet, i["row_id"]))
        elif action == "drop":
            c.execute("UPDATE master_rows SET state='dropped' WHERE id=?", (i["row_id"],))
        else:
            return
        c.execute("UPDATE master_issues SET state=?, decided_at=? WHERE id=?", (action, int(time.time()), issue_id))


def current_import(c):
    return c.execute("SELECT * FROM master_imports ORDER BY id DESC LIMIT 1").fetchone()


def home_sheet(row):
    return row["target_sheet"] if row["state"] == "moved" else row["sheet"]


# ---------- current values ----------
def current(c, row_id, field):
    """(raw_text, parsed) for one cell after approved changes."""
    p = c.execute("SELECT new_raw, new_parsed_json FROM proposals WHERE row_id=? AND field=? AND state='approved' "
                  "ORDER BY decided_at DESC, id DESC LIMIT 1", (row_id, field)).fetchone()
    if p:
        return p["new_raw"], json.loads(p["new_parsed_json"])
    m = c.execute("SELECT raw_text, parsed_json FROM master_cells WHERE row_id=? AND field=?", (row_id, field)).fetchone()
    if m:
        return m["raw_text"], json.loads(m["parsed_json"])
    return "", ms.parse(field, None)


def approval_date(c, row_id):
    ts = c.execute("SELECT MAX(decided_at) FROM proposals WHERE row_id=? AND state='approved'", (row_id,)).fetchone()[0]
    return datetime.date.fromtimestamp(ts) if ts else None


def row_values(c, row):
    """{field: (raw, parsed)} for every known field of a row, after approvals."""
    fields = {r["field"] for r in c.execute("SELECT field FROM master_cells WHERE row_id=?", (row["id"],))}
    fields |= {r["field"] for r in c.execute("SELECT DISTINCT field FROM proposals WHERE row_id=? AND state='approved'",
                                             (row["id"],))}
    return {f: current(c, row["id"], f) for f in fields}


# ---------- matching (decision 13) ----------
def same_developer(dev_key, row):
    rk = cp.normalize_project_key(row["developer"])
    return bool(dev_key and rk and len(dev_key) >= 3 and (dev_key in rk or rk in dev_key))


def _loose(key, cands, floor=0.6):
    """Best row among cands by name, allowing prefix/containment ('Arc' -> 'Arc Of Soma')."""
    best, score = None, 0.0
    for r in cands:
        pk = r["project_key"]
        s = difflib.SequenceMatcher(None, key, pk).ratio()
        if len(key) >= 3 and len(pk) >= 3 and (pk.startswith(key) or key.startswith(pk) or key in pk or pk in key):
            s = max(s, 0.8)
        if s > score:
            best, score = r, s
    return best if best is not None and score >= floor else None


MATCHER = os.getenv("MATCHER", "legacy")  # legacy (default) / v2 / shadow (legacy decides, disagreements logged)


def match(c, project, developer, sheets, assigned=None, alias_before=None, chat_jid=None, field="",
          source_kind="message", location="", mode=None):
    """{sheet: (row, match_state, label)}. MATCHER picks the implementation (see matcher.py)."""
    mode = mode or MATCHER
    if mode == "legacy":
        return match_legacy(c, project, developer, sheets, assigned, alias_before)
    import matcher
    v2 = matcher.decide(c, project, developer, sheets, assigned, chat_jid, field, source_kind, location, alias_before)
    if mode == "v2":
        return {s: t[:3] for s, t in v2.items()}
    legacy = match_legacy(c, project, developer, sheets, assigned, alias_before)
    if not alias_before:
        _log_shadow(c, project, developer, legacy, v2)
    return legacy


def _log_shadow(c, project, developer, legacy, v2):
    for sheet in set(legacy) | set(v2):
        a, b = legacy.get(sheet), v2.get(sheet)
        ka, kb = (row_key_of(a[0]) if a else None), (row_key_of(b[0]) if b else None)
        if ka != kb:
            c.execute("INSERT INTO matcher_shadow(ts,project,developer,sheet,legacy_key,legacy_state,v2_key,v2_state,"
                      "v2_p) VALUES(?,?,?,?,?,?,?,?,?)", (int(time.time()), project, developer or "", sheet, ka,
                                                          a[1] if a else None, kb, b[1] if b else None,
                                                          b[3] if b else None))


def match_legacy(c, project, developer, sheets, assigned=None, alias_before=None):
    # alias_before (eval replay): only aliases and approval-created rows that existed before that time
    """{sheet: (row, match_state, label)} for the best row per sheet. match_state: exact / alias / near.
    When the developer is known (from the message or the chat's developer profile) that developer's rows are tried
    first, with a looser name match ('Arc' -> 'Arc Of Soma'); another developer's same-named project only counts
    as a near match, so a person or the brain confirms it."""
    key = cp.normalize_project_key(project)
    if not key:
        return {}
    alias = c.execute("SELECT project_key FROM master_aliases WHERE alias_key=?" +
                      (" AND confirmed_at<?" if alias_before else ""),
                      (key, alias_before) if alias_before else (key,)).fetchone()  # eval: aliases known back then
    dev_key = cp.normalize_project_key(developer)
    q = (f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(USABLE))})")
    rows = [r for r in c.execute(q, USABLE).fetchall() if home_sheet(r) in sheets
            and not (alias_before and r["state"] == "new" and (r["created_at"] or 0) >= alias_before)]
    out = {}
    for sheet in sheets:
        cands = [r for r in rows if home_sheet(r) == sheet]
        mine = [r for r in cands if same_developer(dev_key, r)]
        hit = None
        ours = [r for r in cands if assigned and r["project_key"] in assigned]  # the team's assignment first
        if ours:
            for state, keyset in (("exact", {key}), ("alias", {alias[0]} if alias else set())):
                hits = [r for r in ours if r["project_key"] in keyset]
                if hits:
                    hit = (hits[0], state)
                    break
            if not hit:
                best = _loose(key, ours)
                if best is not None:
                    hit = (best, "near")
            if hit:
                out[sheet] = (hit[0], hit[1], hit[0]["project"])
                continue
        for state, keyset in (("exact", {key}), ("alias", {alias[0]} if alias else set())):
            hits = [r for r in mine if r["project_key"] in keyset]
            if hits:
                hit = (hits[0], state)
                break
        if not hit and mine:  # the developer's own rows, looser
            best, score = None, 0.0
            for r in mine:
                pk = r["project_key"]
                s = difflib.SequenceMatcher(None, key, pk).ratio()
                if len(key) >= 3 and len(pk) >= 3 and (pk.startswith(key) or key.startswith(pk) or key in pk or pk in key):
                    s = max(s, 0.8)
                if s > score:
                    best, score = r, s
            if best is not None and score >= 0.6:
                hit = (best, "near")
        if not hit:
            for state, keyset in (("exact", {key}), ("alias", {alias[0]} if alias else set())):
                hits = [r for r in cands if r["project_key"] in keyset]
                if hits:
                    # same name, other developer: only a near match when we know whose chat this is
                    hit = (hits[0], "near" if dev_key and mine else state)
                    break
        if not hit:
            best, score = None, 0.0
            for r in cands:
                pk = r["project_key"]
                s = difflib.SequenceMatcher(None, key, pk).ratio()
                if len(key) >= 5 and len(pk) >= 5 and (key in pk or pk in key):
                    s = max(s, 0.86)
                if s > score:
                    best, score = r, s
            if best is not None and score >= 0.85:
                hit = (best, "near")
        if hit:
            out[sheet] = (hit[0], hit[1], hit[0]["project"])
    return out


# ---------- proposals ----------
def _sheets_for(field):
    kinds = [k for k, fs in ms.PROPOSABLE.items() if field in fs]
    return [s for s, k in ms.SHEETS.items() if k in kinds]


OUTSIDE_ASSIGNMENT = "Outside this group's assigned projects"

# reading order (owner decision): text messages first, then spreadsheets, PDFs, images. A later source only fills a
# cell that no earlier source filled recently: the cell is empty, or nothing better updated it in the last 14 days.
SOURCE_RANK = {"message": 0, "row": 1, "pdf_page": 2, "image": 3}
FRESH_DAYS = 14


def _filled_by_better(c, row, sheet, key, field, kind):
    rank = SOURCE_RANK.get(kind, 0)
    better = [k for k, r in SOURCE_RANK.items() if r < rank]
    if not better:
        return False
    since = int(time.time()) - FRESH_DAYS * 86400
    marks = ",".join("?" * len(better))
    if row is not None:
        where, args = "row_id=? AND field=?", [row["id"], field]
    else:
        where, args = "row_id IS NULL AND sheet=? AND project_key=? AND field=?", [sheet, key, field]
    return c.execute(f"SELECT 1 FROM proposals WHERE {where} AND state IN ('proposed','approved') "
                     f"AND source_kind IN ({marks}) AND COALESCE(source_ts,0)>=? LIMIT 1",
                     args + better + [since]).fetchone() is not None


def add_proposal(c, item):
    """item: developer, project, location_code, field, value, quote, source_kind, source_ref, source_ts.
    Writes one proposal per matched sheet row (or one 'new project' proposal). Returns how many were written.
    Files (rows, PDF pages, images) only fill gaps: see SOURCE_RANK."""
    field = item["field"]
    if field not in ms.ALL_PROPOSABLE or field in ms.NEVER or not item.get("project", "").strip():
        return 0
    parsed = ms.round_prices(field, ms.parse(field, item["value"]))  # the team's rounding rule
    if not parsed.get("raw"):
        return 0
    new_raw = ms.fmt(field, parsed) if parsed.get("ok") else parsed["raw"]
    project, key = item["project"].strip(), cp.normalize_project_key(item["project"])
    loc = (item.get("location_code") or "").strip().lower()
    sheets = _sheets_for(field)
    assigned = profiles.assigned_keys(c, item.get("chat_jid"))
    hits = match(c, project, item.get("developer", ""), sheets, assigned, chat_jid=item.get("chat_jid"), field=field,
                 source_kind=item["source_kind"], location=loc)
    written = 0
    targets = []
    for sheet, (row, state, label) in hits.items():
        targets.append((sheet, row, state, label))
    grid_sheets = [s for s in sheets if ms.sheet_kind(s) == ms.GRID]
    if grid_sheets and not any(ms.sheet_kind(t[0]) == ms.GRID for t in targets):
        targets.append((ms.sheet_for_location(loc) or "", None, "new", ""))  # new project on a price grid
    if "Sheet 2" in sheets and "Sheet 2" not in hits and not grid_sheets:
        targets.append(("Sheet 2", None, "new", ""))  # commission / feedback for a project not on Sheet 2
    for sheet, row, state, label in targets:
        if _filled_by_better(c, row, sheet, key, field, item["source_kind"]):
            continue  # an earlier source in the reading order already holds a recent value for this cell
        warn = []
        old_raw = ""
        if row is not None:
            newer = c.execute("SELECT 1 FROM proposals WHERE row_id=? AND field=? AND state='approved' "
                              "AND COALESCE(source_ts,0)>?", (row["id"], field, item.get("source_ts") or 0)).fetchone()
            if newer:
                continue  # an approved value from a newer source already holds this cell
            old_raw, old = current(c, row["id"], field)
            if ms.same_value(field, old, parsed):
                continue
            if (row["kind"] == ms.COMMISSION and current(c, row["id"], "date")[1].get("row_status")
                    and not approval_date(c, row["id"])):
                warn.append("Approving writes today's date and lifts Hold/Hide on Sheet 2")
        if assigned and row is not None and row["project_key"] not in assigned:
            state = "near"  # outside the team's assignment for this group: a person (or the brain) confirms it
            warn.append(OUTSIDE_ASSIGNMENT)
        if state == "near":
            warn.append(f"Near match: chat says '{project}', sheet has '{label}'")
        if state == "new" and not sheet:
            warn.append("New project with no known location: choose the sheet when approving")
        if not parsed.get("ok"):
            warn.append("Value doesn't follow the sheet format; check before approving")
        rkey = row_key_of(row) if row is not None else normalize.row_key(item.get("developer", ""), project, sheet)
        cur = c.execute("INSERT OR IGNORE INTO proposals(row_id,sheet,developer,project,project_key,field,old_raw,"
                        "new_raw,new_parsed_json,quote,source_kind,source_ref,source_ts,match_state,matched_label,"
                        "location_code,warning,created_at,row_key,first_row_key,first_match_state) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["id"] if row is not None else None, sheet, item.get("developer", "").strip(), project,
                         key, field, old_raw, new_raw, json.dumps(parsed, ensure_ascii=False), item.get("quote", ""),
                         item["source_kind"], item["source_ref"], item.get("source_ts"), state, label, loc,
                         "; ".join(warn), int(time.time()), rkey, rkey, state))
        if cur.rowcount:
            written += 1
            _supersede(c, cur.lastrowid)
    return written


def _same_target(p):
    if p["row_id"] is not None:
        return "row_id=? AND field=?", (p["row_id"], p["field"])
    return "row_id IS NULL AND sheet=? AND project_key=? AND field=?", (p["sheet"], p["project_key"], p["field"])


def _supersede(c, pid):
    """Latest quoted wins (decision 3): only the newest pending proposal per cell stays open."""
    p = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    where, args = _same_target(p)
    pend = c.execute(f"SELECT id, source_ts FROM proposals WHERE state='proposed' AND {where} "
                     "ORDER BY COALESCE(source_ts,0) DESC, id DESC", args).fetchall()
    for old in pend[1:]:
        c.execute("UPDATE proposals SET state='superseded' WHERE id=?", (old["id"],))


WARNING_CODES = ("needs_sheet", "near_match", "lifts_hold", "bad_format", "new_project")


def warnings_of(p):
    """Structured warnings for one proposal row: [(code, text)]. Derived from the stored row, so the
    `warning` text written by add_proposal stays as it is. Codes: needs_sheet (blocks approval until a sheet
    is chosen), near_match, lifts_hold, bad_format, new_project (informational)."""
    texts = [t.strip() for t in (p["warning"] or "").split(";") if t.strip()]
    find = lambda word: next((t for t in texts if word in t), "")
    out = []
    if p["match_state"] == "new" and p["row_id"] is None and not p["sheet"]:
        out.append(("needs_sheet", find("choose the sheet") or "New project with no known location: choose the sheet"))
    if p["match_state"] == "near":
        out.append(("near_match", find("Near match") or
                    f"Near match: chat says '{p['project']}', sheet has '{p['matched_label']}'"))
    if find("Hold/Hide"):
        out.append(("lifts_hold", find("Hold/Hide")))
    parsed = json.loads(p["new_parsed_json"] or "{}")
    if find("sheet format") or (p["new_parsed_json"] and not parsed.get("ok")):
        out.append(("bad_format", find("sheet format") or "Value doesn't follow the sheet format"))
    if p["match_state"] == "new" and p["sheet"]:
        out.append(("new_project", f"New row on {p['sheet']}"))
    return out


def warning_codes(p):
    return [code for code, _ in warnings_of(p)]


def decide(ids, action, sheet=""):
    """Approve or reject proposals. `sheet` is one sheet for every new project, or {id: sheet} per proposal.
    Returns (done, skipped_reasons)."""
    done, skipped = decide_detail(ids, action, sheet)
    return len(done), [s["reason"] for s in skipped if s["state"] == "proposed"]


REJECT_REASONS = ("wrong_project", "wrong_value", "not_an_offer", "duplicate")


def decide_detail(ids, action, sheet="", actor="person", reason=None):
    """Like decide, but returns ([done ids], [{id, reason, state}]). A proposal that is no longer open (decided
    elsewhere, or superseded by an approval earlier in the same batch) is skipped with its current state.
    actor: 'person' or 'brain' (written to decided_by). reason: a REJECT_REASONS value for a rejection."""
    done, skipped = [], []
    now = int(time.time())
    with db.connect() as c:
        for pid in ids:
            p = c.execute("SELECT * FROM proposals WHERE id=? AND state='proposed'", (pid,)).fetchone()
            if not p:
                st = c.execute("SELECT state FROM proposals WHERE id=?", (pid,)).fetchone()
                skipped.append({"id": pid, "reason": f"already {st[0]}" if st else "not found",
                                "state": st[0] if st else None})
                continue
            by = "brain" if actor == "brain" else None
            if action == "reject":
                c.execute("UPDATE proposals SET state='rejected', decided_at=?, decided_by=?, reject_reason=? "
                          "WHERE id=?", (now, by, reason if reason in REJECT_REASONS else None, pid))
                if by is None:
                    _learn(c, pid, "rejected")
                done.append(pid)
                continue
            row_id = p["row_id"]
            if row_id is None:
                chosen = sheet.get(pid, "") if isinstance(sheet, dict) else sheet
                target = p["sheet"] or chosen
                if not target or target not in ms.SHEETS:
                    skipped.append({"id": pid, "reason": f"{p['project']}: choose a sheet for the new project",
                                    "state": "proposed"})
                    continue
                row_id = _new_row(c, target, p, now)
            elif p["match_state"] == "near":
                row = c.execute("SELECT project_key, developer FROM master_rows WHERE id=?", (row_id,)).fetchone()
                c.execute("INSERT OR REPLACE INTO master_aliases(alias_key,project_key,label,confirmed_at,developer_key) "
                          "VALUES(?,?,?,?,?)", (p["project_key"], row["project_key"], p["project"], now,
                                                normalize.compact(row["developer"])))
                c.execute("INSERT OR REPLACE INTO dev_aliases(alias_key,developer_key,project_key,label,confirmed_at) "
                          "VALUES(?,?,?,?,?)", (p["project_key"], normalize.compact(row["developer"]),
                                                row["project_key"], p["project"], now))
            final = c.execute("SELECT * FROM master_rows WHERE id=?", (row_id,)).fetchone()
            c.execute("UPDATE proposals SET state='approved', decided_at=?, row_id=?, decided_by=?, row_key=? "
                      "WHERE id=?", (now, row_id, by, row_key_of(final) if final else p["row_key"], pid))
            if by is None:
                _learn(c, pid, "approved")
            # older open proposals for the same cell are outdated now
            c.execute("UPDATE proposals SET state='superseded' WHERE state='proposed' AND row_id=? AND field=? "
                      "AND COALESCE(source_ts,0)<=?", (row_id, p["field"], p["source_ts"] or 0))
            done.append(pid)
    return done, skipped


def _learn(c, pid, decision):
    """A person decided: keep it as a learning example (memory.py). Never blocks the decision."""
    try:
        import memory
        memory.record(c, pid, decision)
    except Exception:
        pass


def _new_row(c, sheet, p, now):
    r = c.execute("SELECT id FROM master_rows WHERE state='new' AND sheet=? AND project_key=?",
                  (sheet, p["project_key"])).fetchone()
    if r:
        return r["id"]
    imp = current_import(c)
    rid = c.execute("INSERT INTO master_rows(import_id,sheet,kind,row_number,developer,project,project_key,"
                    "location_code,state,created_at) VALUES(?,?,?,?,?,?,?,?,'new',?)",
                    (imp["id"] if imp else None, sheet, ms.sheet_kind(sheet), None, p["developer"], p["project"],
                     p["project_key"], p["location_code"], now)).lastrowid
    if p["location_code"] and ms.sheet_kind(sheet) == ms.GRID:
        c.execute("INSERT INTO master_cells VALUES(?,?,?,?)", (rid, "location", p["location_code"].upper(),
                                                              json.dumps(ms.parse("location", p["location_code"].upper()))))
    # other open proposals for the same new project now point at this row
    c.execute("UPDATE proposals SET row_id=? WHERE row_id IS NULL AND state='proposed' AND project_key=? "
              "AND (sheet=? OR sheet='')", (rid, p["project_key"], sheet))
    return rid
