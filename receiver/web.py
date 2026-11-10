"""Server side of the admin app: session cookie, auth, the pipeline step list, stored files, and redirects from
the old page URLs into the app (/app). All screens live in the React app (frontend/); data comes from api.py."""
import datetime, hashlib, hmac, os, time
from urllib.parse import urlencode
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import brain, db, proposals

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
def _eval_job(log):
    from eval import run_eval
    run_eval.run_job(log)


def _requeue_noise(log):
    import noise
    noise.requeue(log)


def _retrain_job(log):
    import retrain
    retrain.run(log, force=True, reason="manual")


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
    ("requeue_noise", "Re-read skipped messages", "Put every text message the noise filter skipped back in the "
     "queue; the next sheet-update read sends them to Claude.", _requeue_noise),
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
