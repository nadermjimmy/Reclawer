"""Server side of the admin app: session cookie, auth, the pipeline step list, stored files, and redirects from
the old page URLs into the app (/app). All screens live in the React app (frontend/); data comes from api.py."""
import datetime, hashlib, hmac, os, time
from urllib.parse import urlencode
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import brain, db, proposals

PASSWORD = os.getenv("APP_PASSWORD", "")
TEAM_PASSWORD = os.getenv("APP_TEAM_PASSWORD", "")  # optional: the team logs in with this and sees the simple app
SECRET = (os.getenv("SESSION_SECRET") or PASSWORD or "unset").encode()
COOKIE = "bridge_session"
PUBLIC = ("/health", "/webhook/", "/sync-groups/", "/static/")
DUBAI = datetime.timezone(datetime.timedelta(hours=4))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter()


def _sign(exp):
    return hmac.new(SECRET, str(exp).encode(), hashlib.sha256).hexdigest()


def _sign_role(exp, role):
    return _sign(exp) if role == "admin" else hmac.new(SECRET, f"{exp}.{role}".encode(), hashlib.sha256).hexdigest()


def role_of(request):
    """'admin' or 'team' for a valid session, else None (older cookies without a role are admin)."""
    parts = request.cookies.get(COOKIE, "").split(".")
    if len(parts) == 2:
        exp, role, sig = parts[0], "admin", parts[1]
    elif len(parts) == 3:
        exp, role, sig = parts
    else:
        return None
    if role not in ("admin", "team") or not exp.isdigit() or int(exp) <= time.time():
        return None
    return role if hmac.compare_digest(sig, _sign_role(exp, role)) else None


def logged_in(request):
    return role_of(request) is not None


# the team login can review and decide, not change the system
ADMIN_ONLY = (("POST", "/api/reset"), ("POST", "/api/master"), ("POST", "/api/catalog"), ("PUT", "/api/settings/"),
              ("POST", "/api/jobs/"), ("POST", "/api/master/issues/"))


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
    role = role_of(request)
    if not role:
        return JSONResponse({"error": "login"}, 401) if is_api else RedirectResponse("/app", 303)
    if role == "team" and any(request.method == m and (path == p or (p.endswith("/") and path.startswith(p)))
                              for m, p in ADMIN_ONLY):
        return JSONResponse({"error": "admin_only", "detail": "Only the admin login can do this."}, 403)
    return await call_next(request)


def password_ok(password):
    """The role this password logs in as ('admin' / 'team'), or None."""
    if PASSWORD and hmac.compare_digest(password or "", PASSWORD):
        return "admin"
    if TEAM_PASSWORD and hmac.compare_digest(password or "", TEAM_PASSWORD):
        return "team"
    return None


def set_session(r, role="admin"):
    """Attach a signed 14-day session cookie."""
    exp = int(time.time()) + 14 * 86400
    token = f"{exp}.{_sign(exp)}" if role == "admin" else f"{exp}.{role}.{_sign_role(exp, role)}"
    r.set_cookie(COOKIE, token, max_age=14 * 86400, httponly=True, secure=True, samesite="lax")
    return r


# ---------- pipeline steps (run from More > Pipeline) ----------
def _eval_job(log):
    from eval import run_eval
    run_eval.run_job(log)


def _requeue_noise(log):
    import noise
    noise.requeue(log)


def _retrain_job(log):
    import retrain
    retrain.run(log, force=True, reason="manual")


def _rematch(log):
    import rematch
    rematch.run(log)


def _gate_sort(log):
    import gate
    gate.run(log)


def _lessons(log):
    import lessons
    lessons.mine(log)


def _uae_preview(log):
    import market_scope
    market_scope.apply(log, dry_run=True)


def _uae_untick(log):
    import market_scope
    market_scope.apply(log)


def _uae_undo(log):
    import market_scope
    market_scope.undo(log)


def _free_space(log):
    import history
    history.free_space(log)


def _rule_suggestions_job(log):
    import rule_suggestions
    rule_suggestions.run(log)


STEPS = [
    # chat steps (sync chats, pull history, download attachments, read files) run inside "Sync all chats"
    ("propose_updates", "Propose sheet updates (Claude)", "Read every group's text messages first, then "
     "spreadsheets, PDFs and images for what's still missing; sent through the batch queue at half price, results "
     "arrive on the Updates page as each stage finishes.", proposals.propose),
    ("brain_review", "Brain review (Claude)", "Second look at open suggestions: moves false new rows onto "
     "existing rows, approves safe updates (existing row, sheet format, price within ±20%, no Hold/Hide, never "
     "commission) and rejects clear noise. Runs after every sync; run it here for the backlog.", brain.review),
    ("eval_report", "Eval: brain report (no LLM)", "Freeze every decision a person made into a gold set and measure "
     "matching, noise, accepted values, auto-approve precision and API cost per source kind. Free: no Claude calls.",
     _eval_job),
    ("retrain", "Retrain matcher + noise filter (no LLM)", "Train both models on every person decision, re-run the "
     "eval and promote a model only if it does at least as well. Runs nightly by itself when 25+ new labels exist.",
     _retrain_job),
    ("rematch", "Re-match suggestions (no LLM)", "Point every open suggestion (and the ones the last master upload "
     "closed) at the current master; with the accuracy gate on, each stays with its group's own developer.",
     _rematch),
    ("gate_sort", "Sort into queues (no LLM)", "Put every open suggestion in Ready / Check / New / Hidden with the "
     "reasons, using the catalog, the team's sheet, the lessons and the approval model.", _gate_sort),
    ("lessons", "Learn lessons (no LLM unless LESSONS_LLM=1)", "Recompute what the team's decisions teach per group, "
     "developer and column.", _lessons),
    ("requeue_noise", "Re-read skipped messages", "Put every text message the noise filter skipped back in the "
     "queue; the next sheet-update read sends them to Claude.", _requeue_noise),
    ("uae_preview", "Preview UAE developer chats (no change)", "List the groups of UAE developers "
     "(uae_developers.json) that would be unticked, and the Emaar / Modon / Reportage-style groups kept because they "
     "talk about Egypt. Changes nothing.", _uae_preview),
    ("uae_untick", "Untick UAE developer chats", "Untick UAE developers' groups so they aren't pulled or read. "
     "Developers selling in both markets are decided per chat from its messages; chats a person set and chats the "
     "team assigned are never touched. New groups are checked on every sync.", _uae_untick),
    ("uae_undo", "Re-tick UAE groups", "Undo: tick again every group the UAE rule unticked.", _uae_undo),
    ("free_space", "Free space (no LLM)", "Delete downloaded attachments posted only before the reading window "
     "(INGEST_DAYS) and the raw WhatsApp records of old messages. Message text, parsed sheet cells, PDF text, "
     "suggestions and decisions are kept.", _free_space),
    ("rule_suggestions", "Rule suggestions (no LLM)", "Suggest owner-rule additions from what people rejected. "
     "Suggestions only: nothing is applied.", _rule_suggestions_job),
]
STEP_FN = {s[0]: (s[1], s[3]) for s in STEPS}


# ---------- stored files (opened from the app) ----------
@router.get("/sources/file/{file_id}")
def source_file(file_id: int, download: int = 0):
    with db.connect() as c:
        f = c.execute("SELECT * FROM source_files WHERE id=?", (file_id,)).fetchone()
    if not f or not f["path"] or not os.path.exists(f["path"]):
        return HTMLResponse("File not found", 404)
    return FileResponse(f["path"], media_type=f["mimetype"] or None,
                        filename=f["file_name"] or os.path.basename(f["path"]),
                        content_disposition_type="inline" if f["kind"] in ("image", "pdf") and not download
                        else "attachment")


# ---------- old page URLs: the app maps each one to its new screen ----------
OLD_PAGES = ("/", "/login", "/logout", "/dashboard", "/feed", "/groups", "/sources", "/master", "/updates")
OLD_PREFIXES = ("/groups/", "/sources/message/", "/sources/row/", "/jobs/")


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
