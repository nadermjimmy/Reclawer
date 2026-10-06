"""Password-protected web UI: run the pipeline, browse the inventory, work the review queue."""
import datetime, hashlib, hmac, json, os, time
from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import build, db, facts, history, jobs, mapping, media_review, parse_files, rows, rules

PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET = (os.getenv("SESSION_SECRET") or PASSWORD or "unset").encode()
COOKIE = "bridge_session"
PUBLIC = ("/login", "/health", "/webhook/", "/sync-groups/", "/static/")
DUBAI = datetime.timezone(datetime.timedelta(hours=4))

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def fmt_ts(ts):
    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(int(ts), DUBAI).strftime("%d %b %Y %H:%M")


def fmt_money(v):
    return "—" if v is None else f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


templates.env.filters["ts"] = fmt_ts
templates.env.filters["money"] = fmt_money
templates.env.filters["label"] = lambda s: (s or "").replace("_", " ")
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else {}


def _sign(exp):
    return hmac.new(SECRET, str(exp).encode(), hashlib.sha256).hexdigest()


def logged_in(request):
    token = request.cookies.get(COOKIE, "")
    exp, _, sig = token.partition(".")
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp))


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith(PUBLIC) or path == "/login":
        return await call_next(request)
    if not PASSWORD:
        return HTMLResponse("Set APP_PASSWORD on the receiver service to enable the web app.", 503)
    if not logged_in(request):
        return RedirectResponse("/login", 303)
    return await call_next(request)


def page(request, template, **ctx):
    with db.connect() as c:
        ctx["open_issues"] = c.execute("SELECT COUNT(*) FROM review_issues WHERE severity IN ('high','medium')"
                                       ).fetchone()[0]
    ctx["job_running"] = jobs.running()
    return templates.TemplateResponse(request, template, ctx)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
def login(password: str = Form(...)):
    if not PASSWORD or not hmac.compare_digest(password, PASSWORD):
        time.sleep(1)
        return RedirectResponse("/login?error=1", 303)
    exp = int(time.time()) + 14 * 86400
    r = RedirectResponse("/", 303)
    r.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=14 * 86400, httponly=True, secure=True, samesite="lax")
    return r


@router.get("/logout")
def logout():
    r = RedirectResponse("/login", 303)
    r.delete_cookie(COOKIE)
    return r


# ---------- dashboard & jobs ----------
STEPS = [
    ("sync_groups", "1. Sync groups", "Fetch the list of WhatsApp groups from Evolution.", history.sync_groups),
    ("pull_history", "2. Pull history", "Copy every group's stored messages into the bridge.", history.pull_history),
    ("download_media", "3. Download attachments", "Fetch workbooks, PDFs and images from the in-scope groups.",
     history.download_media),
    ("parse_files", "4. Read files", "Read every workbook/CSV/PDF cell by cell.", parse_files.parse_all),
    ("map_projects", "5. Map projects (Claude)", "Propose the parent project / phase for each table.",
     mapping.propose),
    ("extract_facts", "6. Extract facts (Claude)", "Find location, service charge, payment plan, completion, "
     "amenities, handover and status statements.", facts.extract_all),
    ("classify_images", "7. Review images (Claude)", "Propose what each image shows (shown only after you approve).",
     media_review.classify),
    ("build", "8. Build inventory", "Rebuild projects, phases, units and the review queue.", build.rebuild),
]
STEP_FN = {s[0]: (s[1], s[3]) for s in STEPS}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db.connect() as c:
        recent = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 8").fetchall()
        counts = {
            "groups": c.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
            "in_scope": len(db.scoped_groups(c)),
            "messages": c.execute("SELECT COUNT(*) FROM source_messages").fetchone()[0],
            "files": c.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
            "projects": c.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "units": c.execute("SELECT COUNT(*) FROM units").fetchone()[0],
        }
    return page(request, "dashboard.html", steps=STEPS, recent=recent, counts=counts)


@router.post("/jobs/{name}")
def start_job(name: str, redo: str = Form("")):
    if name not in STEP_FN:
        return RedirectResponse("/", 303)
    title, fn = STEP_FN[name]
    args = (True,) if redo and name in ("map_projects", "parse_files", "download_media") else ()
    job_id = jobs.start(title, fn, *args)
    return RedirectResponse(f"/jobs/{job_id}" if job_id else "/?busy=1", 303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_view(request: Request, job_id: int):
    with db.connect() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return page(request, "job.html", job=job)


# ---------- groups ----------
@router.get("/groups", response_class=HTMLResponse)
def groups(request: Request):
    with db.connect() as c:
        gs = c.execute("SELECT g.jid, g.name, COALESCE(s.in_scope,0) in_scope, "
                       "(SELECT COUNT(*) FROM source_messages m WHERE m.group_jid=g.jid) n, "
                       "(SELECT MIN(ts) FROM source_messages m WHERE m.group_jid=g.jid) first_ts "
                       "FROM groups g LEFT JOIN group_scope s ON s.jid=g.jid ORDER BY in_scope DESC, n DESC").fetchall()
    return page(request, "groups.html", groups=gs)


@router.post("/groups")
async def save_groups(request: Request):
    form = await request.form()
    chosen = set(form.getlist("scope"))
    with db.connect() as c:
        for (jid,) in c.execute("SELECT jid FROM groups").fetchall():
            c.execute("INSERT INTO group_scope(jid,in_scope) VALUES(?,?) ON CONFLICT(jid) DO UPDATE SET "
                      "in_scope=excluded.in_scope", (jid, 1 if jid in chosen else 0))
    return RedirectResponse("/groups", 303)


@router.get("/groups/{jid}", response_class=HTMLResponse)
def group_messages(request: Request, jid: str, q: str = "", before: int = 0):
    sql = "SELECT * FROM source_messages WHERE group_jid=?"
    args = [jid]
    if q:
        sql += " AND (text LIKE ? OR file_name LIKE ? OR sender_name LIKE ?)"
        args += [f"%{q}%"] * 3
    if before:
        sql += " AND ts<?"
        args.append(before)
    with db.connect() as c:
        msgs = c.execute(sql + " ORDER BY ts DESC LIMIT 200", args).fetchall()
        name = (c.execute("SELECT name FROM groups WHERE jid=?", (jid,)).fetchone() or [jid])[0]
        files = {r["wa_id"]: r["file_id"] for r in c.execute(
            "SELECT o.wa_id, o.file_id FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
            "WHERE m.group_jid=?", (jid,))}
    return page(request, "messages.html", msgs=msgs, name=name, jid=jid, q=q, files=files)


# ---------- inventory ----------
def fact_summary(rows_):
    """Current display state for one fact type (never filled in when the group is silent)."""
    live = [f for f in rows_ if f["validation_status"] != "rejected"]
    if not live:
        return {"state": "not_supplied", "value": None, "items": rows_}
    live.sort(key=lambda f: f["observed_ts"] or 0)
    latest = live[-1]
    values = {(f["parsed_value"] or f["raw_value"]).strip().lower() for f in live}
    state = latest["validation_status"]
    if len(values) > 1 and not latest["updates_previous"]:
        state = "conflict"
    return {"state": state, "value": latest["parsed_value"] or latest["raw_value"], "items": rows_}


@router.get("/projects", response_class=HTMLResponse)
def projects(request: Request, q: str = ""):
    with db.connect() as c:
        ps = c.execute(
            "SELECT p.slug, p.name, d.name developer, "
            "(SELECT COUNT(*) FROM project_phases f WHERE f.project_id=p.id) phases, "
            "(SELECT COUNT(*) FROM units u WHERE u.project_id=p.id) units "
            "FROM projects p LEFT JOIN developers d ON d.id=p.developer_id "
            "WHERE p.name LIKE ? ORDER BY p.name", (f"%{q}%",)).fetchall()
    return page(request, "projects.html", projects=ps, q=q)


def latest_snapshots(c, where, args):
    """Most recent observation per unit."""
    return c.execute(
        "SELECT s.*, u.unit_code, u.phase_key, p.name project, p.slug FROM unit_snapshots s "
        "JOIN units u ON u.id=s.unit_id JOIN projects p ON p.id=u.project_id "
        f"WHERE {where} AND s.id=(SELECT s2.id FROM unit_snapshots s2 WHERE s2.unit_id=s.unit_id "
        "ORDER BY COALESCE(s2.snapshot_ts,0) DESC, s2.id DESC LIMIT 1) "
        "ORDER BY p.name, u.phase_key, u.unit_code", args).fetchall()


@router.get("/projects/{slug}", response_class=HTMLResponse)
def project(request: Request, slug: str):
    with db.connect() as c:
        p = c.execute("SELECT p.*, d.name developer FROM projects p LEFT JOIN developers d ON d.id=p.developer_id "
                      "WHERE slug=?", (slug,)).fetchone()
        if not p:
            return RedirectResponse("/projects", 303)
        fs = c.execute("SELECT a.* FROM fact_assertions a JOIN fact_links l ON l.fact_id=a.id "
                       "WHERE l.project_id=? ORDER BY a.observed_ts", (p["id"],)).fetchall()
        by_type = {}
        for f in fs:
            by_type.setdefault(f["fact_type"], []).append(f)
        key_facts = [(t, fact_summary(by_type.get(t, []))) for t in build.REQUIRED_FACTS]
        phase_facts = [f for f in by_type.get("phase_status", [])]
        other = [f for t in ("launch", "unit_status", "other") for f in by_type.get(t, [])]
        phases = c.execute("SELECT * FROM project_phases WHERE project_id=? ORDER BY raw_label", (p["id"],)).fetchall()
        units = latest_snapshots(c, "u.project_id=?", (p["id"],))
        images = c.execute("SELECT m.*, f.id fid FROM media_assignments m JOIN source_files f ON f.id=m.file_id "
                           "WHERE m.review_state='approved' AND m.project_name IN "
                           "(SELECT raw_label FROM project_aliases WHERE project_id=? UNION SELECT ?)",
                           (p["id"], p["name"])).fetchall()
        history_n = c.execute("SELECT COUNT(*) FROM unit_snapshots s JOIN units u ON u.id=s.unit_id "
                              "WHERE u.project_id=?", (p["id"],)).fetchone()[0]
    return page(request, "project.html", p=p, key_facts=key_facts, phase_facts=phase_facts, other=other,
                phases=phases, units=units, images=images, history_n=history_n)


@router.get("/units", response_class=HTMLResponse)
def units(request: Request, q: str = "", status: str = "", max_price: str = ""):
    where, args = ["1=1"], []
    if q:
        where.append("(u.unit_code LIKE ? OR p.name LIKE ? OR u.phase_key LIKE ? OR s.unit_type_raw LIKE ?)")
        args += [f"%{q}%"] * 4
    if status:
        where.append("s.unit_status=?")
        args.append(status)
    with db.connect() as c:
        res = latest_snapshots(c, " AND ".join(where), args)
    if max_price.replace(",", "").isdigit():
        mp = float(max_price.replace(",", ""))
        res = [r for r in res if r["price_value"] is not None and r["price_value"] <= mp]
    return page(request, "units.html", units=res[:2000], total=len(res), q=q, status=status, max_price=max_price)


@router.get("/units/{unit_id}", response_class=HTMLResponse)
def unit_history(request: Request, unit_id: int):
    with db.connect() as c:
        snaps = c.execute("SELECT s.*, r.file_id, r.sheet, r.row_number, f.file_name FROM unit_snapshots s "
                          "JOIN source_rows r ON r.id=s.source_row_id JOIN source_files f ON f.id=r.file_id "
                          "WHERE s.unit_id=? ORDER BY COALESCE(s.snapshot_ts,0) DESC", (unit_id,)).fetchall()
        u = c.execute("SELECT u.*, p.name project, p.slug FROM units u JOIN projects p ON p.id=u.project_id "
                      "WHERE u.id=?", (unit_id,)).fetchone()
    return page(request, "unit.html", u=u, snaps=snaps)


@router.post("/facts/{fact_id}")
def set_fact(fact_id: int, status: str = Form(...), back: str = Form("/")):
    if status in ("verified_from_source", "needs_validation", "source_stated_unverified", "rejected"):
        with db.connect() as c:
            c.execute("UPDATE fact_assertions SET validation_status=? WHERE id=?", (status, fact_id))
    return RedirectResponse(back, 303)


# ---------- review ----------
@router.get("/review", response_class=HTMLResponse)
def review(request: Request, kind: str = ""):
    with db.connect() as c:
        kinds = c.execute("SELECT issue_type, severity, COUNT(*) n FROM review_issues GROUP BY issue_type, severity "
                          "ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END").fetchall()
        sql = "SELECT * FROM review_issues"
        args = ()
        if kind:
            sql += " WHERE issue_type=?"
            args = (kind,)
        issues = c.execute(sql + " ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id "
                           "LIMIT 500", args).fetchall()
        pending_maps = c.execute("SELECT COUNT(*) FROM label_mappings WHERE review_state='proposed'").fetchone()[0]
        pending_imgs = c.execute("SELECT COUNT(*) FROM media_assignments WHERE review_state IN ('proposed','withheld')"
                                 ).fetchone()[0]
    return page(request, "review.html", kinds=kinds, issues=issues, kind=kind, pending_maps=pending_maps,
                pending_imgs=pending_imgs)


@router.get("/review/mappings", response_class=HTMLResponse)
def review_mappings(request: Request, state: str = "proposed"):
    with db.connect() as c:
        sql = ("SELECT x.*, m.decision, m.developer, m.project_name, m.phase_label, m.confidence, m.rationale, "
               "m.is_uae, m.source, m.review_state, f.file_name FROM label_contexts x "
               "LEFT JOIN label_mappings m ON m.context_key=x.key LEFT JOIN source_files f ON f.id=x.file_id ")
        if state == "all":
            res = c.execute(sql + "ORDER BY f.file_name, x.sheet").fetchall()
        elif state == "unmapped":
            res = c.execute(sql + "WHERE m.context_key IS NULL ORDER BY f.file_name, x.sheet").fetchall()
        else:
            res = c.execute(sql + "WHERE m.review_state=? ORDER BY f.file_name, x.sheet", (state,)).fetchall()
        known = [r[0] for r in c.execute("SELECT DISTINCT project_name FROM label_mappings "
                                         "WHERE decision='project' AND project_name<>'' ORDER BY 1")]
    warn = {x["key"]: rules.why_not_a_project(x["project_name"]) for x in res
            if x["decision"] == "project" and x["project_name"]}
    return page(request, "mappings.html", contexts=res, state=state, known=known, warn=warn)


@router.post("/review/mappings")
def save_mapping(key: str = Form(...), action: str = Form(...), project_name: str = Form(""),
                 phase_label: str = Form(""), developer: str = Form(""), back: str = Form("/review/mappings")):
    with db.connect() as c:
        cur = c.execute("SELECT * FROM label_mappings WHERE context_key=?", (key,)).fetchone()
        if action == "reject":
            c.execute("INSERT INTO label_mappings(context_key,decision,review_state,source,updated_at) "
                      "VALUES(?, 'unresolved', 'rejected', 'reviewer', ?) ON CONFLICT(context_key) DO UPDATE SET "
                      "review_state='rejected', updated_at=excluded.updated_at", (key, int(time.time())))
        elif action == "exclude":
            c.execute("INSERT OR REPLACE INTO label_mappings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (key, "exclude_scope", "", "", "", "high", "Reviewer: outside UAE scope", "no", "reviewer",
                       "approved", int(time.time())))
        else:  # approve, possibly with edits
            c.execute("INSERT OR REPLACE INTO label_mappings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (key, "project", developer.strip() or (cur["developer"] if cur else ""),
                       project_name.strip(), phase_label.strip(), "high",
                       (cur["rationale"] if cur and cur["project_name"] == project_name.strip() else
                        "Set by reviewer"), "yes", "reviewer", "approved", int(time.time())))
    return RedirectResponse(back, 303)


@router.get("/review/context", response_class=HTMLResponse)
def review_context(request: Request, key: str):
    with db.connect() as c:
        x = c.execute("SELECT x.*, f.file_name FROM label_contexts x JOIN source_files f ON f.id=x.file_id "
                      "WHERE key=?", (key,)).fetchone()
        m = c.execute("SELECT * FROM label_mappings WHERE context_key=?", (key,)).fetchone()
        sample = [(r, v) for r, v in rows.data_rows(c, {x["file_id"]}) if rows.context_key(r, v) == key][:50] if x else []
        layout = c.execute("SELECT * FROM sheet_layouts WHERE file_id=? AND sheet=?",
                           (x["file_id"], x["sheet"])).fetchone() if x else None
    return page(request, "context.html", x=x, m=m, sample=sample, layout=layout,
                evidence=json.loads(x["evidence_json"]) if x else {})


@router.post("/layout")
async def save_layout(request: Request):
    form = await request.form()
    file_id, sheet = int(form["file_id"]), form["sheet"]
    with db.connect() as c:
        lay = c.execute("SELECT * FROM sheet_layouts WHERE file_id=? AND sheet=?", (file_id, sheet)).fetchone()
        cols = json.loads(lay["columns_json"])
        for col in cols:
            col["role"] = form.get(f"role_{col['index']}") or None
            col["area_unit"] = form.get(f"unit_{col['index']}", "")
            col["currency"] = form.get(f"cur_{col['index']}", "")
        c.execute("UPDATE sheet_layouts SET columns_json=?, source='reviewer' WHERE file_id=? AND sheet=?",
                  (json.dumps(cols), file_id, sheet))
    return RedirectResponse(form.get("back", "/review"), 303)


@router.get("/review/images", response_class=HTMLResponse)
def review_images(request: Request, state: str = "proposed"):
    with db.connect() as c:
        imgs = c.execute("SELECT m.*, f.file_name FROM media_assignments m JOIN source_files f ON f.id=m.file_id "
                         "WHERE m.review_state=? ORDER BY m.updated_at DESC LIMIT 200", (state,)).fetchall()
    return page(request, "images.html", imgs=imgs, state=state)


@router.post("/review/images")
def save_image(file_id: int = Form(...), action: str = Form(...), project_name: str = Form(""),
               back: str = Form("/review/images")):
    state = {"approve": "approved", "withhold": "withheld", "exclude": "excluded"}.get(action)
    if state:
        with db.connect() as c:
            if project_name.strip():
                c.execute("UPDATE media_assignments SET project_name=? WHERE file_id=?", (project_name.strip(), file_id))
            c.execute("UPDATE media_assignments SET review_state=?, updated_at=? WHERE file_id=?",
                      (state, int(time.time()), file_id))
    return RedirectResponse(back, 303)


# ---------- sources ----------
@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    with db.connect() as c:
        files = c.execute("SELECT f.*, (SELECT COUNT(*) FROM file_occurrences o WHERE o.file_id=f.id) posts, "
                          "(SELECT COUNT(*) FROM source_rows r WHERE r.file_id=f.id) nrows FROM source_files f "
                          "ORDER BY f.kind, f.file_name").fetchall()
        failed = c.execute("SELECT f.*, m.file_name, m.ts, m.group_jid FROM media_fetch f "
                           "JOIN source_messages m ON m.wa_id=f.wa_id WHERE f.status='failed' ORDER BY m.ts DESC"
                           ).fetchall()
    return page(request, "sources.html", files=files, failed=failed)


@router.get("/sources/file/{file_id}")
def source_file(file_id: int):
    with db.connect() as c:
        f = c.execute("SELECT * FROM source_files WHERE id=?", (file_id,)).fetchone()
    if not f or not os.path.exists(f["path"]):
        return HTMLResponse("File not found", 404)
    return FileResponse(f["path"], media_type=f["mimetype"] or None,
                        filename=f["file_name"] or os.path.basename(f["path"]),
                        content_disposition_type="inline" if f["kind"] == "image" else "attachment")


@router.get("/sources/message/{wa_id}", response_class=HTMLResponse)
def source_message(request: Request, wa_id: str):
    with db.connect() as c:
        m = c.execute("SELECT m.*, g.name gname FROM source_messages m LEFT JOIN groups g ON g.jid=m.group_jid "
                      "WHERE wa_id=?", (wa_id,)).fetchone()
        near = c.execute("SELECT * FROM source_messages WHERE group_jid=? AND ts BETWEEN ? AND ? ORDER BY ts",
                         (m["group_jid"], (m["ts"] or 0) - 1800, (m["ts"] or 0) + 1800)).fetchall() if m else []
        files = {r["wa_id"]: r["file_id"] for r in c.execute("SELECT * FROM file_occurrences")}
    return page(request, "message.html", m=m, near=near, files=files)


@router.get("/sources/row/{row_id}", response_class=HTMLResponse)
def source_row(request: Request, row_id: int):
    with db.connect() as c:
        r = c.execute("SELECT r.*, f.file_name FROM source_rows r JOIN source_files f ON f.id=r.file_id "
                      "WHERE r.id=?", (row_id,)).fetchone()
        lay = c.execute("SELECT * FROM sheet_layouts WHERE file_id=? AND sheet=?", (r["file_id"], r["sheet"])
                        ).fetchone() if r else None
        header = None
        if lay:
            h = c.execute("SELECT raw_json FROM source_rows WHERE file_id=? AND sheet=? AND row_number=? "
                          "AND kind='sheet_row'", (r["file_id"], r["sheet"], lay["header_row"])).fetchone()
            header = json.loads(h["raw_json"])["cells"] if h else None
    return page(request, "row.html", r=r, raw=json.loads(r["raw_json"]) if r else {}, header=header)


@router.get("/reconciliation", response_class=HTMLResponse)
def reconciliation(request: Request):
    with db.connect() as c:
        rep = build.reconcile(c)
        per_file = c.execute(
            "SELECT f.file_name, r.file_id, s.mapping_state, COUNT(*) n FROM unit_snapshots s "
            "JOIN source_rows r ON r.id=s.source_row_id JOIN source_files f ON f.id=r.file_id "
            "GROUP BY r.file_id, s.mapping_state ORDER BY f.file_name").fetchall()
    return page(request, "reconciliation.html", rep=rep, per_file=per_file)


@router.get("/review/fact/{fact_id}", response_class=HTMLResponse)
def review_fact(request: Request, fact_id: int):
    with db.connect() as c:
        f = c.execute("SELECT * FROM fact_assertions WHERE id=?", (fact_id,)).fetchone()
    return page(request, "fact.html", f=f)
