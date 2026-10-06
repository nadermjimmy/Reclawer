"""Pull group history and attachments from Evolution into the immutable source tables."""
import base64, hashlib, json, mimetypes, os, re, time
import db, evolution

WRAPPERS = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage",
            "editedMessage")
MEDIA_KINDS = {"imageMessage": "image", "documentMessage": "document", "videoMessage": "video",
               "audioMessage": "audio", "stickerMessage": "sticker"}
DOWNLOAD_KINDS = ("image", "document")


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


def parse_record(rec, origin):
    """Evolution message record (history or webhook data) -> source_messages row, or None."""
    key = rec.get("key") or {}
    jid = key.get("remoteJid") or ""
    wa_id = key.get("id")
    if not jid.endswith("@g.us") or not wa_id:
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
        "raw_json": json.dumps(rec, ensure_ascii=False), "origin": origin,
        "ingested_at": int(time.time()),
    }


def store_message(c, row):
    """Insert once; a message already stored is never overwritten."""
    if not row:
        return False
    cols = ",".join(row)
    cur = c.execute(f"INSERT OR IGNORE INTO source_messages({cols}) VALUES({','.join('?' * len(row))})",
                    tuple(row.values()))
    return cur.rowcount == 1


def sync_groups(log):
    groups = evolution.fetch_groups()
    with db.connect() as c:
        for g in groups:
            if not g.get("id"):
                continue
            c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (g["id"], g.get("subject")))
            c.execute("INSERT OR IGNORE INTO group_scope(jid,in_scope) VALUES(?,0)", (g["id"],))
        c.execute("UPDATE messages SET group_name=(SELECT name FROM groups WHERE jid=group_jid) "
                  "WHERE group_name IS NULL")
    log(f"{len(groups)} groups found. Mark the UAE inventory group(s) on the Groups page.")


def pull_history(log, only_scoped=False):
    with db.connect() as c:
        jids = db.scoped_groups(c) if only_scoped else [r[0] for r in c.execute("SELECT jid FROM groups")]
    if not jids:
        log("No groups yet - run 'Sync groups' first.")
        return
    for jid in jids:
        new = seen = 0
        page, pages = 1, 1
        while page <= pages:
            records, pages = evolution.find_messages(jid, page)
            with db.connect() as c:
                for rec in records:
                    seen += 1
                    new += store_message(c, parse_record(rec, "history"))
            page += 1
        log(f"{jid}: {seen} messages read, {new} new")


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
    else:
        os.makedirs(db.MEDIA_DIR, exist_ok=True)
        ext = os.path.splitext(file_name or "")[1] or mimetypes.guess_extension(mimetype or "") or ""
        path = os.path.join(db.MEDIA_DIR, sha + ext.lower())
        with open(path, "wb") as f:
            f.write(content)
        file_id = c.execute(
            "INSERT INTO source_files(sha256,file_name,mimetype,size,path,kind,ingested_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (sha, file_name, mimetype, len(content), path, kind_of(file_name, mimetype),
             int(time.time()))).lastrowid
    c.execute("INSERT OR IGNORE INTO file_occurrences(wa_id,file_id,file_name) VALUES(?,?,?)",
              (wa_id, file_id, file_name))
    return file_id


def download_media(log, retry_failed=False):
    """Fetch images and documents posted in the in-scope groups."""
    with db.connect() as c:
        scoped = db.scoped_groups(c)
        if not scoped:
            log("No in-scope groups. Mark the UAE inventory group(s) on the Groups page first.")
            return
        q = ("SELECT wa_id, message_type, file_name, mimetype FROM source_messages m WHERE has_media=1 "
             f"AND group_jid IN ({','.join('?' * len(scoped))}) "
             "AND wa_id NOT IN (SELECT wa_id FROM file_occurrences)")
        if not retry_failed:
            q += " AND wa_id NOT IN (SELECT wa_id FROM media_fetch WHERE status='failed')"
        todo = c.execute(q, scoped).fetchall()
    ok = failed = skipped = 0
    for r in todo:
        if MEDIA_KINDS.get(r["message_type"]) not in DOWNLOAD_KINDS:
            skipped += 1
            continue
        try:
            data = evolution.media_base64(r["wa_id"])
            b64 = data.get("base64") or ""
            content = base64.b64decode(re.sub(r"^data:[^,]*,", "", b64))
            if not content:
                raise RuntimeError("empty attachment")
            with db.connect() as c:
                save_file(c, r["wa_id"], content, r["file_name"] or data.get("fileName"),
                          r["mimetype"] or data.get("mimetype"))
                c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)",
                          (r["wa_id"], "ok", None, int(time.time())))
            ok += 1
        except Exception as e:
            with db.connect() as c:
                c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)",
                          (r["wa_id"], "failed", str(e)[:500], int(time.time())))
            failed += 1
    log(f"Attachments: {ok} downloaded, {failed} unavailable (listed on the Sources page), "
        f"{skipped} video/audio skipped")
