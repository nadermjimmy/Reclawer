"""JSON API for the admin app (/app). Session cookie and auth live in web.py.

Unauthenticated /api/* calls get 401 {"error": "login"} from web.auth_middleware. Errors share one shape:
{"error": code, "detail"?: ...}; 409 {"error": "busy", "job": {...}} means another job is running.
"""
import datetime, json, os, re, time
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
import brain, build, chat_sync, db, feed, history, jobs, master, master_export, master_schema as ms, parse_files, proposals, \
    rules, search, web

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
PROPOSAL_SQL = (f"SELECT x.*, g.name chat_name, b.verdict brain_verdict, b.confidence brain_confidence, "
                f"b.reason brain_reason, b.action brain_action FROM (SELECT p.*, r.project row_project, "
                f"r.developer row_developer, "
                f"r.row_number, {CHAT_SQL} chat_jid, {FILE_SQL} source_file FROM proposals p "
                f"LEFT JOIN master_rows r ON r.id=p.row_id) x LEFT JOIN groups g ON g.jid=x.chat_jid "
                f"LEFT JOIN brain_reviews b ON b.proposal_id=x.id")


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
        "decided_by": p["decided_by"] if "decided_by" in p.keys() else None,
        "brain": ({"verdict": p["brain_verdict"], "confidence": p["brain_confidence"], "reason": p["brain_reason"],
                   "action": p["brain_action"]} if "brain_verdict" in p.keys() and p["brain_verdict"] else None),
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


@router.post("/proposals/{pid}/reopen")
def reopen_proposal(pid: int):
    """Undo a brain decision (approved or rejected by the brain): back on Updates."""
    if not brain.reopen(pid):
        return err("not_reopenable", 422, "Only suggestions the brain decided can be reopened")
    with db.connect() as c:
        return {"ok": True, "counts": state_counts(c)}


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
    if r["kind"] == "image":
        return dict(base, image_url=f"/sources/file/{r['file_id']}")
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


def chat_routine(log, mode="batch"):
    """Everyday sync of every inventory chat (all groups + ticked private chats), text first:
    list chats, pull messages, suggest from text, then download and read attachments and suggest from them.
    mode 'live' answers everything now (full price); 'batch' sends the reading to the half-price batch queue,
    where the file stages start after this job has finished downloading."""
    has_master = False
    with db.connect() as c:
        has_master = master.current_import(c) is not None
    stages = ["Sync chats", "Pull history", "Suggest from text", "Download attachments", "Read files",
              "Suggest from files"]

    def step(i):
        log("PROGRESS " + json.dumps({"stage": stages[i], "done": i, "total": len(stages)}))

    step(0)
    history.sync_groups(log)
    step(1)
    history.pull_history(log)
    step(2)
    if not has_master:
        log("Skipped suggestions: upload the master workbook on the Master page first")
    elif mode == "live":
        n, f = proposals.run_stage_live(log, "text")
        log(f"Text: {n} suggestion(s), {f} failed request(s)")
    else:
        proposals.start_batched(log)  # text batch goes out now; file stages follow once this job ends
    step(3)
    history.download_media(log)
    step(4)
    parse_files.parse_all(log)
    step(5)
    if has_master and mode == "live":
        for st in ("sheets", "pdfs", "images"):
            n, f = proposals.run_stage_live(log, st)
            log(f"{st.capitalize()}: {n} suggestion(s), {f} failed request(s)")
        brain.review(log)
    elif has_master:
        log("File suggestions follow automatically after the text batch: they arrive on the Updates page")
    log("PROGRESS " + json.dumps({"stage": "Finished", "done": len(stages), "total": len(stages)}))


CHAT_ROUTINE = ("chat_routine", "Sync all chats",
                "Every group plus ticked private chats: text suggestions first, then attachments.", chat_routine)


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
    if key == "chat_routine":
        mode = "live" if data.get("mode") == "live" else "batch"
        args, title = (mode,), f"{title} ({'fast' if mode == 'live' else 'half price'})"
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


# ---------- chats (WhatsApp-Web style reading view; listen-only: nothing here sends to WhatsApp) ----------
VISIBLE = feed.VISIBLE  # uses alias m


def chat_kind(jid):
    return "group" if jid.endswith("@g.us") else "private"


def chat_row(c, jid):
    """(jid, name, in_scope) for a chat we know (groups table or stored messages), else None."""
    g = c.execute("SELECT g.name, COALESCE(s.in_scope,0) in_scope FROM groups g LEFT JOIN group_scope s ON s.jid=g.jid "
                  "WHERE g.jid=?", (jid,)).fetchone()
    if g:
        return {"jid": jid, "name": g["name"] or jid, "in_scope": bool(g["in_scope"])}
    if c.execute("SELECT 1 FROM source_messages WHERE group_jid=? LIMIT 1", (jid,)).fetchone():
        s = c.execute("SELECT in_scope FROM group_scope WHERE jid=?", (jid,)).fetchone()
        return {"jid": jid, "name": history.chat_name(jid, None), "in_scope": bool(s and s[0])}
    return None


def open_by_chat(c):
    out = {}
    for r in c.execute(f"SELECT {CHAT_SQL} chat_jid FROM proposals p WHERE p.state='proposed'"):
        if r["chat_jid"]:
            out[r["chat_jid"]] = out.get(r["chat_jid"], 0) + 1
    return out


def _preview(c, jid):
    last = c.execute(f"SELECT m.text, m.file_name, m.message_type, m.sender_name FROM source_messages m "
                     f"WHERE m.group_jid=? AND {VISIBLE} ORDER BY m.ts DESC LIMIT 1", (jid,)).fetchone()
    if not last:
        return "", False, None
    text = last["text"] or last["file_name"] or \
        {"imageMessage": "Photo", "videoMessage": "Video"}.get(last["message_type"], "Attachment")
    return text[:140], not last["text"], last["sender_name"]


KIND_FILTER = {"groups": "group", "private": "private"}


@router.get("/chats")
def list_chats(filter: str = "all", q: str = "", scope: str = "", has_suggestions: bool = False):
    """Every chat with messages (plus groups and inventory chats without any), newest first.
    filter: all / inventory / suggestions / groups / private. q filters names (Arabic-aware)."""
    if scope == "ticked":
        filter = "inventory"
    if has_suggestions:
        filter = "suggestions"
    with db.connect() as c:
        stats = {r["jid"]: r for r in c.execute(
            f"SELECT m.group_jid jid, COUNT(*) n, MAX(m.ts) last_ts, SUM(m.has_media) media FROM source_messages m "
            f"WHERE {VISIBLE} GROUP BY m.group_jid")}
        names = {r["jid"]: r["name"] for r in c.execute("SELECT jid, name FROM groups")}
        scoped = {r[0] for r in c.execute("SELECT jid FROM group_scope WHERE in_scope=1")}
        synced = {r["jid"]: r for r in c.execute("SELECT * FROM chat_syncs")}
        fresh = {r[0]: r[1] for r in c.execute(
            f"SELECT m.group_jid, COUNT(*) FROM source_messages m JOIN chat_syncs s ON s.jid=m.group_jid "
            f"WHERE m.ingested_at>s.synced_at AND {VISIBLE} GROUP BY m.group_jid")}
        opened = open_by_chat(c)
        jids = set(stats) | scoped | {j for j in names if j.endswith("@g.us")}
        toks = search.tokens(q)
        out = []
        for jid in jids:
            name = names.get(jid) or history.chat_name(jid, None)
            if toks and not all(t in search.normalize(name) for t in toks):
                continue
            st = stats.get(jid)
            item = {"jid": jid, "name": name, "kind": chat_kind(jid), "in_scope": jid in scoped,
                    "count": st["n"] if st else 0, "media": (st["media"] or 0) if st else 0,
                    "last_ts": st["last_ts"] if st else None, "open_suggestions": opened.get(jid, 0),
                    "last_sync_at": synced[jid]["synced_at"] if jid in synced else None,
                    "new_since_sync": fresh.get(jid, 0) if jid in synced else None}
            if (filter == "inventory" and not item["in_scope"]) or (filter == "suggestions" and not
                                                                    item["open_suggestions"]) \
                    or (filter in KIND_FILTER and item["kind"] != KIND_FILTER[filter]):
                continue
            out.append(item)
        out.sort(key=lambda i: (i["last_ts"] or 0, i["name"]), reverse=True)
        for item in out:
            item["preview"], item["media_preview"], item["preview_sender"] = _preview(c, item["jid"]) if item["count"] \
                else ("", False, None)
        counts = {"all": len(jids), "inventory": len(scoped & jids), "suggestions": len(set(opened) & jids)}
    return {"chats": out, "counts": counts}


def _set_scope(c, jids, on):
    c.executemany("INSERT INTO group_scope(jid,in_scope) VALUES(?,?) ON CONFLICT(jid) DO UPDATE SET in_scope=?",
                  [(j, 1 if on else 0, 1 if on else 0) for j in jids])


@router.patch("/chats/{jid}")
async def patch_chat(jid: str, request: Request):
    data = await body_of(request)
    if not isinstance(data.get("in_scope"), bool):
        return err("bad_request", 422, "Send in_scope true or false")
    with db.connect() as c:
        if not chat_row(c, jid):
            return err("unknown_chat", 404, "This chat isn't known yet")
        _set_scope(c, [jid], data["in_scope"])
    return {"jid": jid, "in_scope": data["in_scope"]}


@router.patch("/chats")
async def patch_chats(request: Request):
    data = await body_of(request)
    jids = [j for j in data.get("jids") or [] if isinstance(j, str)]
    if not jids or not isinstance(data.get("in_scope"), bool):
        return err("bad_request", 422, "Send jids and in_scope")
    with db.connect() as c:
        jids = [j for j in jids if chat_row(c, j)]
        _set_scope(c, jids, data["in_scope"])
    return {"updated": len(jids)}


def _message_rows(c, rows):
    """Chat messages as JSON, with the stored file (download link) and the suggestions they produced."""
    ids = [r["wa_id"] for r in rows]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    files = {r["wa_id"]: r["file_id"] for r in c.execute(
        f"SELECT wa_id, file_id FROM file_occurrences WHERE wa_id IN ({marks})", ids)}
    failed = {r[0] for r in c.execute(f"SELECT wa_id FROM media_fetch WHERE status='failed' AND wa_id IN ({marks})",
                                      ids)}
    finfo = {}
    if files:
        fids = list(set(files.values()))
        finfo = {r["id"]: dict(r) for r in c.execute(
            f"SELECT id, kind, file_name, mimetype, size, parse_status FROM source_files "
            f"WHERE id IN ({','.join('?' * len(fids))})", fids)}
    sugg = {}
    for r in c.execute(f"SELECT id, source_ref FROM proposals WHERE source_kind='message' AND state<>'superseded' "
                       f"AND source_ref IN ({marks})", ids):
        sugg.setdefault(r["source_ref"], []).append(r["id"])
    if files:
        fids = list(set(files.values()))
        by_file = {}
        for r in c.execute(f"SELECT p.id, sr.file_id FROM proposals p JOIN source_rows sr "
                           f"ON sr.id=CAST(p.source_ref AS INTEGER) WHERE p.source_kind IN ('row','pdf_page','image') "
                           f"AND p.state<>'superseded' AND sr.file_id IN ({','.join('?' * len(fids))})", fids):
            by_file.setdefault(r["file_id"], []).append(r["id"])
        for wa, fid in files.items():
            sugg.setdefault(wa, []).extend(by_file.get(fid, []))
    out = []
    for m in rows:
        kind = feed.kind_of_message(m)
        fid = files.get(m["wa_id"])
        f = finfo.get(fid)
        if f:
            f = dict(f, url=f"/sources/file/{fid}", download_url=f"/sources/file/{fid}?download=1")
        out.append({"id": m["wa_id"], "ts": m["ts"], "sender": m["sender_name"], "text": m["text"] or "",
                    "kind": kind, "file_name": m["file_name"], "mimetype": m["mimetype"], "file": f,
                    "media": "ok" if fid else "failed" if m["wa_id"] in failed else
                             ("none" if kind == "text" else "pending"),
                    "suggestion_ids": sorted(set(sugg.get(m["wa_id"], [])))})
    return out


def _page(c, jid, cond, args, order, limit):
    return c.execute(f"SELECT m.rowid rid, m.* FROM source_messages m WHERE m.group_jid=? AND {VISIBLE} {cond} "
                     f"ORDER BY m.ts {order}, m.rowid {order} LIMIT ?", [jid, *args, limit]).fetchall()


@router.get("/chats/{jid}/messages")
def chat_messages(jid: str, before: str = "", after: str = "", around: str = "", limit: int = 60):
    """Paged messages, oldest -> newest. before/after: a message id (wa_id) or a timestamp; around: a wa_id to
    open the chat at (half the page before it, half after). has_more = older exist, has_newer = newer exist."""
    limit = max(10, min(limit, 200))
    with db.connect() as c:
        chat = chat_row(c, jid)
        if not chat:
            return err("unknown_chat", 404, "This chat isn't known yet")

        def anchor(ref):
            """(ts, rowid) of a message id, or (ts, big) for a plain timestamp."""
            r = c.execute("SELECT ts, rowid FROM source_messages WHERE wa_id=? AND group_jid=?", (ref, jid)).fetchone()
            if r:
                return r["ts"] or 0, r["rowid"]
            return (int(ref), None) if ref.isdigit() else (None, None)

        older_cond = "AND (COALESCE(m.ts,0)<? OR (COALESCE(m.ts,0)=? AND m.rowid<?))"
        newer_cond = "AND (COALESCE(m.ts,0)>? OR (COALESCE(m.ts,0)=? AND m.rowid>?))"
        BIG = 1 << 62
        found = None
        if around:
            ts, rid = anchor(around)
            if rid is None:
                return err("not_found", 404, "That message isn't in this chat")
            half = limit // 2
            older = _page(c, jid, older_cond, [ts, ts, rid], "DESC", half + 1)
            newer = _page(c, jid, "AND (COALESCE(m.ts,0)>? OR (COALESCE(m.ts,0)=? AND m.rowid>=?))", [ts, ts, rid],
                          "ASC", limit - half + 1)
            has_more, has_newer = len(older) > half, len(newer) > limit - half
            rows = list(reversed(older[:half])) + newer[:limit - half]
            found = around
        elif after:
            ts, rid = anchor(after)
            newer = _page(c, jid, newer_cond, [ts or 0, ts or 0, rid if rid is not None else BIG], "ASC", limit + 1)
            has_newer, rows = len(newer) > limit, newer[:limit]
            has_more = True
        else:
            if before:
                ts, rid = anchor(before)
                older = _page(c, jid, older_cond, [ts or 0, ts or 0, rid if rid is not None else -1], "DESC",
                              limit + 1)
            else:
                older = _page(c, jid, "", [], "DESC", limit + 1)
            has_more, rows, has_newer = len(older) > limit, list(reversed(older[:limit])), bool(before)
        return {"chat": dict(chat, kind=chat_kind(jid)), "messages": _message_rows(c, rows), "has_more": has_more,
                "has_newer": has_newer, "around": found}


@router.get("/chats/{jid}/suggestions")
@router.get("/chats/{jid}/proposals")
def chat_suggestions(jid: str, state: str = "", days: int = 30, limit: int = 200):
    """Suggestions whose source is this chat: open ones plus those decided in the last `days`, newest first.
    state: proposed / approved / rejected to narrow; counts cover the same window."""
    since = int(time.time()) - max(1, days) * 86400
    with db.connect() as c:
        if not chat_row(c, jid):
            return err("unknown_chat", 404, "This chat isn't known yet")
        found = [item(p) for p in c.execute(
            f"SELECT * FROM ({PROPOSAL_SQL}) x WHERE x.chat_jid=? AND (x.state='proposed' OR "
            f"(x.state IN ('approved','rejected') AND x.decided_at>=?)) "
            f"ORDER BY CASE x.state WHEN 'proposed' THEN 0 ELSE 1 END, COALESCE(x.source_ts,0) DESC, x.id DESC",
            (jid, since)).fetchall()]
        has_master = master.current_import(c) is not None
    counts = {s: sum(i["state"] == s for i in found) for s in ("proposed", "approved", "rejected")}
    if state in counts:
        found = [i for i in found if i["state"] == state]
    return {"items": found[:max(1, min(limit, 500))], "counts": counts, "has_master": has_master,
            "sheets": list(ms.SHEETS)}


@router.post("/chats/{jid}/sync")
async def sync_chat(jid: str, request: Request, add_to_scope: str = ""):
    """202 {job_id} · 422 unknown_chat · 409 busy · 422 not_in_scope (confirm, then send add_to_scope)."""
    data = await body_of(request)
    add = bool(data.get("add_to_scope")) or add_to_scope.lower() in ("1", "true", "yes")
    with db.connect() as c:
        chat = chat_row(c, jid)
        if not chat:
            return err("unknown_chat", 422, "This chat isn't known yet. Run Sync chats in More > Pipeline.")
        if jobs.running():
            return busy(c)
        if not chat["in_scope"] and not add:
            return err("not_in_scope", 422, "Syncing adds this chat to the inventory chats. Confirm to continue.",
                       chat=chat)
    job_id = jobs.start(f"Sync chat: {chat['name']}", chat_sync.sync_chat, jid, add and not chat["in_scope"])
    if not job_id:
        with db.connect() as c:
            return busy(c)
    return JSONResponse({"job_id": job_id, "jid": jid}, 202)


@router.get("/search")
def search_api(q: str = "", chat: str = "", jid: str = "", limit: int = 50):
    """Instant search: chat names, then messages (text, file name, sender), newest first, with a snippet and
    highlight ranges [[start, end), ...] in the snippet. q needs 2+ characters. chat (or jid) narrows to one chat."""
    t0 = time.perf_counter()
    q, chat = q.strip(), chat or jid
    empty = {"q": q, "chats": [], "messages": [], "has_more": False, "took_ms": 0}
    toks = search.tokens(q)
    if len(q) < 2 or not toks:
        return empty
    limit = max(1, min(limit, 1000))
    with db.connect() as c:
        names = {r["jid"]: r["name"] for r in c.execute("SELECT jid, name FROM groups")}
        chats = []
        if not chat:
            scoped = {r[0] for r in c.execute("SELECT jid FROM group_scope WHERE in_scope=1")}
            last = {r[0]: r[1] for r in c.execute("SELECT group_jid, MAX(ts) FROM source_messages GROUP BY group_jid")}
            for j in set(names) | set(last):
                name = names.get(j) or history.chat_name(j, None)
                norm = search.normalize(name)
                if all(t in norm for t in toks):
                    chats.append({"jid": j, "name": name, "kind": chat_kind(j), "in_scope": j in scoped,
                                  "last_ts": last.get(j), "match_ranges": search.ranges_in(name, toks)})
            chats.sort(key=lambda x: x["last_ts"] or 0, reverse=True)
        rows, more = search.find_messages(c, q, chat, limit)
        hits = []
        for r in rows:
            h = search.message_hit(r, toks)
            h["chat_name"] = names.get(r["group_jid"]) or history.chat_name(r["group_jid"], None)
            hits.append(h)
    return {"q": q, "chats": chats[:20], "messages": hits, "has_more": more,
            "took_ms": round((time.perf_counter() - t0) * 1000, 1)}


# ---------- insights ----------
STALE_DAYS = 14  # owner decision: no change for 14 days (last approval, else the master Date cell)


def _day(ts):
    return datetime.datetime.fromtimestamp(ts, web.DUBAI).date().isoformat()


def ins_summary(c, since, now):
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]
    spans = sorted(r[0] for r in c.execute(
        "SELECT decided_at-created_at FROM proposals WHERE state IN ('approved','rejected') AND decided_at>=? "
        "AND created_at IS NOT NULL", (since,)))
    return {"open": one("SELECT COUNT(*) FROM proposals WHERE state='proposed'"),
            "approved": one("SELECT COUNT(*) FROM proposals WHERE state='approved' AND decided_at>=?", since),
            "rejected": one("SELECT COUNT(*) FROM proposals WHERE state='rejected' AND decided_at>=?", since),
            "median_decision_s": spans[len(spans) // 2] if spans else None,
            "rows_to_place": one("SELECT COUNT(*) FROM master_issues WHERE state='open'"),
            "stale_count": len(_stale_rows(c, now))}


def ins_changes(c, since, now):
    approved = c.execute(f"SELECT * FROM ({PROPOSAL_SQL}) x WHERE x.state='approved' AND x.decided_at>=? "
                         f"ORDER BY x.decided_at DESC, x.id DESC", (since,)).fetchall()
    by = {}
    for p in approved:
        k = (p["sheet"] or "", p["field"])
        by[k] = by.get(k, 0) + 1
    groups = [{"sheet": s, "field": f, "field_label": field_label(f), "count": n}
              for (s, f), n in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))]
    recent = [{"id": p["id"], "project": p["row_project"] or p["project"], "sheet": p["sheet"] or "",
               "field_label": field_label(p["field"]), "old_raw": p["old_raw"] or "", "new_raw": p["new_raw"] or "",
               "decided_at": p["decided_at"], "chat_jid": p["chat_jid"], "chat_name": p["chat_name"]}
              for p in approved[:30]]
    return {"total": len(approved), "by_sheet_field": groups, "recent": recent}


def ins_price_moves(c, since, now):
    fields = ",".join("?" * len(ms.UNIT_FIELDS))
    out = []
    for p in c.execute(f"SELECT * FROM ({PROPOSAL_SQL}) x WHERE x.state='approved' AND x.decided_at>=? "
                       f"AND x.field IN ({fields})", (since, *ms.UNIT_FIELDS)).fetchall():
        old = ms.parse(p["field"], p["old_raw"]) if p["old_raw"] else {}
        new = json.loads(p["new_parsed_json"] or "{}")
        d = delta(p) or {}
        if not (d.get("price_pct") is not None or d.get("area_to") or d.get("status_to") is not None):
            continue
        side = lambda x: {"area": x.get("area_sqm"), "price": x.get("price_egp"), "status": x.get("status") or None}
        out.append({"id": p["id"], "project": p["row_project"] or p["project"], "developer": p["row_developer"] or
                    p["developer"] or "", "sheet": p["sheet"] or "", "field": p["field"],
                    "unit_type": field_label(p["field"]), "old_raw": p["old_raw"] or "", "new_raw": p["new_raw"] or "",
                    "old": side(old), "new": side(new), "pct": d.get("price_pct"), "decided_at": p["decided_at"],
                    "chat_jid": p["chat_jid"], "chat_name": p["chat_name"]})
    out.sort(key=lambda r: (-(abs(r["pct"]) if r["pct"] is not None else -1), r["project"]))
    return out


def _stale_rows(c, now, days=STALE_DAYS):
    """Usable price-grid and Sheet 2 rows with no approval and no master date within `days`."""
    if not master.current_import(c):
        return []
    cutoff = datetime.datetime.fromtimestamp(now, web.DUBAI).date() - datetime.timedelta(days=days)
    approvals = {r[0]: r[1] for r in c.execute(
        "SELECT row_id, MAX(decided_at) FROM proposals WHERE state='approved' AND row_id IS NOT NULL GROUP BY row_id")}
    dates = {r["row_id"]: json.loads(r["parsed_json"] or "{}") for r in c.execute(
        "SELECT row_id, parsed_json FROM master_cells WHERE field='date'")}
    out = []
    kinds = (ms.GRID, ms.COMMISSION)
    for r in c.execute(f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(master.USABLE))}) "
                       f"AND kind IN (?,?)", (*master.USABLE, *kinds)).fetchall():
        last, basis = None, "never"
        if approvals.get(r["id"]):
            last, basis = datetime.datetime.fromtimestamp(approvals[r["id"]], web.DUBAI).date(), "approval"
        else:
            d = dates.get(r["id"]) or {}
            if d.get("date"):
                try:
                    last, basis = datetime.date.fromisoformat(str(d["date"])[:10]), "master_date"
                except ValueError:
                    pass
        if last and last >= cutoff:
            continue
        out.append({"row_id": r["id"], "project": r["project"], "project_key": r["project_key"],
                    "developer": r["developer"] or "", "sheet": master.home_sheet(r), "row_number": r["row_number"],
                    "last_update": last.isoformat() if last else None, "basis": basis,
                    "days": (cutoff + datetime.timedelta(days=days) - last).days if last else None,
                    "hold": (dates.get(r["id"]) or {}).get("row_status") or None})
    return out


def ins_stale(c, since, now):
    rows_ = _stale_rows(c, now)
    chats = {}
    for r in c.execute(f"SELECT p.project_key, p.row_id, {CHAT_SQL} chat_jid FROM proposals p"):
        if r["chat_jid"]:
            for key in (("row", r["row_id"]), ("key", r["project_key"])):
                bucket = chats.setdefault(key, {})
                bucket[r["chat_jid"]] = bucket.get(r["chat_jid"], 0) + 1
    names = {r["jid"]: r["name"] for r in c.execute("SELECT jid, name FROM groups")}
    for r in rows_:
        votes = chats.get(("row", r["row_id"])) or chats.get(("key", r["project_key"])) or {}
        jid = max(votes, key=votes.get) if votes else None
        r["chat_jid"], r["chat_name"] = jid, (names.get(jid) or jid) if jid else None
        r.pop("project_key")
    rows_.sort(key=lambda r: (r["last_update"] or "", r["project"]))
    return {"days": STALE_DAYS, "count": len(rows_), "rows": rows_[:200]}


def ins_throughput(c, since, now):
    days = {}
    start = datetime.datetime.fromtimestamp(since, web.DUBAI).date()
    end = datetime.datetime.fromtimestamp(now, web.DUBAI).date()
    d = start
    while d <= end:
        days[d.isoformat()] = {"day": d.isoformat(), "created": 0, "approved": 0, "rejected": 0}
        d += datetime.timedelta(days=1)
    for r in c.execute("SELECT created_at FROM proposals WHERE created_at>=?", (since,)):
        b = days.get(_day(r[0]))
        if b is not None:
            b["created"] += 1
    for r in c.execute("SELECT state, decided_at FROM proposals WHERE state IN ('approved','rejected') "
                       "AND decided_at>=?", (since,)):
        b = days.get(_day(r[1]))
        if b is not None:
            b[r[0]] += 1
    return list(days.values())


def ins_rows_to_place(c, since, now):
    return {"count": c.execute("SELECT COUNT(*) FROM master_issues WHERE state='open'").fetchone()[0],
            "first": master_issues("open")[:5]}


def ins_chats(c, since, now):
    names = {r["jid"]: r["name"] for r in c.execute("SELECT jid, name FROM groups")}
    opened = open_by_chat(c)
    synced = {r["jid"]: r["synced_at"] for r in c.execute("SELECT jid, synced_at FROM chat_syncs")}
    scoped = {r[0] for r in c.execute("SELECT jid FROM group_scope WHERE in_scope=1")}
    act = c.execute(f"SELECT m.group_jid jid, COUNT(*) n FROM source_messages m WHERE m.ts>=? AND {VISIBLE} "
                    f"GROUP BY m.group_jid ORDER BY n DESC", (since,)).fetchall()
    return {"total_messages": sum(r["n"] for r in act), "active_chats": len(act),
            "top": [{"jid": r["jid"], "name": names.get(r["jid"]) or history.chat_name(r["jid"], None),
                     "messages": r["n"], "open_suggestions": opened.get(r["jid"], 0), "in_scope": r["jid"] in scoped,
                     "last_sync_at": synced.get(r["jid"])} for r in act[:10]]}


INSIGHTS = {"summary": ins_summary, "changes": ins_changes, "price_moves": ins_price_moves, "stale": ins_stale,
            "throughput": ins_throughput, "rows_to_place": ins_rows_to_place, "chats": ins_chats}


@router.get("/insights")
def insights(days: int = 7, sections: str = ""):
    """Each section is computed on its own: one failing returns {errors: {section: text}} and the rest still render.
    sections: comma list to compute only some (a section's Retry)."""
    days = max(1, min(days, 366))
    now = int(time.time())
    since = int((datetime.datetime.fromtimestamp(now, web.DUBAI).replace(hour=0, minute=0, second=0, microsecond=0)
                 - datetime.timedelta(days=days - 1)).timestamp())
    wanted = [s for s in sections.split(",") if s in INSIGHTS] or list(INSIGHTS)
    out, errors = {}, {}
    with db.connect() as c:
        has_master = master.current_import(c) is not None
        open_n = c.execute("SELECT COUNT(*) FROM proposals WHERE state='proposed'").fetchone()[0]
        for name in wanted:
            try:
                out[name] = INSIGHTS[name](c, since, now)
            except Exception as e:  # keep the other sections
                errors[name] = f"{type(e).__name__}: {e}"[:300]
    return {"days": days, "from": since, "to": now, "has_master": has_master, "sections": out, "errors": errors,
            "exports": {"team": "/api/master/export/team", "sql": "/api/master/export/sql",
                        "open_suggestions": open_n}}


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
                     phase_label=d.get("phase_label", ""), developer=d.get("developer", ""))
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
        return source_context(c, kind[0] if kind[0] in ("pdf_page", "image") else "row", str(row_id))


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
