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
            c.execute("INSERT OR IGNORE INTO group_scope(jid,in_scope) VALUES(?,0)", (jid,))
        for g in groups:  # live subjects win for groups
            if g.get("id"):
                if not c.execute("SELECT 1 FROM groups WHERE jid=?", (g["id"],)).fetchone():
                    n_groups += 1
                c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (g["id"], g.get("subject") or g["id"]))
                c.execute("INSERT OR IGNORE INTO group_scope(jid,in_scope) VALUES(?,0)", (g["id"],))
        c.execute("UPDATE messages SET group_name=(SELECT name FROM groups WHERE jid=group_jid) "
                  "WHERE group_name IS NULL")
    for e in errors:
        log(f"Note - couldn't get {e}")
    log(f"{n_groups} groups and {n_private} private chats found. Tick the inventory chat(s) on the Chats page.")


def pull_history(log, only_scoped=True):
    """Copy stored history for the ticked chats (new messages arrive through the webhook)."""
    with db.connect() as c:
        jids = db.scoped_groups(c) if only_scoped else [r[0] for r in c.execute("SELECT jid FROM groups")]
    if not jids:
        log("No chats ticked yet - run 'Sync chats', then tick the inventory chat(s) on the Chats page.")
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
    try:
        data = evolution.media_base64(wa_id, m["group_jid"])
        content = base64.b64decode(re.sub(r"^data:[^,]*,", "", data.get("base64") or ""))
        if not content:
            raise RuntimeError("empty attachment")
        with db.connect() as c:
            file_id = save_file(c, wa_id, content, m["file_name"] or data.get("fileName"),
                                m["mimetype"] or data.get("mimetype"))
            c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)", (wa_id, "ok", None, int(time.time())))
        return file_id
    except Exception as e:
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO media_fetch VALUES(?,?,?,?)",
                      (wa_id, "failed", str(e)[:500], int(time.time())))
        raise


def download_media(log, retry_failed=False):
    """Fetch images and documents posted in the in-scope groups."""
    with db.connect() as c:
        scoped = db.scoped_groups(c)
        if not scoped:
            log("No chats ticked. Tick the inventory chat(s) on the Chats page first.")
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
            fetch_media(r["wa_id"])
            ok += 1
        except Exception:
            failed += 1
    log(f"Attachments: {ok} downloaded, {failed} unavailable (listed on the Sources page), "
        f"{skipped} video/audio skipped")
