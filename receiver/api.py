"""JSON API for the new admin app (/app). Same cookie session as the classic pages (web.py).

Unauthenticated /api/* calls get 401 {"error": "login"} from web.auth_middleware. Errors share one shape:
{"error": code, "detail"?: ...}; 409 {"error": "busy", "job": {...}} means another job is running.
"""
import datetime, json, os, re, time
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
import build, db, history, jobs, master, master_export, master_schema as ms, parse_files, proposals, rules, web

router = APIRouter(prefix="/api")
spa_router = APIRouter()

MARKET = {"name": rules.MARKET_NAME, "short": rules.MARKET_SHORT, "currency": rules.CURRENCY,
          "metric": rules.METRIC_LABEL}
STATES = ("proposed", "approved", "rejected", "superseded")


def err(code, status, detail=None, **extra):
    body = {"error": code, **extra}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(body, status)


async def body_of(request):
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---------- session & shell ----------
@router.post("/login")
async def login(request: Request):
    data = await body_of(request)
    if not web.password_ok(str(data.get("password", ""))):
        time.sleep(1)
        return err("bad_password", 401)
    return web.set_session(JSONResponse({"ok": True}))


@router.post("/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(web.COOKIE)
    return r


def running_job(c):
    if not jobs.running():
        return None
    j = c.execute("SELECT id, name, status, started_at FROM jobs WHERE status='running' ORDER BY id DESC LIMIT 1"
                  ).fetchone()
    return dict(j) if j else {"id": None, "name": "A job", "status": "running", "started_at": None}


def busy(c):
    return err("busy", 409, job=running_job(c))


@router.get("/me")
@router.get("/status")
def me():
    with db.connect() as c:
        imp = master.current_import(c)
        open_jids = {r["chat_jid"] for r in c.execute(
            f"SELECT {CHAT_SQL} chat_jid FROM proposals p WHERE p.state='proposed'") if r["chat_jid"]}
        counts = {
            "open_suggestions": c.execute("SELECT COUNT(*) FROM proposals WHERE state='proposed'").fetchone()[0],
            "rows_to_place": c.execute("SELECT COUNT(*) FROM master_issues WHERE state='open'").fetchone()[0],
            "open_issues": c.execute("SELECT COUNT(*) FROM review_issues WHERE severity IN ('high','medium')"
                                     ).fetchone()[0],
            "failed_downloads": c.execute("SELECT COUNT(*) FROM media_fetch WHERE status='failed'").fetchone()[0],
            "chats_with_suggestions": len(open_jids),
        }
        last_sync = c.execute("SELECT MAX(finished_at) FROM jobs WHERE status='done'").fetchone()[0]
        return {"market": MARKET, "has_master": imp is not None,
                "master": {"file_name": imp["file_name"], "imported_at": imp["imported_at"]} if imp else None,
                "job": running_job(c), "counts": counts, "last_job_finished_at": last_sync}


# ---------- suggestions (Updates) ----------
# chat a proposal came from: the message's chat, or for a file row / PDF page the latest chat that posted the file
CHAT_SQL = ("(CASE WHEN p.source_kind='message' THEN (SELECT sm.group_jid FROM source_messages sm "
            "WHERE sm.wa_id=p.source_ref) ELSE (SELECT sm.group_jid FROM source_rows sr "
            "JOIN file_occurrences fo ON fo.file_id=sr.file_id JOIN source_messages sm ON sm.wa_id=fo.wa_id "
            "WHERE sr.id=CAST(p.source_ref AS INTEGER) ORDER BY sm.ts DESC LIMIT 1) END)")
FILE_SQL = ("(CASE WHEN p.source_kind='message' THEN NULL ELSE (SELECT sf.file_name FROM source_rows sr "
            "JOIN source_files sf ON sf.id=sr.file_id WHERE sr.id=CAST(p.source_ref AS INTEGER)) END)")
PROPOSAL_SQL = (f"SELECT x.*, g.name chat_name FROM (SELECT p.*, r.project row_project, r.developer row_developer, "
                f"r.row_number, {CHAT_SQL} chat_jid, {FILE_SQL} source_file FROM proposals p "
                f"LEFT JOIN master_rows r ON r.id=p.row_id) x LEFT JOIN groups g ON g.jid=x.chat_jid")


def field_label(field):
    return ms.UNIT_LABELS.get(field) or (field or "").replace("_", " ").title()


def delta(p):
    """Parsed change for unit cells: price % and area from/to (neutral numbers; the UI adds no judgement)."""
    if p["field"] not in ms.UNIT_FIELDS or not p["old_raw"]:
        return None
    old, new = ms.parse(p["field"], p["old_raw"]), json.loads(p["new_parsed_json"] or "{}")
    if not (old.get("ok") and new.get("ok")):
        return None
    out = {}
    if old.get("price_egp") and new.get("price_egp"):
        out.update(price_from=old["price_egp"], price_to=new["price_egp"],
                   price_pct=round((new["price_egp"] - old["price_egp"]) * 100.0 / old["price_egp"], 1))
    if old.get("area_sqm") and new.get("area_sqm") and old["area_sqm"] != new["area_sqm"]:
        out.update(area_from=old["area_sqm"], area_to=new["area_sqm"])
    if old.get("status") != new.get("status"):
        out.update(status_from=old.get("status"), status_to=new.get("status"))
    return out or None


def item(p):
    warns = master.warnings_of(p)
    return {
        "id": p["id"], "state": p["state"], "sheet": p["sheet"] or "", "row_id": p["row_id"],
        "row_number": p["row_number"], "project": p["project"], "row_project": p["row_project"],
        "developer": p["row_developer"] or p["developer"] or "", "field": p["field"],
        "field_label": field_label(p["field"]), "old_raw": p["old_raw"] or "", "new_raw": p["new_raw"] or "",
        "new_parsed": json.loads(p["new_parsed_json"] or "{}"), "delta": delta(p), "quote": p["quote"] or "",
        "source": {"kind": p["source_kind"], "ref": p["source_ref"], "ts": p["source_ts"], "chat_jid": p["chat_jid"],
                   "chat_name": p["chat_name"] or p["chat_jid"], "file_name": p["source_file"]},
        "match_state": p["match_state"], "matched_label": p["matched_label"] or "",
        "location_code": p["location_code"] or "", "warning": p["warning"] or "",
        "warnings": [{"code": code, "text": text} for code, text in warns],
        "warning_codes": [code for code, _ in warns],
        "created_at": p["created_at"], "decided_at": p["decided_at"],
    }


def state_counts(c):
    got = {r["state"]: r["n"] for r in c.execute("SELECT state, COUNT(*) n FROM proposals GROUP BY state")}
    return {s: got.get(s, 0) for s in STATES}


@router.get("/proposals")
def list_proposals(state: str = "proposed", sheet: str = "", match: str = "", q: str = "", chat: str = "",
                   warning: str = "", sort: str = "project", cursor: str = "0", limit: int = 200):
    """Filters: state, sheet, match (exact/alias/near/new), q (project/developer/sheet name), chat (jid),
    warning (comma list of codes; 'any' = has warnings). sort: project (default) / newest / warnings."""
    where, args = ["x.state=?"], [state if state in STATES else "proposed"]
    if sheet:
        where.append("x.sheet=?")
        args.append(sheet)
    if match:
        where.append("x.match_state=?")
        args.append(match)
    if q:
        where.append("(x.project LIKE ? OR x.developer LIKE ? OR x.matched_label LIKE ? OR x.row_project LIKE ?)")
        args += [f"%{q}%"] * 4
    if chat:
        where.append("x.chat_jid=?")
        args.append(chat)
    order = {"newest": "COALESCE(x.source_ts,0) DESC, x.id DESC"}.get(
        sort, "x.project_key, x.sheet, x.field, COALESCE(x.source_ts,0) DESC")
    with db.connect() as c:
        found = [item(p) for p in c.execute(f"SELECT * FROM ({PROPOSAL_SQL}) x WHERE {' AND '.join(where)} "
                                            f"ORDER BY {order}", args).fetchall()]
        counts = state_counts(c)
        has_master = master.current_import(c) is not None
    warning_counts = {code: sum(code in i["warning_codes"] for i in found) for code in master.WARNING_CODES}
    wanted = {w for w in warning.split(",") if w}
    if wanted == {"any"}:
        found = [i for i in found if i["warning_codes"]]
    elif wanted:
        found = [i for i in found if wanted & set(i["warning_codes"])]
    if sort == "warnings":
        found.sort(key=lambda i: 0 if i["warning_codes"] else 1)  # stable: keeps project order inside
    start = int(cursor) if str(cursor).isdigit() else 0
    limit = max(1, min(limit, 500))
    page = found[start:start + limit]
    return {"items": page, "total": len(found), "counts": counts, "warning_counts": warning_counts,
            "next_cursor": str(start + limit) if start + limit < len(found) else None, "has_master": has_master,
            "sheets": list(ms.SHEETS)}


@router.post("/proposals/decide")
async def decide(request: Request):
    """{ids: [int], action: approve|reject, sheet_by_id?: {id: sheet}, sheet?: str}."""
    data = await body_of(request)
    action = data.get("action")
    ids = [int(i) for i in data.get("ids") or [] if str(i).lstrip("-").isdigit()]
    if action not in ("approve", "reject") or not ids:
        return err("bad_request", 422, "Send ids and action approve or reject")
    sheets = {int(k): v for k, v in (data.get("sheet_by_id") or {}).items() if str(k).isdigit() and v}
    bad = [s for s in sheets.values() if s not in ms.SHEETS]
    if bad:
        return err("bad_sheet", 422, f"Unknown sheet: {bad[0]}")
    default = data.get("sheet") or ""
    chosen = {i: sheets.get(i, default) for i in ids} if (sheets or default) else ""
    done, skipped = master.decide_detail(ids, action, chosen)
    with db.connect() as c:
        return {"done": done, "skipped": skipped, "counts": state_counts(c)}


def _target(p):
    if p["row_id"] is not None:
        return "row_id=? AND field=?", (p["row_id"], p["field"])
    return "row_id IS NULL AND sheet=? AND project_key=? AND field=?", (p["sheet"], p["project_key"], p["field"])


def _message_json(m, files, source_id=None):
    return {"id": m["wa_id"], "ts": m["ts"], "sender": m["sender_name"] or "", "text": m["text"] or "",
            "file_name": m["file_name"], "file_id": files.get(m["wa_id"]), "is_source": m["wa_id"] == source_id}


def source_context(c, kind, ref):
    if kind == "message":
        m = c.execute("SELECT m.*, g.name gname FROM source_messages m LEFT JOIN groups g ON g.jid=m.group_jid "
                      "WHERE wa_id=?", (ref,)).fetchone()
        if not m:
            return {"kind": "message", "missing": True}
        near = c.execute("SELECT * FROM source_messages WHERE group_jid=? AND ts BETWEEN ? AND ? "
                         "AND (text<>'' OR file_name IS NOT NULL) ORDER BY ts LIMIT 60",
                         (m["group_jid"], (m["ts"] or 0) - 1800, (m["ts"] or 0) + 1800)).fetchall()
        ids = [n["wa_id"] for n in near] or [ref]
        files = {r["wa_id"]: r["file_id"] for r in c.execute(
            f"SELECT wa_id, file_id FROM file_occurrences WHERE wa_id IN ({','.join('?' * len(ids))})", ids)}
        return {"kind": "message", "chat": {"jid": m["group_jid"], "name": m["gname"] or m["group_jid"]},
                "message": _message_json(m, files, ref), "messages": [_message_json(n, files, ref) for n in near]}
    rid = int(ref) if str(ref).isdigit() else 0
    r = c.execute("SELECT r.*, f.file_name, f.kind file_kind FROM source_rows r JOIN source_files f ON f.id=r.file_id "
                  "WHERE r.id=?", (rid,)).fetchone()
    if not r:
        return {"kind": kind, "missing": True}
    raw = json.loads(r["raw_json"])
    chats = rows(c.execute("SELECT DISTINCT m.group_jid jid, g.name FROM file_occurrences o "
                           "JOIN source_messages m ON m.wa_id=o.wa_id LEFT JOIN groups g ON g.jid=m.group_jid "
                           "WHERE o.file_id=? ORDER BY m.ts DESC", (r["file_id"],)))
    base = {"kind": kind, "file": {"id": r["file_id"], "name": r["file_name"], "kind": r["file_kind"]},
            "sheet": r["sheet"], "row_number": r["row_number"], "section_label": r["section_label"],
            "hidden": bool(r["hidden"]), "chats": chats}
    if r["kind"] == "pdf_page":
        text = raw.get("text", "")
        return dict(base, page=r["row_number"], text=text[:8000], truncated=len(text) > 8000)
    lay = c.execute("SELECT header_row FROM sheet_layouts WHERE file_id=? AND sheet=?", (r["file_id"], r["sheet"])
                    ).fetchone()
    header = []
    if lay and lay["header_row"]:
        h = c.execute("SELECT raw_json FROM source_rows WHERE file_id=? AND sheet=? AND row_number=? "
                      "AND kind='sheet_row'", (r["file_id"], r["sheet"], lay["header_row"])).fetchone()
        header = json.loads(h["raw_json"])["cells"] if h else []
    cells = [{"col": cell.get("c"), "header": header[i]["v"] if i < len(header) else "", "value": cell["v"]}
             for i, cell in enumerate(raw.get("cells", [])) if (cell.get("v") or "").strip()]
    return dict(base, cells=cells)


def ordered_row_values(c, row):
    vals = master.row_values(c, row)
    imp = master.current_import(c)
    order = []
    if imp:
        s = c.execute("SELECT columns_json FROM master_sheets WHERE import_id=? AND sheet=?",
                      (imp["id"], master.home_sheet(row))).fetchone()
        order = list(json.loads(s["columns_json"])) if s else []
    fields = [f for f in order if f in vals] + sorted(f for f in vals if f not in order)
    return [{"field": f, "label": field_label(f), "raw": vals[f][0]} for f in fields]


@router.get("/proposals/{pid}")
def proposal_detail(pid: int):
    with db.connect() as c:
        p = c.execute(f"SELECT * FROM ({PROPOSAL_SQL}) x WHERE x.id=?", (pid,)).fetchone()
        if not p:
            return err("not_found", 404)
        out = item(p)
        row = c.execute("SELECT * FROM master_rows WHERE id=?", (p["row_id"],)).fetchone() if p["row_id"] else None
        out["row"] = {"id": row["id"], "sheet": master.home_sheet(row), "row_number": row["row_number"],
                      "developer": row["developer"], "project": row["project"], "state": row["state"]} if row else None
        out["row_values"] = ordered_row_values(c, row) if row else []
        where, args = _target(p)
        out["cell_history"] = rows(c.execute(
            f"SELECT id, state, old_raw, new_raw, quote, source_kind, source_ts, decided_at, created_at FROM proposals "
            f"WHERE {where} AND id<>? ORDER BY COALESCE(decided_at, created_at) DESC LIMIT 20", (*args, pid)))
        out["source_context"] = source_context(c, p["source_kind"], p["source_ref"])
    return out


# ---------- master workbook ----------
@router.get("/master")
def master_info():
    with db.connect() as c:
        imp = master.current_import(c)
        return {"import": {"file_name": imp["file_name"], "imported_at": imp["imported_at"]} if imp else None,
                "summary": json.loads(imp["summary_json"] or "{}") if imp else None, "counts": state_counts(c),
                "rows_to_place": c.execute("SELECT COUNT(*) FROM master_issues WHERE state='open'").fetchone()[0],
                "grid_sheets": ms.GRID_SHEETS, "sheets": list(ms.SHEETS), "job": running_job(c)}


@router.post("/master")
async def master_upload(file: UploadFile = File(...)):
    data = await file.read()
    with db.connect() as c:
        if jobs.running():
            return busy(c)
        prev = master.current_import(c)
        previous = json.loads(prev["summary_json"] or "{}") if prev else None
        open_n = c.execute("SELECT COUNT(*) FROM proposals WHERE state='proposed'").fetchone()[0]
    try:
        summary = master.import_workbook(data, file.filename or "master.xlsx")
    except Exception as e:  # not an xlsx, or unreadable
        return err("unreadable", 422, type(e).__name__)
    return {"summary": summary, "superseded": open_n, "previous": previous}


@router.get("/master/issues")
def master_issues(show: str = "open"):
    with db.connect() as c:
        return rows(c.execute(
            "SELECT i.id, i.issue_type, i.reason, i.state, i.decided_at, r.sheet, r.row_number, r.developer, "
            "r.project, r.location_code, r.target_sheet FROM master_issues i JOIN master_rows r ON r.id=i.row_id "
            + ("WHERE i.state='open' " if show == "open" else "") + "ORDER BY i.issue_type, r.sheet, r.row_number"))


@router.post("/master/issues/{issue_id}")
async def master_issue(issue_id: int, request: Request):
    data = await body_of(request)
    action, sheet = data.get("action"), data.get("sheet") or ""
    if action not in ("move", "keep", "drop") or (action == "move" and sheet not in ms.GRID_SHEETS):
        return err("bad_request", 422, "action must be move (with a price-grid sheet), keep or drop")
    master.resolve_issue(issue_id, action, sheet)
    with db.connect() as c:
        i = c.execute("SELECT id, state FROM master_issues WHERE id=?", (issue_id,)).fetchone()
    return dict(i) if i else err("not_found", 404)


@router.get("/master/export/{which}")
def master_export_file(which: str):
    if which not in ("team", "sql"):
        return err("not_found", 404)
    data = master_export.team_file() if which == "team" else master_export.sql_file()
    if data is None:
        return err("no_master", 409, "Upload the master workbook first")
    day = datetime.datetime.now(web.DUBAI).strftime("%Y-%m-%d")
    name = f"App Updates {day}.xlsx" if which == "team" else f"App Updates SQL {day}.xlsx"
    return Response(data, media_type=web.XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- jobs / pipeline ----------
REDO = {"map_projects": "Re-ask unreviewed", "parse_files": "Re-read all", "download_media": "Retry failed"}


def chat_routine(log):
    """Today's sync routine as one job: steps 1, 2, 3, 4 and 6b for every inventory chat."""
    stages = [("Sync chats", history.sync_groups), ("Pull history", history.pull_history),
              ("Download attachments", history.download_media), ("Read files", parse_files.parse_all),
              ("Propose sheet updates", proposals.propose)]
    for i, (name, fn) in enumerate(stages):
        log("PROGRESS " + json.dumps({"stage": name, "done": i, "total": len(stages)}))
        fn(log)
    log("PROGRESS " + json.dumps({"stage": "Finished", "done": len(stages), "total": len(stages)}))


CHAT_ROUTINE = ("chat_routine", "Run chat steps for all inventory chats",
                "Steps 1, 2, 3, 4 and 6b in one go: the everyday sync for the ticked chats.", chat_routine)


def steps():
    return [CHAT_ROUTINE, *web.STEPS]


@router.get("/steps")
def list_steps():
    return [{"key": k, "title": t, "description": d, "redo_label": REDO.get(k)} for k, t, d, _ in steps()]


def job_json(j, since=0, with_log=True):
    out = {k: j[k] for k in ("id", "name", "status", "started_at", "finished_at")}
    if with_log:
        log = j["log"] or ""
        out.update(log_chunk=log[since:], next_offset=len(log), progress=parse_progress(log),
                   result=parse_result(log))
    return out


def parse_progress(log):
    """Last PROGRESS {json} line, else the last 'n/m' count a step logged (e.g. 'read 60/120 messages')."""
    for line in reversed(log.splitlines()):
        body = line[9:] if re.match(r"\d\d:\d\d:\d\d ", line) else line
        if body.startswith("PROGRESS "):
            try:
                return json.loads(body[9:])
            except ValueError:
                continue
        m = re.search(r"(\d+)\s*/\s*(\d+)", body)
        if m and int(m.group(2)) and int(m.group(1)) <= int(m.group(2)):
            return {"stage": body.strip()[:120], "done": int(m.group(1)), "total": int(m.group(2))}
    return None


def parse_result(log):
    for line in reversed(log.splitlines()):
        i = line.find("RESULT ")
        if i != -1:
            try:
                return json.loads(line[i + 7:])
            except ValueError:
                return None
    return None


@router.get("/jobs")
def list_jobs(limit: int = 8):
    with db.connect() as c:
        return [job_json(j, with_log=False) for j in c.execute(
            "SELECT id, name, status, started_at, finished_at FROM jobs ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 50)),)).fetchall()]


@router.post("/jobs/{key}")
async def start_job(key: str, request: Request):
    data = await body_of(request)
    found = {k: (t, fn) for k, t, _, fn in steps()}
    if key not in found:
        return err("not_found", 404, f"Unknown step {key}")
    title, fn = found[key]
    args = (True,) if data.get("redo") and key in REDO else ()
    job_id = jobs.start(title, fn, *args)
    if not job_id:
        with db.connect() as c:
            return busy(c)
    return JSONResponse({"job_id": job_id}, 202)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, since: int = 0):
    with db.connect() as c:
        j = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return job_json(j, max(0, since)) if j else err("not_found", 404)


@router.get("/pipeline")
def pipeline():
    with db.connect() as c:
        counts = {
            "groups": c.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
            "in_scope": len(db.scoped_groups(c)),
            "messages": c.execute("SELECT COUNT(*) FROM source_messages").fetchone()[0],
            "files": c.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
            "projects": c.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "units": c.execute("SELECT COUNT(*) FROM units").fetchone()[0],
        }
        return {"counts": counts, "job": running_job(c)}


@router.post("/reset")
async def reset_all(request: Request):
    data = await body_of(request)
    if str(data.get("confirm", "")).strip() != "DELETE ALL":
        return err("confirm", 422, "Type DELETE ALL to confirm")
    if jobs.running():
        with db.connect() as c:
            return busy(c)
    db.reset()
    return {"ok": True}


# ---------- legacy pages (More), straight ports of web.py ----------
@router.get("/projects")
def projects(q: str = ""):
    with db.connect() as c:
        return rows(c.execute(
            "SELECT p.slug, p.name, d.name developer, "
            "(SELECT COUNT(*) FROM project_phases f WHERE f.project_id=p.id) phases, "
            "(SELECT COUNT(*) FROM units u WHERE u.project_id=p.id) units "
            "FROM projects p LEFT JOIN developers d ON d.id=p.developer_id WHERE p.name LIKE ? ORDER BY p.name",
            (f"%{q}%",)))


@router.get("/projects/{slug}")
def project(slug: str):
    with db.connect() as c:
        p = c.execute("SELECT p.*, d.name developer FROM projects p LEFT JOIN developers d ON d.id=p.developer_id "
                      "WHERE slug=?", (slug,)).fetchone()
        if not p:
            return err("not_found", 404)
        fs = rows(c.execute("SELECT a.* FROM fact_assertions a JOIN fact_links l ON l.fact_id=a.id "
                            "WHERE l.project_id=? ORDER BY a.observed_ts", (p["id"],)))
        by_type = {}
        for f in fs:
            by_type.setdefault(f["fact_type"], []).append(f)
        key_facts = [dict(web.fact_summary(by_type.get(t, [])), type=t) for t in build.REQUIRED_FACTS]
        return {
            "project": dict(p), "key_facts": key_facts, "phase_facts": by_type.get("phase_status", []),
            "other_facts": [f for t in ("launch", "unit_status", "other") for f in by_type.get(t, [])],
            "phases": rows(c.execute("SELECT * FROM project_phases WHERE project_id=? ORDER BY raw_label", (p["id"],))),
            "units": [dict(u) for u in web.latest_snapshots(c, "u.project_id=?", (p["id"],))],
            "images": rows(c.execute(
                "SELECT m.*, f.id fid, f.file_name FROM media_assignments m JOIN source_files f ON f.id=m.file_id "
                "WHERE m.review_state='approved' AND m.project_name IN "
                "(SELECT raw_label FROM project_aliases WHERE project_id=? UNION SELECT ?)", (p["id"], p["name"]))),
        }


@router.get("/units")
def units(q: str = "", status: str = "", max_price: str = ""):
    where, args = ["1=1"], []
    if q:
        where.append("(u.unit_code LIKE ? OR p.name LIKE ? OR u.phase_key LIKE ? OR s.unit_type_raw LIKE ?)")
        args += [f"%{q}%"] * 4
    if status:
        where.append("s.unit_status=?")
        args.append(status)
    with db.connect() as c:
        res = [dict(r) for r in web.latest_snapshots(c, " AND ".join(where), args)]
    if max_price.replace(",", "").isdigit():
        mp = float(max_price.replace(",", ""))
        res = [r for r in res if r["price_value"] is not None and r["price_value"] <= mp]
    return {"units": res[:2000], "total": len(res)}


@router.get("/units/{unit_id}")
def unit_history(unit_id: int):
    with db.connect() as c:
        u = c.execute("SELECT u.*, p.name project, p.slug FROM units u JOIN projects p ON p.id=u.project_id "
                      "WHERE u.id=?", (unit_id,)).fetchone()
        if not u:
            return err("not_found", 404)
        snaps = rows(c.execute("SELECT s.*, r.file_id, r.sheet, r.row_number, f.file_name FROM unit_snapshots s "
                               "JOIN source_rows r ON r.id=s.source_row_id JOIN source_files f ON f.id=r.file_id "
                               "WHERE s.unit_id=? ORDER BY COALESCE(s.snapshot_ts,0) DESC", (unit_id,)))
    return {"unit": dict(u), "snapshots": snaps}


FACT_STATES = ("verified_from_source", "needs_validation", "source_stated_unverified", "rejected")


@router.post("/facts/{fact_id}")
async def set_fact(fact_id: int, request: Request):
    status = (await body_of(request)).get("status")
    if status not in FACT_STATES:
        return err("bad_request", 422, f"status must be one of {', '.join(FACT_STATES)}")
    with db.connect() as c:
        c.execute("UPDATE fact_assertions SET validation_status=? WHERE id=?", (status, fact_id))
    return {"id": fact_id, "status": status}


SEVERITY = "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"


@router.get("/review/issues")
def review_issues(kind: str = ""):
    with db.connect() as c:
        sql, args = "SELECT * FROM review_issues", ()
        if kind:
            sql, args = sql + " WHERE issue_type=?", (kind,)
        return {
            "kinds": rows(c.execute(f"SELECT issue_type, severity, COUNT(*) n FROM review_issues "
                                    f"GROUP BY issue_type, severity ORDER BY {SEVERITY}")),
            "issues": rows(c.execute(f"{sql} ORDER BY {SEVERITY}, id LIMIT 500", args)),
            "pending_mappings": c.execute("SELECT COUNT(*) FROM label_mappings WHERE review_state='proposed'"
                                          ).fetchone()[0],
            "pending_images": c.execute("SELECT COUNT(*) FROM media_assignments WHERE review_state IN "
                                        "('proposed','withheld')").fetchone()[0],
        }


@router.get("/review/mappings")
def review_mappings(state: str = "proposed"):
    with db.connect() as c:
        sql = ("SELECT x.key, x.file_id, x.sheet, x.section_label, x.raw_project, x.raw_phase, x.row_count, "
               "m.decision, m.developer, m.project_name, m.phase_label, m.confidence, m.rationale, m.source, "
               "m.review_state, f.file_name FROM label_contexts x LEFT JOIN label_mappings m ON m.context_key=x.key "
               "LEFT JOIN source_files f ON f.id=x.file_id ")
        if state == "all":
            res = rows(c.execute(sql + "ORDER BY f.file_name, x.sheet"))
        elif state == "unmapped":
            res = rows(c.execute(sql + "WHERE m.context_key IS NULL ORDER BY f.file_name, x.sheet"))
        else:
            res = rows(c.execute(sql + "WHERE m.review_state=? ORDER BY f.file_name, x.sheet", (state,)))
        known = [r[0] for r in c.execute("SELECT DISTINCT project_name FROM label_mappings "
                                         "WHERE decision='project' AND project_name<>'' ORDER BY 1")]
    for x in res:
        x["warning"] = rules.why_not_a_project(x["project_name"]) if x["decision"] == "project" and x["project_name"] \
            else None
    return {"mappings": res, "known_projects": known}


@router.post("/review/mappings")
async def save_mapping(request: Request):
    """{key, action: approve|reject|exclude, project_name?, phase_label?, developer?} (same rules as web.py)."""
    d = await body_of(request)
    if not d.get("key") or d.get("action") not in ("approve", "reject", "exclude"):
        return err("bad_request", 422)
    web.save_mapping(key=d["key"], action=d["action"], project_name=d.get("project_name", ""),
                     phase_label=d.get("phase_label", ""), developer=d.get("developer", ""), back="/")
    return {"ok": True}


@router.get("/review/images")
def review_images(state: str = "proposed"):
    with db.connect() as c:
        return rows(c.execute("SELECT m.*, f.file_name FROM media_assignments m JOIN source_files f ON f.id=m.file_id "
                              "WHERE m.review_state=? ORDER BY m.updated_at DESC LIMIT 200", (state,)))


@router.post("/review/images")
async def save_image(request: Request):
    d = await body_of(request)
    if d.get("action") not in ("approve", "withhold", "exclude") or not str(d.get("file_id", "")).isdigit():
        return err("bad_request", 422)
    web.save_image(file_id=int(d["file_id"]), action=d["action"], project_name=d.get("project_name", ""), back="/")
    return {"ok": True}


@router.get("/review/facts")
def review_facts(status: str = ""):
    with db.connect() as c:
        sql, args = "SELECT * FROM fact_assertions", ()
        if status:
            sql, args = sql + " WHERE validation_status=?", (status,)
        return rows(c.execute(sql + " ORDER BY observed_ts DESC LIMIT 500", args))


@router.get("/sources")
def sources():
    with db.connect() as c:
        return {
            "files": rows(c.execute("SELECT f.id, f.file_name, f.kind, f.mimetype, f.size, f.parse_status, f.parse_error, "
                                    "(SELECT COUNT(*) FROM file_occurrences o WHERE o.file_id=f.id) posts, "
                                    "(SELECT COUNT(*) FROM source_rows r WHERE r.file_id=f.id) nrows "
                                    "FROM source_files f ORDER BY f.kind, f.file_name")),
            "failed": rows(c.execute("SELECT f.wa_id, f.error, f.attempted_at, m.file_name, m.ts, m.group_jid, "
                                     "g.name chat_name FROM media_fetch f JOIN source_messages m ON m.wa_id=f.wa_id "
                                     "LEFT JOIN groups g ON g.jid=m.group_jid WHERE f.status='failed' "
                                     "ORDER BY m.ts DESC")),
        }


@router.get("/sources/messages/{wa_id}")
def source_message(wa_id: str):
    with db.connect() as c:
        out = source_context(c, "message", wa_id)
    return err("not_found", 404) if out.get("missing") else out


@router.get("/sources/rows/{row_id}")
def source_row(row_id: int):
    with db.connect() as c:
        kind = c.execute("SELECT kind FROM source_rows WHERE id=?", (row_id,)).fetchone()
        if not kind:
            return err("not_found", 404)
        return source_context(c, "pdf_page" if kind[0] == "pdf_page" else "row", str(row_id))


@router.get("/reconciliation")
def reconciliation():
    with db.connect() as c:
        return {"report": build.reconcile(c), "per_file": rows(c.execute(
            "SELECT f.file_name, r.file_id, s.mapping_state, COUNT(*) n FROM unit_snapshots s "
            "JOIN source_rows r ON r.id=s.source_row_id JOIN source_files f ON f.id=r.file_id "
            "GROUP BY r.file_id, s.mapping_state ORDER BY f.file_name"))}


# ---------- the built SPA at /app ----------
DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
NOT_BUILT = ("<!doctype html><title>Receiver</title><p>The new app hasn't been built on this server. "
             "Run <code>npm ci && npm run build</code> in <code>receiver/frontend</code>, or use the "
             "<a href='/'>classic pages</a>.</p>")


@spa_router.get("/app", include_in_schema=False)
@spa_router.get("/app/{path:path}", include_in_schema=False)
def spa(path: str = ""):
    index = os.path.join(DIST, "index.html")
    if not os.path.exists(index):
        return HTMLResponse(NOT_BUILT, 503)
    if path:
        full = os.path.realpath(os.path.join(DIST, path))
        if full.startswith(os.path.realpath(DIST) + os.sep) and os.path.isfile(full):
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(full, headers={"Cache-Control": cache})
        if path.startswith("assets/"):
            return Response("Not found", 404)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
