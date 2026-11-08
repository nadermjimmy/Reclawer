"""Server side of the admin app: session cookie, auth, the pipeline step list, stored files, and redirects from
the old page URLs into the app (/app). All screens live in the React app (frontend/); data comes from api.py."""
import datetime, hashlib, hmac, os, time
from urllib.parse import urlencode
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import brain, build, db, facts, history, mapping, media_review, parse_files, proposals, rules

PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET = (os.getenv("SESSION_SECRET") or PASSWORD or "unset").encode()
COOKIE = "bridge_session"
PUBLIC = ("/health", "/webhook/", "/sync-groups/", "/static/")
DUBAI = datetime.timezone(datetime.timedelta(hours=4))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter()


def _sign(exp):
    return hmac.new(SECRET, str(exp).encode(), hashlib.sha256).hexdigest()


def logged_in(request):
    token = request.cookies.get(COOKIE, "")
    exp, _, sig = token.partition(".")
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp))


def _spa_shell(path):
    """The app's HTML/JS/CSS holds no data, so it loads without a session and shows its own login."""
    return path == "/app" or path.startswith("/app/")


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith(PUBLIC) or path in ("/api/login", "/api/logout") or _spa_shell(path) \
            or (path in OLD_PAGES or path.startswith(OLD_PREFIXES)) and request.method == "GET":
        return await call_next(request)
    is_api = path.startswith("/api/")
    if not PASSWORD:
        msg = "Set APP_PASSWORD on the receiver service to enable the web app."
        return JSONResponse({"error": "no_password", "detail": msg}, 503) if is_api else HTMLResponse(msg, 503)
    if not logged_in(request):
        return JSONResponse({"error": "login"}, 401) if is_api else RedirectResponse("/app", 303)
    return await call_next(request)


def password_ok(password):
    return bool(PASSWORD) and hmac.compare_digest(password or "", PASSWORD)


def set_session(r):
    """Attach a signed 14-day session cookie."""
    exp = int(time.time()) + 14 * 86400
    r.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=14 * 86400, httponly=True, secure=True, samesite="lax")
    return r


# ---------- pipeline steps (run from More > Pipeline) ----------
STEPS = [
    ("sync_groups", "1. Sync chats", "Fetch the list of groups and private chats from Evolution.",
     history.sync_groups),
    ("pull_history", "2. Pull history", "Copy the ticked chats' stored messages into the app.", history.pull_history),
    ("download_media", "3. Download attachments", "Fetch workbooks, PDFs and images from the ticked chats.",
     history.download_media),
    ("parse_files", "4. Read files", "Read every workbook/CSV/PDF cell by cell.", parse_files.parse_all),
    ("map_projects", "5. Map projects (Claude)", "Propose the parent project / phase for each table.",
     mapping.propose),
    ("extract_facts", "6. Extract facts (Claude)", "Find location, service charge, payment plan, completion, "
     "amenities, handover and status statements.", facts.extract_all),
    ("propose_updates", "6b. Propose sheet updates (Claude)", "Read every group's text messages first, then "
     "spreadsheets, PDFs and images for what's still missing; sent through the batch queue at half price, results "
     "arrive on the Updates page as each stage finishes.", proposals.propose),
    ("brain_review", "6c. Brain review (Claude)", "Second look at open suggestions: moves false new rows onto "
     "existing rows, approves safe updates (existing row, sheet format, price within ±20%, no Hold/Hide, never "
     "commission) and rejects clear noise. Runs after every sync; run it here for the backlog.", brain.review),
    ("classify_images", "7. Review images (Claude)", "Propose what each image shows (shown only after you approve).",
     media_review.classify),
    ("build", "8. Build inventory", "Rebuild projects, phases, units and the review queue.", build.rebuild),
]
STEP_FN = {s[0]: (s[1], s[3]) for s in STEPS}


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


def latest_snapshots(c, where, args):
    """Most recent observation per unit."""
    return c.execute(
        "SELECT s.*, u.unit_code, u.phase_key, p.name project, p.slug FROM unit_snapshots s "
        "JOIN units u ON u.id=s.unit_id JOIN projects p ON p.id=u.project_id "
        f"WHERE {where} AND s.id=(SELECT s2.id FROM unit_snapshots s2 WHERE s2.unit_id=s.unit_id "
        "ORDER BY COALESCE(s2.snapshot_ts,0) DESC, s2.id DESC LIMIT 1) "
        "ORDER BY p.name, u.phase_key, u.unit_code", args).fetchall()


def save_mapping(key, action, project_name="", phase_label="", developer=""):
    """Reviewer decision on a table's project mapping (approve, possibly with edits / reject / exclude)."""
    with db.connect() as c:
        cur = c.execute("SELECT * FROM label_mappings WHERE context_key=?", (key,)).fetchone()
        if action == "reject":
            c.execute("INSERT INTO label_mappings(context_key,decision,review_state,source,updated_at) "
                      "VALUES(?, 'unresolved', 'rejected', 'reviewer', ?) ON CONFLICT(context_key) DO UPDATE SET "
                      "review_state='rejected', updated_at=excluded.updated_at", (key, int(time.time())))
        elif action == "exclude":
            c.execute("INSERT OR REPLACE INTO label_mappings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (key, "exclude_scope", "", "", "", "high", f"Reviewer: outside {rules.MARKET_NAME} scope", "no",
                       "reviewer", "approved", int(time.time())))
        else:
            c.execute("INSERT OR REPLACE INTO label_mappings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (key, "project", developer.strip() or (cur["developer"] if cur else ""),
                       project_name.strip(), phase_label.strip(), "high",
                       (cur["rationale"] if cur and cur["project_name"] == project_name.strip() else
                        "Set by reviewer"), "yes", "reviewer", "approved", int(time.time())))


# ---------- stored files (opened from the app) ----------
@router.get("/sources/file/{file_id}")
def source_file(file_id: int, download: int = 0):
    with db.connect() as c:
        f = c.execute("SELECT * FROM source_files WHERE id=?", (file_id,)).fetchone()
    if not f or not os.path.exists(f["path"]):
        return HTMLResponse("File not found", 404)
    return FileResponse(f["path"], media_type=f["mimetype"] or None,
                        filename=f["file_name"] or os.path.basename(f["path"]),
                        content_disposition_type="inline" if f["kind"] in ("image", "pdf") and not download
                        else "attachment")


# ---------- old page URLs: the app maps each one to its new screen ----------
OLD_PAGES = ("/", "/login", "/logout", "/dashboard", "/feed", "/groups", "/projects", "/units", "/review",
             "/sources", "/reconciliation", "/master", "/updates")
OLD_PREFIXES = ("/groups/", "/projects/", "/units/", "/review/", "/sources/message/", "/sources/row/", "/jobs/")


@router.get("/")
def home():
    return RedirectResponse("/app", 303)


@router.get("/logout")
def logout():
    r = RedirectResponse("/app", 303)
    r.delete_cookie(COOKIE)
    return r


def _old(request: Request):
    path = request.url.path
    target = "/app" if path in ("/", "/login") else "/app" + path
    q = urlencode(list(request.query_params.multi_items()))
    return RedirectResponse(target + (f"?{q}" if q else ""), 303)


for _p in OLD_PAGES:
    if _p not in ("/", "/logout"):
        router.add_api_route(_p, _old, methods=["GET"], include_in_schema=False)
for _p in OLD_PREFIXES:
    router.add_api_route(_p + "{rest:path}", _old, methods=["GET"], include_in_schema=False)
