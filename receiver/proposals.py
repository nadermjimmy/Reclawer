"""Pipeline step 6b: propose cell updates to the master 'App Updates' workbook, reading sources in a fixed order.

Reading order (owner decision): 1 text messages from every in-scope chat (all groups + ticked private chats),
2 spreadsheet rows, 3 PDFs, 4 images. Each later stage only fills cells that no earlier source filled recently
(master.add_proposal / SOURCE_RANK). Claude reads values in the sheet's own notation; matching to a row, choosing
the sheet and comparing with the current value are done in code.

Models (llm.py): text and spreadsheet rows -> CLAUDE_MODEL; PDFs (sent whole, natively: page images + text, so
scans and tables work) and images (vision) -> CLAUDE_FILE_MODEL.

Two ways to run the same stages:
- live: one chat's Sync now (and tests): every request answered while the job runs.
- batched: the full run (step 6b) goes through the Message Batches API at half price. Each stage is one batch;
  a background poller (start_poller) collects the results, then submits the next stage, so the gap rule sees
  what the earlier stage found. No job lock is held while a batch waits.
"""
import base64, io, json, os, re, threading, time
import db, jobs, llm, master, master_schema as ms, rows

FIELD_HELP = "\n".join([
    "- location: one-letter location code (see codes below)",
    "- ready_to_move: 'rtm' (some units ready), 'all project rtm', a ready unit as '3 bed/16,400,000' or "
    "'area/price', or 'Hold' / 'Hide' for the whole project",
    *[f"- {f}: {ms.UNIT_LABELS[f]} — 'area/price' in sq m and EGP, e.g. '125/6,900,000'; or a status: "
      "so (sold out), de (not offered), rtm, service (serviced apartment), Hold, Hide" for f in ms.UNIT_FIELDS],
    "- payment_plan: down payment and years, e.g. '10% - 8Y', or 'Cash'",
    "- commission: broker commission percent, e.g. '4' or '3.5 net' (keep the word net if stated)",
    "- feedback: a short status note: Launch, Open Inventory, Sold, Hold, or a few words",
])

SYSTEM = f"""You read Egyptian real-estate WhatsApp messages (Arabic, English, Franco-Arabic), price-list rows,
brochure PDFs and images, and list values that belong in the broker team's project sheet (one row per developer
project).

Fields you may fill (value must be written in the sheet's notation):
{FIELD_HELP}

Unit types: Apartment 1..4 = 1..4 bedrooms. S Villa / Duplex, Town(house), Twin(house), Standalone villa,
Office, Clinic, Shop, Pharmacy, Studio. For each project and unit type give the single price the source quotes
(the latest one if the source gives several over time); for a price list give the starting (lowest) unit of
that type in the list. Area in sq m, price in EGP, no currency text in the value.

Location codes: {', '.join(f'{k}={v}' for k, v in ms.LOCATION_CODES.items())}. Use '' if the source doesn't say.

Rules:
- Record only what the source states. No outside knowledge, no guessing, no calculations beyond writing the number.
- project: the project name as written; developer: the developer if stated, else ''.
- If it is unclear which project a value belongs to, skip it.
- Never output salesperson names or phone numbers.
- quote: the exact words/cells the value came from (verbatim).
- Return an empty list if nothing fits."""

FILE_RULES = """This is a file posted in a WhatsApp chat. The chat context below only helps you tell which project the
file is about; never take values from the chat context, only from the file itself. Read tables, scanned pages and
text in images carefully, in Arabic or English."""

SCHEMA = llm.obj({"updates": {"type": "array", "items": llm.obj({
    "source_id": llm.STR, "developer": llm.STR, "project": llm.STR,
    "location_code": {"type": "string", "enum": ["", *ms.LOCATION_CODES]},
    "field": {"type": "string", "enum": ms.ALL_PROPOSABLE},
    "value": llm.STR, "quote": llm.STR})}})

STAGES = [("text", "Text messages"), ("sheets", "Spreadsheets"), ("pdfs", "PDFs"), ("images", "Images")]
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
IMAGE_MAX = 5 * 1024 * 1024        # API limit per image
PDF_PAGES_PER_REQUEST = 20
PDF_MAX = 24 * 1024 * 1024         # keep each request well under the 32 MB request limit
BATCH_MAX = 200 * 1024 * 1024      # keep each batch under the 256 MB batch limit


# ---------- work units ----------
def _unit(uid, stage, label, model, content, sources, keys):
    """sources: {source_id Claude returns: [source_kind, source_ref, ts]}; keys: processed_batches keys."""
    return {"id": uid, "stage": stage, "label": label, "model": model, "content": content,
            "sources": sources, "keys": keys}


def _skip_keys(c, extra=()):
    done = {r[0] for r in c.execute("SELECT batch_key FROM processed_batches")}
    pending = set()
    for r in c.execute("SELECT keys_json FROM ingest_items WHERE state='pending'"):
        pending.update(json.loads(r[0]))
    return done | pending | set(extra)


def _chats(c, jids):
    return db.scoped_groups(c) if jids is None else list(jids)


def _file_ids(c, jids):
    if jids is None:
        return rows.scoped_file_ids(c)
    if not jids:
        return set()
    return {r[0] for r in c.execute(
        "SELECT DISTINCT o.file_id FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
        f"WHERE m.group_jid IN ({','.join('?' * len(jids))})", list(jids))}


def _file_ts(c, file_id):
    return c.execute("SELECT MAX(m.ts) FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
                     "WHERE o.file_id=?", (file_id,)).fetchone()[0]


def _chat_context(c, file_id, around=4):
    """Caption and nearby messages of the file's first posting: only to tell which project the file is about."""
    m = c.execute("SELECT m.* FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
                  "WHERE o.file_id=? ORDER BY m.ts LIMIT 1", (file_id,)).fetchone()
    if not m:
        return {"caption": "", "nearby": []}
    near = c.execute("SELECT text FROM (SELECT text, ts FROM source_messages WHERE group_jid=? AND ts<? AND text<>'' "
                     "ORDER BY ts DESC LIMIT ?) UNION ALL SELECT text FROM (SELECT text, ts FROM source_messages "
                     "WHERE group_jid=? AND ts>? AND text<>'' ORDER BY ts LIMIT ?)",
                     (m["group_jid"], m["ts"], around, m["group_jid"], m["ts"], around)).fetchall()
    return {"caption": (m["text"] or "")[:500], "nearby": [(n[0] or "")[:300] for n in near]}


def text_units(c, jids=None, skip=None, batch=60):
    chats = _chats(c, jids)
    if not chats:
        return []
    skip = _skip_keys(c) if skip is None else skip
    msgs = [m for m in c.execute(
        "SELECT wa_id, ts, text, file_name FROM source_messages "
        f"WHERE group_jid IN ({','.join('?' * len(chats))}) AND text<>'' ORDER BY group_jid, ts", chats)
        if "u:" + m["wa_id"] not in skip]
    out = []
    for i in range(0, len(msgs), batch):
        chunk = msgs[i:i + batch]
        body = {"messages": [{"source_id": m["wa_id"], "time": m["ts"], "text": m["text"],
                              "attached_file": m["file_name"]} for m in chunk]}
        out.append(_unit(f"t{chunk[0]['wa_id']}"[:60], "text", f"messages {i + 1}-{i + len(chunk)}", llm.MODEL,
                         json.dumps(body, ensure_ascii=False),
                         {m["wa_id"]: ["message", m["wa_id"], m["ts"]] for m in chunk},
                         ["u:" + m["wa_id"] for m in chunk]))
    return out


def sheet_units(c, jids=None, skip=None, chunk_rows=150):
    files = _file_ids(c, jids)
    skip = _skip_keys(c) if skip is None else skip
    todo = [r for r in c.execute(
        "SELECT r.id, r.file_id, r.sheet, r.raw_json, f.file_name FROM source_rows r JOIN source_files f "
        "ON f.id=r.file_id WHERE r.kind='sheet_row' ORDER BY r.file_id, r.sheet, r.row_number")
        if r["file_id"] in files and "ur:%d" % r["id"] not in skip]
    groups = {}
    for r in todo:
        groups.setdefault((r["file_id"], r["sheet"]), []).append(r)
    out = []
    for (file_id, sheet), items in groups.items():
        ts = _file_ts(c, file_id)
        for i in range(0, len(items), chunk_rows):
            part = items[i:i + chunk_rows]
            lines = [{"source_id": str(r["id"]), "text": " | ".join(
                cell["v"] for cell in json.loads(r["raw_json"]).get("cells", []) if cell["v"].strip())}
                for r in part]
            lines = [l for l in lines if l["text"].strip()]
            body = {"file": part[0]["file_name"], "sheet": sheet, "rows": lines}
            out.append(_unit(f"s{part[0]['id']}", "sheets", f"{part[0]['file_name']} / {sheet}", llm.MODEL,
                             json.dumps(body, ensure_ascii=False) if lines else None,
                             {str(r["id"]): ["row", str(r["id"]), ts] for r in part},
                             ["ur:%d" % r["id"] for r in part]))
    return out


def _pdf_chunks(path, n_pages):
    """[(first_page, last_page, pdf bytes)] of at most PDF_PAGES_PER_REQUEST pages and PDF_MAX bytes each."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(path)
    n_pages = len(reader.pages)
    if n_pages <= PDF_PAGES_PER_REQUEST and os.path.getsize(path) <= PDF_MAX:
        return [(1, n_pages, open(path, "rb").read())]

    def piece(a, b):
        w = PdfWriter()
        for i in range(a - 1, b):
            w.add_page(reader.pages[i])
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()

    out, stack = [], [(a, min(a + PDF_PAGES_PER_REQUEST - 1, n_pages))
                      for a in range(1, n_pages + 1, PDF_PAGES_PER_REQUEST)]
    while stack:
        a, b = stack.pop(0)
        data = piece(a, b)
        if len(data) <= PDF_MAX:
            out.append((a, b, data))
        elif a < b:
            mid = (a + b) // 2
            stack[:0] = [(a, mid), (mid + 1, b)]
        # a single page over the limit is skipped
    return out


def pdf_units(c, jids=None, skip=None):
    files = _file_ids(c, jids)
    skip = _skip_keys(c) if skip is None else skip
    out = []
    for f in c.execute("SELECT * FROM source_files WHERE kind='pdf' AND parse_status='parsed' ORDER BY id").fetchall():
        if f["id"] not in files or not f["path"] or not os.path.exists(f["path"]):
            continue
        pages = {r["row_number"]: r["id"] for r in c.execute(
            "SELECT id, row_number FROM source_rows WHERE file_id=? AND kind='pdf_page'", (f["id"],))}
        if not pages:
            continue
        if all("up:%d:%d" % (f["id"], a) in skip for a in range(1, max(pages) + 1, PDF_PAGES_PER_REQUEST)):
            continue
        ts, ctx = _file_ts(c, f["id"]), _chat_context(c, f["id"])
        try:
            chunks = _pdf_chunks(f["path"], len(pages))
        except Exception:
            continue
        for a, b, data in chunks:
            key = "up:%d:%d" % (f["id"], a)
            if key in skip:
                continue
            note = (f"{FILE_RULES}\nFile: {f['file_name']} (pages {a}-{b} of {max(pages)}).\n"
                    f"source_id: write 'p' followed by the page number in the ORIGINAL file, e.g. 'p{a}'.\n"
                    f"Chat context: {json.dumps(ctx, ensure_ascii=False)}")
            content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                       "data": base64.standard_b64encode(data).decode()}},
                       {"type": "text", "text": note}]
            out.append(_unit(f"p{f['id']}_{a}", "pdfs", f"{f['file_name']} p{a}-{b}", llm.FILE_MODEL, content,
                             {f"p{n}": ["pdf_page", str(pages[n]), ts] for n in range(a, b + 1) if n in pages},
                             [key]))
    return out


def image_units(c, jids=None, skip=None):
    files = _file_ids(c, jids)
    skip = _skip_keys(c) if skip is None else skip
    out = []
    for r in c.execute("SELECT r.id rid, f.* FROM source_rows r JOIN source_files f ON f.id=r.file_id "
                       "WHERE r.kind='image' ORDER BY f.id").fetchall():
        key = "ui:%d" % r["id"]
        mt = (r["mimetype"] or "").split(";")[0]
        if (r["id"] not in files or key in skip or mt not in IMAGE_TYPES or not r["path"]
                or not os.path.exists(r["path"]) or os.path.getsize(r["path"]) > IMAGE_MAX):
            continue
        ctx = _chat_context(c, r["id"])
        note = (f"{FILE_RULES}\nFile: {r['file_name'] or 'image'}. source_id: write 'img'.\n"
                f"Chat context: {json.dumps(ctx, ensure_ascii=False)}")
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt,
                                                "data": base64.standard_b64encode(open(r["path"], "rb").read()).decode()}},
                   {"type": "text", "text": note}]
        out.append(_unit(f"i{r['id']}", "images", r["file_name"] or f"image {r['id']}", llm.FILE_MODEL, content,
                         {"img": ["image", str(r["rid"]), _file_ts(c, r["id"])]}, [key]))
    return out


BUILDERS = {"text": text_units, "sheets": sheet_units, "pdfs": pdf_units, "images": image_units}


def _store(unit_sources, updates):
    n = 0
    with db.connect() as c:
        for u in updates:
            src = unit_sources.get(u["source_id"])
            if not src:
                continue
            kind, ref, ts = src
            n += master.add_proposal(c, dict(u, source_kind=kind, source_ref=ref, source_ts=ts))
    return n


def _done(keys):
    with db.connect() as c:
        c.executemany("INSERT OR IGNORE INTO processed_batches VALUES(?,?)", [(k, int(time.time())) for k in keys])


# ---------- live ----------
LIVE_WORKERS = int(os.getenv("CLAUDE_WORKERS", "6"))
KEY_HELP = ("Claude rejected the API key (401). Set a valid ANTHROPIC_API_KEY on the receiver service in Railway "
            "(Variables), redeploy, then sync again. Messages and files are still pulled and downloaded.")


def _key_rejected(e):
    import anthropic
    return isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError))


def run_stage_live(log, stage, jids=None):
    """Answer one stage's requests now, several at a time. Returns (proposals written, failed requests)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with db.connect() as c:
        units = BUILDERS[stage](c, jids)
    if units:
        log(f"{dict(STAGES)[stage]}: {len(units)} request(s)")
    written = failed = 0
    empty = [u for u in units if u["content"] is None]
    _done([k for u in empty for k in u["keys"]])
    units = [u for u in units if u["content"] is not None]

    def ask(u):
        return llm.call_json(SYSTEM, u["content"], SCHEMA, model=u["model"])["updates"]

    with ThreadPoolExecutor(LIVE_WORKERS) as pool:
        futures = {pool.submit(ask, u): u for u in units}
        for f in as_completed(futures):  # results are stored one at a time, in this thread
            u = futures[f]
            try:
                out = f.result()
            except Exception as e:
                if _key_rejected(e):  # every other request would fail the same way: stop this stage
                    for other in futures:
                        other.cancel()
                    log(KEY_HELP)
                    return written, failed + 1
                log(f"{u['label']} failed: {e}")
                failed += 1
                continue
            n = _store(u["sources"], out)
            written += n
            _done(u["keys"])
            log(f"{u['label']}: {n} suggestion(s)")
    return written, failed


def run_live(log, jids=None):
    """All four stages in order, answered while the job runs. Returns (written, failed)."""
    written = failed = 0
    for stage, _ in STAGES:
        w, f = run_stage_live(log, stage, jids)
        written += w
        failed += f
    return written, failed


# compatibility for callers that read one kind of source
def from_messages(log, batch=60, jids=None):
    return run_stage_live(log, "text", jids)


def from_files(log, chunk_rows=150, jids=None):
    written = failed = 0
    for stage in ("sheets", "pdfs", "images"):
        w, f = run_stage_live(log, stage, jids)
        written += w
        failed += f
    return written, failed


# ---------- batched full run ----------
def _run_log(run):
    return jobs.Log(run["job_id"])


def _set_run(c, run_id, **kw):
    kw["updated_at"] = int(time.time())
    c.execute(f"UPDATE ingest_runs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), run_id))


def _advance(run_id):
    """Submit the current stage's next batch, or move on to the next stage; finish after images."""
    while True:
        with db.connect() as c:
            run = c.execute("SELECT * FROM ingest_runs WHERE id=?", (run_id,)).fetchone()
            log = _run_log(run)
            if run["stage"] >= len(STAGES):
                totals = json.loads(run["totals_json"] or "{}")
                _set_run(c, run_id, status="done", batch_id=None)
                c.execute("UPDATE jobs SET status='done', finished_at=? WHERE id=?", (int(time.time()), run["job_id"]))
                done = True
            elif run["stage"] >= 1 and jobs.running():
                # files are still being downloaded/read by the job that started this run: read them afterwards
                _set_run(c, run_id, status="files_pending", batch_id=None)
                return
            else:
                done = False
                stage, label = STAGES[run["stage"]]
                failed_keys = set()
                for r in c.execute("SELECT keys_json FROM ingest_items WHERE run_id=? AND state='failed'", (run_id,)):
                    failed_keys.update(json.loads(r[0]))  # don't retry a failed request within the same run
                units = BUILDERS[stage](c, None, _skip_keys(c, failed_keys))
        if done:
            log(f"Finished: {totals.get('suggestions', 0)} suggestion(s), {totals.get('failed', 0)} failed request(s)")
            log("RESULT " + json.dumps(totals))
            return
        empty = [u for u in units if u["content"] is None]
        if empty:
            _done([k for u in empty for k in u["keys"]])
        units = [u for u in units if u["content"] is not None]
        if not units:
            with db.connect() as c:
                _set_run(c, run_id, stage=run["stage"] + 1)
            log(f"{label}: nothing new")
            continue
        picked, size = [], 0
        for u in units:  # one batch per call; what doesn't fit goes in the next batch of the same stage
            s = len(json.dumps(u["content"], ensure_ascii=False))
            if picked and size + s > BATCH_MAX:
                break
            picked.append(u)
            size += s
        reqs = [(re.sub(r"[^A-Za-z0-9_-]", "_", f"r{run_id}_{u['id']}")[:64],
                 llm.params(SYSTEM, u["content"], SCHEMA, model=u["model"])) for u in picked]
        batch_id = llm.batch_submit(reqs)
        with db.connect() as c:
            for (cid, _), u in zip(reqs, picked):
                c.execute("INSERT OR REPLACE INTO ingest_items VALUES(?,?,?,?,?,?,?,'pending')",
                          (cid, run_id, batch_id, stage, u["label"], json.dumps(u["sources"]), json.dumps(u["keys"])))
            _set_run(c, run_id, batch_id=batch_id, status="waiting")
        log(f"PROGRESS " + json.dumps({"stage": run["stage"] + 1, "of": len(STAGES), "label": label,
                                       "done": run["stage"], "total": len(STAGES)}))
        log(f"{label}: sent {len(picked)} request(s) to the batch queue ({batch_id}); results usually within an hour")
        return


def _collect(run):
    log = _run_log(run)
    written = failed = 0
    seen = set()
    for cid, payload, error in llm.batch_results(run["batch_id"]):
        with db.connect() as c:
            item = c.execute("SELECT * FROM ingest_items WHERE custom_id=?", (cid,)).fetchone()
        if not item:
            continue
        seen.add(cid)
        if payload is None:
            failed += 1
            log(f"{item['label']} failed: {error}")
            state = "failed"
        else:
            written += _store(json.loads(item["sources_json"]), payload.get("updates", []))
            _done(json.loads(item["keys_json"]))
            state = "done"
        with db.connect() as c:
            c.execute("UPDATE ingest_items SET state=? WHERE custom_id=?", (state, cid))
    with db.connect() as c:
        c.execute("UPDATE ingest_items SET state='failed' WHERE batch_id=? AND state='pending'", (run["batch_id"],))
        totals = json.loads(run["totals_json"] or "{}")
        totals["suggestions"] = totals.get("suggestions", 0) + written
        totals["failed"] = totals.get("failed", 0) + failed + c.execute(
            "SELECT changes()").fetchone()[0]
        _set_run(c, run["id"], totals_json=json.dumps(totals), batch_id=None, status="running")
    log(f"{dict(STAGES)[STAGES[run['stage']][0]]}: {written} suggestion(s) from this batch")


def poll(log=None):
    """Collect finished batches and submit the next stage. Safe to call repeatedly (poller thread, tests)."""
    with db.connect() as c:
        resume = [] if jobs.running() else \
            [r["id"] for r in c.execute("SELECT id FROM ingest_runs WHERE status='files_pending'")]
        runs = c.execute("SELECT * FROM ingest_runs WHERE status='waiting'").fetchall()
    for run_id in resume:
        try:
            _advance(run_id)
        except Exception as e:
            with db.connect() as c:
                run = c.execute("SELECT * FROM ingest_runs WHERE id=?", (run_id,)).fetchone()
            _run_log(run)(f"Couldn't start the file stages, will retry: {e}")
    for run in runs:
        try:
            status, _ = llm.batch_status(run["batch_id"])
            if status != "ended":
                continue
            _collect(run)
            _advance(run["id"])
        except Exception as e:
            _run_log(run)(f"Batch check failed, will retry: {e}")


def start_batched(log):
    """Step 6b in batched mode: start a run and return; the poller does the rest."""
    with db.connect() as c:
        if c.execute("SELECT 1 FROM ingest_runs WHERE status IN ('waiting','files_pending') "
                     "OR (status='running' AND updated_at>?)",
                     (int(time.time()) - 900,)).fetchone():
            log("A full read is already in progress; its results arrive on the Updates page as each stage ends.")
            return
        run_id = c.execute("INSERT INTO ingest_runs(job_id,stage,status,started_at,updated_at,totals_json) "
                           "VALUES(?,0,'running',?,?,'{}')", (log.job_id, int(time.time()), int(time.time()))).lastrowid
    log("Reading order: text messages, then spreadsheets, PDFs and images (each fills only what's still missing). "
        "Sent through the batch queue at half price.")
    try:
        _advance(run_id)
    except Exception as e:  # don't stop the rest of the sync (downloads) because Claude can't be reached
        with db.connect() as c:
            _set_run(c, run_id, status="failed")
        log(KEY_HELP if _key_rejected(e) else f"Couldn't send the batch to Claude: {e}")
        return
    with db.connect() as c:
        if c.execute("SELECT status FROM ingest_runs WHERE id=?", (run_id,)).fetchone()[0] != "done":
            jobs.keep_open(log.job_id)  # the job row stays 'waiting' until the last stage's results are in


def start_poller(seconds):
    def loop():
        while True:
            time.sleep(seconds)
            try:
                poll()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True, name="batch-poller").start()


def propose(log):
    with db.connect() as c:
        if not master.current_import(c):
            log("Upload the master workbook on the Master page first.")
            return
    if os.getenv("INGEST_MODE", "batch") == "live":
        written, failed = run_live(log)
        log(f"{written} suggestion(s), {failed} failed request(s)")
    else:
        start_batched(log)
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM proposals WHERE state='proposed'").fetchone()[0]
    log(f"{n} suggestions waiting on the Updates page")
