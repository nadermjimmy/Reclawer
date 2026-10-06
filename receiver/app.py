"""Receives Evolution API webhooks, keeps group messages, drops noise."""
import os, re, sqlite3, time
from fastapi import FastAPI, Request, HTTPException

DB = os.getenv("DB_PATH", "/data/messages.db")
TOKEN = os.environ["WEBHOOK_TOKEN"]
ALLOWED = {g.strip() for g in os.getenv("ALLOWED_GROUPS", "").split(",") if g.strip()}
NOISE = re.compile(r"^\W*(ok|okay|tmam|تمام|thanks|شكرا|mashy|ماشي)?\W*$", re.I)
os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)

def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS messages(
        id TEXT PRIMARY KEY, group_jid TEXT, group_name TEXT, sender TEXT,
        sender_name TEXT, ts INTEGER, type TEXT, text TEXT, processed INTEGER DEFAULT 0)""")
    c.execute("CREATE TABLE IF NOT EXISTS groups(jid TEXT PRIMARY KEY, name TEXT)")
    return c

def extract_text(msg: dict) -> str:
    if not msg:
        return ""
    return (msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("imageMessage", {}).get("caption")
            or msg.get("videoMessage", {}).get("caption")
            or msg.get("documentMessage", {}).get("fileName")
            or "")

app = FastAPI()

@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != TOKEN:
        raise HTTPException(403)
    body = await request.json()
    event = body.get("event", "")

    if event == "groups.upsert":
        items = body.get("data") or []
        with db() as c:
            for g in items if isinstance(items, list) else [items]:
                if g.get("id"):
                    c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (g["id"], g.get("subject")))
                    c.execute("UPDATE messages SET group_name=? WHERE group_jid=? AND group_name IS NULL",
                              (g.get("subject"), g["id"]))
        return {"ok": True}

    if event != "messages.upsert":
        return {"ignored": event}

    d = body.get("data", {})
    key = d.get("key", {})
    jid = key.get("remoteJid", "")
    if not jid.endswith("@g.us"):
        return {"ignored": "not a group"}
    if ALLOWED and jid not in ALLOWED:
        return {"ignored": "group not allowed"}

    text = extract_text(d.get("message", {})).strip()
    mtype = d.get("messageType", "unknown")
    if mtype in ("reactionMessage", "stickerMessage", "protocolMessage"):
        return {"ignored": mtype}
    if mtype in ("conversation", "extendedTextMessage") and NOISE.match(text):
        return {"ignored": "noise"}

    with db() as c:
        row = c.execute("SELECT name FROM groups WHERE jid=?", (jid,)).fetchone()
        c.execute("INSERT OR IGNORE INTO messages(id,group_jid,group_name,sender,sender_name,ts,type,text)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (key.get("id"), jid, row[0] if row else None,
                   key.get("participant") or key.get("participantAlt"),
                   d.get("pushName"), int(d.get("messageTimestamp") or time.time()), mtype, text))
    return {"stored": True}

@app.post("/sync-groups/{token}")
def sync_groups(token: str, groups: list[dict]):
    """Seed group names from Evolution's fetchAllGroups output."""
    if token != TOKEN:
        raise HTTPException(403)
    with db() as c:
        for g in groups:
            c.execute("INSERT OR REPLACE INTO groups VALUES(?,?)", (g["id"], g.get("subject")))
        c.execute("UPDATE messages SET group_name=(SELECT name FROM groups WHERE jid=group_jid) WHERE group_name IS NULL")
    return {"synced": len(groups)}

@app.get("/health")
def health():
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    return {"ok": True, "messages": n}
