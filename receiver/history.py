"""Pull group history and attachments from Evolution into the immutable source tables."""
import base64, hashlib, json, mimetypes, os, re, time
import db, evolution, search, window

WRAPPERS = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage",
            "editedMessage")
MEDIA_KINDS = {"imageMessage": "image", "documentMessage": "document", "videoMessage": "video",
               "audioMessage": "audio", "stickerMessage": "sticker"}
DOWNLOAD_KINDS = ("image", "document")
DOWNLOAD_WORKERS = int(os.getenv("DOWNLOAD_WORKERS", "6"))


def unwrap(msg):
    """Strip WhatsApp wrapper layers so the real content is at the top level."""
    for _ in range(4):
        for w in WRAPPERS:
            if isinstance(msg, dict) and isinstance(msg.get(w), dict) and "message" in msg[w]:
                msg = msg[w]["message"]
                break
        else:
            return msg or {}
    return msg or {}


def is_chat(jid):
    """Groups and private chats; not status updates, broadcast lists or channels."""
    return bool(jid) and jid.split("@")[-1] in ("g.us", "s.whatsapp.net", "lid") and jid != "status@broadcast"


def chat_name(jid, name):
    if name:
        return name
    return "+" + jid.split("@")[0] if jid.endswith("@s.whatsapp.net") else jid


def parse_record(rec, origin):
    """Evolution message record (history or webhook data) -> source_messages row, or None."""
    key = rec.get("key") or {}
    jid = key.get("remoteJid") or ""
    wa_id = key.get("id")
    if not is_chat(jid) or not wa_id:
        return None
    msg = unwrap(rec.get("message") or {})
    media_key = next((k for k in MEDIA_KINDS if k in msg), None)
    mtype = media_key or rec.get("messageType") or next(iter(msg), "unknown")
    media = msg.get(media_key, {}) if media_key else {}
    text = (msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
            or media.get("caption") or "")
    ts = rec.get("messageTimestamp")
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        ts = None
    return {
        "wa_id": wa_id, "group_jid": jid,
        "sender": key.get("participant") or rec.get("participant") or key.get("participantAlt"),
        "sender_name": rec.get("pushName"), "ts": ts, "message_type": mtype, "text": text,
        "file_name": media.get("fileName"), "mimetype": media.get("mimetype"),
        "has_media": 1 if media_key in ("imageMessage", "documentMessage", "videoMessage") else 0,
        "file_size": _size(media.get("fileLength")),
        "raw_json": json.dumps(rec, ensure_ascii=False), "origin": origin,
        "ingested_at": int(time.time()),
    }


def _size(v):
    """WhatsApp's fileLength: a number, a numeric string, or {low, high}."""
    if isinstance(v, dict):
        return (int(v.get("high") or 0) << 32) + int(v.get("low") or 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def store_message(c, row):
    """Insert once; a message already stored is never overwritten. New rows go into the search index too."""
    if not row:
        return False
    cols = ",".join(row)
    cur = c.execute(f"INSERT OR IGNORE INTO source_messages({cols}) VALUES({','.join('?' * len(row))})",
                    tuple(row.values()))
    if cur.rowcount == 1:
        search.index_row(c, cur.lastrowid, row)
        return True
    return False


def sync_groups(log):
    """List the instance's chats: Evolution's stored chat list first, then live group names if WhatsApp answers."""
    errors = []
    try:
        chats = evolution.find_chats()
    except Exception as e:
        chats = []
        errors.append(f"stored chat list: {e}")
    try:
        groups = evolution.fetch_groups()
    except Exception as e:
        groups = []
        errors.append(f"live group names: {e}")
    if not chats and not groups:
        raise RuntimeError("Evolution returned no chats (" + "; ".join(errors) + "). If the number was linked "
                           "recently, wait a few minutes for WhatsApp to finish syncing and run this again.")
    n_groups = n_private = 0
    with db.connect() as c:
        for ch in chats:
            jid = ch.get("remoteJid") or ch.get("id") or ""
            if not is_chat(jid):
                continue
            if jid.endswith("@g.us"):
                n_groups += 1
            else:
                n_private += 1
            name = ch.get("name") or ch.get("subject") or ch.get("pushName")
            c.execute("INSERT INTO groups VALUES(?,?) ON CONFLICT(jid) DO UPDATE SET "
                      "name=COALESCE(?, groups.name)", (jid, chat_name(jid, name), name or None))
            c.execute("INSERT OR IGNORE INTO group_scope(jid,in_scope) VALUES(?,?)", (jid, int(jid.endswith("@g.us"))))
        for g in groups:  # live subjects win for groups
            if g.get("id"):
                if not c.execute("SELECT 1 FROM groups WHERE jid=?", (g["id"],)).fetchone():
                    n_groups += 1
                c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (g["id"], g.get("subject") or g["id"]))
                c.execute("INSERT OR IGNORE INTO group_scope(jid,in_scope) VALUES(?,1)", (g["id"],))
        c.execute("UPDATE messages SET group_name=(SELECT name FROM groups WHERE jid=group_jid) "
                  "WHERE group_name IS NULL")
    for e in errors:
        log(f"Note - couldn't get {e}")
    log(f"{n_groups} groups and {n_private} private chats found. All groups are read; tick private chats on the Chats page to include them.")


def pull_history(log, only_scoped=True, jids=None):
    """Copy stored history for the ticked chats (new messages arrive through the webhook), or only `jids`.
    Returns how many new messages were stored."""
    if jids is None:
        with db.connect() as c:
            jids = db.scoped_groups(c) if only_scoped else [r[0] for r in c.execute("SELECT jid FROM groups")]
    if not jids:
        log("No chats ticked yet - run 'Sync chats', then tick the inventory chat(s) on the Chats page.")
        return 0
    total = 0
    since = window.cutoff()
    for jid in jids:
        new = seen = old = 0
        page, pages = 1, 1
        while page <= pages:
            records, pages = evolution.find_messages(jid, page)
            with db.connect() as c:
                for rec in records:
                    seen += 1
                    row = parse_record(rec, "history")
                    if row and since and (row["ts"] or 0) < since:
                        old += 1  # outside the window (INGEST_DAYS): not stored
                        continue
                    new += store_message(c, row)
            ts = [_ts(r) for r in records]
            ts = [t for t in ts if t]
            # Evolution pages newest first: once a whole page is older than the window, the rest is older too
            if since and ts and max(ts) < since and _newest_first(ts):
                break
            page += 1
        log(f"{jid}: {seen} messages read, {new} new, {old} older than the window skipped")
        total += new
    return total


def _ts(rec):
    try:
        return int(rec.get("messageTimestamp"))
    except (TypeError, ValueError):
        return None


def _newest_first(ts):
    return len(ts) < 2 or ts[0] >= ts[-1]


def kind_of(name, mimetype):
    ext = os.path.splitext(name or "")[1].lower()
    mt = (mimetype or "").lower()
    if ext in (".xlsx", ".xlsm", ".xls") or "spreadsheet" in mt or "excel" in mt:
        return "workbook"
    if ext == ".csv" or mt == "text/csv":
        return "csv"
    if ext == ".pdf" or mt == "application/pdf":
        return "pdf"
    if mt.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".webp"):
        return "image"
    return "other"


def save_file(c, wa_id, content, file_name, mimetype):
    """Store bytes once per hash; record every message occurrence separately."""
    sha = hashlib.sha256(content).hexdigest()
    row = c.execute("SELECT id FROM source_files WHERE sha256=?", (sha,)).fetchone()
    if row:
        file_id = row[0]
        # downloads run in parallel: keep the name from the earliest post of this file, whichever finished first
        mine = c.execute("SELECT ts FROM source_messages WHERE wa_id=?", (wa_id,)).fetchone()
        first = c.execute("SELECT MIN(m.ts) FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
                          "WHERE o.file_id=?", (file_id,)).fetchone()[0]
        if file_name and mine and mine[0] is not None and (first is None or mine[0] < first):
            c.execute("UPDATE source_files SET file_name=? WHERE id=?", (file_name, file_id))
    else:
        os.makedirs(db.MEDIA_DIR, exist_ok=True)
        ext = os.path.splitext(file_name or "")[1] or mimetypes.guess_extension(mimetype or "") or ""
        path = os.path.join(db.MEDIA_DIR, sha + ext.lower())
        with open(path, "wb") as f:
            f.write(content)
        # OR IGNORE: the same file can arrive in two posts that download at the same time
        c.execute("INSERT OR IGNORE INTO source_files(sha256,file_name,mimetype,size,path,kind,ingested_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (sha, file_name, mimetype, len(content), path, kind_of(file_name, mimetype), int(time.time())))
        file_id = c.execute("SELECT id FROM source_files WHERE sha256=?", (sha,)).fetchone()[0]
    c.execute("INSERT OR IGNORE INTO file_occurrences(wa_id,file_id,file_name) VALUES(?,?,?)",
              (wa_id, file_id, file_name))
    return file_id


class DiskLow(RuntimeError):
    pass


def fetch_media(wa_id):
    """Download one message's image/document now. Returns the source_files id; records failures."""
    with db.connect() as c:
        hit = c.execute("SELECT file_id FROM file_occurrences WHERE wa_id=?", (wa_id,)).fetchone()
        if hit:
            return hit[0]
        m = c.execute("SELECT * FROM source_messages WHERE wa_id=?", (wa_id,)).fetchone()
    if not m:
        raise LookupError("message not found")
    if MEDIA_KINDS.get(m["message_type"]) not in DOWNLOAD_KINDS:
        raise ValueError("only images and documents can be downloaded")
    if window.low_disk():
        raise DiskLow("not enough free space on the data volume")
    limit = window.MEDIA_MAX_MB * 2**20
    try:
        if (m["file_size"] or 0) > limit:
            raise ValueError(f"over {window.MEDIA_MAX_MB} MB")
        data = evolution.media_base64(wa_id, m["group_jid"])
        b64 = re.sub(r"^data:[^,]*,", "", data.get("base64") or "")
        if len(b64) * 3 // 4 > limit:
            raise ValueError(f"over {window.MEDIA_MAX_MB} MB")
        content = base64.b64decode(b64)
        if not content:
            raise RuntimeError("empty attachment")
        with db.connect() as c:
            file_id = save_file(c, wa_id, content, m["file_name"] or data.get("fileName"),
                                m["mimetype"] or data.get("mimetype"))
            c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)", (wa_id, "ok", None, int(time.time())))
        return file_id
    except Exception as e:
        status = "too_large" if str(e).startswith("over ") else "failed"
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)",
                      (wa_id, status, str(e)[:500], int(time.time())))
        raise


def free_space(log, days=None, jids=None):
    """Delete downloaded attachments whose every post is older than the window, and the raw WhatsApp records of
    old messages. What was read from them stays: message text, parsed sheet cells and PDF page text, suggestions
    and decisions. Returns {files, mb, messages}."""
    since = window.cutoff() if days is None else int(time.time() - days * 86400)
    if not since:
        log("INGEST_DAYS=0: nothing is old enough to remove")
        return {"files": 0, "mb": 0, "messages": 0}
    freed = n = 0
    with db.connect() as c:
        scope, args = "", []
        if jids is not None:  # only files posted in these chats
            marks = ",".join("?" * len(jids)) or "''"
            scope = (" AND EXISTS (SELECT 1 FROM file_occurrences o2 JOIN source_messages m2 ON m2.wa_id=o2.wa_id "
                     f"WHERE o2.file_id=f.id AND m2.group_jid IN ({marks}))")
            args = list(jids)
        old = c.execute("SELECT f.id, f.path, f.size FROM source_files f WHERE f.path IS NOT NULL AND NOT EXISTS ("
                        "SELECT 1 FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
                        "WHERE o.file_id=f.id AND COALESCE(m.ts,0)>=?)" + scope, [since, *args]).fetchall()
        for f in old:
            try:
                if f["path"] and os.path.exists(f["path"]):
                    os.remove(f["path"])
                    freed += f["size"] or 0
                    n += 1
            except OSError:
                continue
            c.execute("UPDATE source_files SET path=NULL, pruned_at=? WHERE id=?", (int(time.time()), f["id"]))
        mscope = f" AND group_jid IN ({','.join('?' * len(jids)) or chr(39) * 2})" if jids is not None else ""
        msgs = c.execute("UPDATE source_messages SET raw_json=NULL WHERE COALESCE(ts,0)<? AND raw_json IS NOT NULL"
                         + mscope, [since, *(jids or [])]).rowcount
    out = {"files": n, "mb": round(freed / 2**20, 1), "messages": msgs}
    log(f"Freed {out['mb']} MB: {n} old attachment(s) deleted, raw records of {msgs} old message(s) cleared. "
        f"Text, parsed cells, PDF text and suggestions are kept. {window.disk()['free_mb']} MB free now.")
    return out


def download_media(log, retry_failed=False, jids=None):
    """Fetch images and documents posted in the in-scope groups (or only in `jids`). Returns (ok, failed)."""
    with db.connect() as c:
        scoped = db.scoped_groups(c) if jids is None else list(jids)
        if not scoped:
            log("No chats ticked. Tick the inventory chat(s) on the Chats page first.")
            return 0, 0
        q = ("SELECT wa_id, message_type, file_name, mimetype, file_size FROM source_messages m WHERE has_media=1 "
             f"AND group_jid IN ({','.join('?' * len(scoped))}) AND COALESCE(ts,0)>=? "
             "AND wa_id NOT IN (SELECT wa_id FROM file_occurrences) "
             "AND wa_id NOT IN (SELECT wa_id FROM media_fetch WHERE status='too_large')")
        if not retry_failed:
            q += " AND wa_id NOT IN (SELECT wa_id FROM media_fetch WHERE status='failed')"
        todo = c.execute(q + " ORDER BY ts DESC", [*scoped, window.cutoff()]).fetchall()  # newest first
        big = [r["wa_id"] for r in todo if MEDIA_KINDS.get(r["message_type"]) in DOWNLOAD_KINDS
               and (r["file_size"] or 0) > window.MEDIA_MAX_MB * 2**20]
        for wa in big:
            c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)",
                      (wa, "too_large", f"over {window.MEDIA_MAX_MB} MB (MEDIA_MAX_MB)", int(time.time())))
    wanted = [r["wa_id"] for r in todo if MEDIA_KINDS.get(r["message_type"]) in DOWNLOAD_KINDS and r["wa_id"] not in big]
    skipped = len(todo) - len(wanted) - len(big)
    ok = failed = 0
    if big:
        log(f"{len(big)} attachment(s) over {window.MEDIA_MAX_MB} MB skipped (listed on Sources)")
    if wanted and window.low_disk():
        log(f"Downloads paused: only {window.disk()['free_mb']} MB free on the data volume "
            f"(MIN_FREE_MB={window.MIN_FREE_MB}). Run 'Free space' in Pipeline or grow the volume.")
        return 0, 0
    if wanted:
        log(f"Downloading {len(wanted)} attachment(s) from the {window.describe()}, {DOWNLOAD_WORKERS} at a time")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:  # Evolution answers one media request at a time per call
        futures = [pool.submit(fetch_media, wa) for wa in wanted]
        for i, f in enumerate(as_completed(futures), 1):
            try:
                f.result()
                ok += 1
            except DiskLow:
                for other in futures:
                    other.cancel()
                log(f"Downloads paused: the data volume is down to {window.disk()['free_mb']} MB free")
                break
            except Exception:
                failed += 1
            if i % 25 == 0 or i == len(futures):
                log(f"downloaded {i}/{len(futures)} attachments")
    if ok:
        with db.connect() as c:  # parallel downloads: settle each file's name on its earliest post, once
            c.execute("UPDATE source_files SET file_name=(SELECT o.file_name FROM file_occurrences o "
                      "JOIN source_messages m ON m.wa_id=o.wa_id WHERE o.file_id=source_files.id "
                      "AND o.file_name IS NOT NULL ORDER BY m.ts, m.wa_id LIMIT 1) "
                      "WHERE id IN (SELECT file_id FROM file_occurrences GROUP BY file_id HAVING COUNT(*)>1)")
    log(f"Attachments: {ok} downloaded, {failed} unavailable (listed on the Sources page), "
        f"{skipped} video/audio skipped")
    return ok, failed
