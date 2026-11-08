"""Master 'App Updates' workbook: import, project matching, current values, proposals and approvals.

The uploaded workbook is the source of truth for "old" values. Chats only ever create proposals; a value
changes only when the owner approves it. Current value of a cell = latest approved proposal, else the
master cell.
"""
import datetime, difflib, hashlib, io, json, os, time
import openpyxl
import cell_parse as cp, db, master_schema as ms

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
        c.execute("UPDATE master_imports SET summary_json=? WHERE id=?", (json.dumps(summary), imp))
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
def match(c, project, developer, sheets):
    """{sheet: (row, match_state, label)} for the best row per sheet. match_state: exact / alias / near."""
    key = cp.normalize_project_key(project)
    if not key:
        return {}
    alias = c.execute("SELECT project_key FROM master_aliases WHERE alias_key=?", (key,)).fetchone()
    dev_key = cp.normalize_project_key(developer)
    q = (f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(USABLE))})")
    rows = [r for r in c.execute(q, USABLE).fetchall() if home_sheet(r) in sheets]
    out = {}
    for sheet in sheets:
        cands = [r for r in rows if home_sheet(r) == sheet]
        for state, keyset in (("exact", {key}), ("alias", {alias[0]} if alias else set())):
            hits = [r for r in cands if r["project_key"] in keyset]
            if len(hits) > 1 and dev_key:
                hits = [r for r in hits if dev_key in cp.normalize_project_key(r["developer"])] or hits
            if hits:
                out[sheet] = (hits[0], state, hits[0]["project"])
                break
        else:
            best, score = None, 0.0
            for r in cands:
                pk = r["project_key"]
                s = difflib.SequenceMatcher(None, key, pk).ratio()
                if len(key) >= 5 and len(pk) >= 5 and (key in pk or pk in key):
                    s = max(s, 0.86)
                if s > score:
                    best, score = r, s
            if best is not None and score >= 0.85:
                out[sheet] = (best, "near", best["project"])
    return out


# ---------- proposals ----------
def _sheets_for(field):
    kinds = [k for k, fs in ms.PROPOSABLE.items() if field in fs]
    return [s for s, k in ms.SHEETS.items() if k in kinds]


def add_proposal(c, item):
    """item: developer, project, location_code, field, value, quote, source_kind, source_ref, source_ts.
    Writes one proposal per matched sheet row (or one 'new project' proposal). Returns how many were written."""
    field = item["field"]
    if field not in ms.ALL_PROPOSABLE or field in ms.NEVER or not item.get("project", "").strip():
        return 0
    parsed = ms.parse(field, item["value"])
    if not parsed.get("raw"):
        return 0
    new_raw = ms.fmt(field, parsed) if parsed.get("ok") else parsed["raw"]
    project, key = item["project"].strip(), cp.normalize_project_key(item["project"])
    loc = (item.get("location_code") or "").strip().lower()
    sheets = _sheets_for(field)
    hits = match(c, project, item.get("developer", ""), sheets)
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
        if state == "near":
            warn.append(f"Near match: chat says '{project}', sheet has '{label}'")
        if state == "new" and not sheet:
            warn.append("New project with no known location: choose the sheet when approving")
        if not parsed.get("ok"):
            warn.append("Value doesn't follow the sheet format; check before approving")
        cur = c.execute("INSERT OR IGNORE INTO proposals(row_id,sheet,developer,project,project_key,field,old_raw,"
                        "new_raw,new_parsed_json,quote,source_kind,source_ref,source_ts,match_state,matched_label,"
                        "location_code,warning,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["id"] if row is not None else None, sheet, item.get("developer", "").strip(), project,
                         key, field, old_raw, new_raw, json.dumps(parsed, ensure_ascii=False), item.get("quote", ""),
                         item["source_kind"], item["source_ref"], item.get("source_ts"), state, label, loc,
                         "; ".join(warn), int(time.time())))
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


def decide(ids, action, sheet=""):
    """Approve or reject proposals. Returns (done, skipped_reasons)."""
    done, skipped = 0, []
    now = int(time.time())
    with db.connect() as c:
        for pid in ids:
            p = c.execute("SELECT * FROM proposals WHERE id=? AND state='proposed'", (pid,)).fetchone()
            if not p:
                continue
            if action == "reject":
                c.execute("UPDATE proposals SET state='rejected', decided_at=? WHERE id=?", (now, pid))
                done += 1
                continue
            row_id = p["row_id"]
            if row_id is None:
                target = p["sheet"] or sheet
                if not target or target not in ms.SHEETS:
                    skipped.append(f"{p['project']}: choose a sheet for the new project")
                    continue
                row_id = _new_row(c, target, p, now)
            elif p["match_state"] == "near":
                row = c.execute("SELECT project_key FROM master_rows WHERE id=?", (row_id,)).fetchone()
                c.execute("INSERT OR REPLACE INTO master_aliases VALUES(?,?,?,?)",
                          (p["project_key"], row["project_key"], p["project"], now))
            c.execute("UPDATE proposals SET state='approved', decided_at=?, row_id=? WHERE id=?", (now, row_id, pid))
            # older open proposals for the same cell are outdated now
            c.execute("UPDATE proposals SET state='superseded' WHERE state='proposed' AND row_id=? AND field=? "
                      "AND COALESCE(source_ts,0)<=?", (row_id, p["field"], p["source_ts"] or 0))
            done += 1
    return done, skipped


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
