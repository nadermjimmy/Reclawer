"""Digest: unprocessed group messages -> Claude -> summary. Listen-only; sends nothing to WhatsApp.
Run on Railway: railway ssh --service receiver python digest.py
"""
import os, sqlite3, datetime
from collections import defaultdict
import anthropic

SYSTEM = """You are the operations analyst for Byit Corp, a real-estate brokerage in Egypt and the UAE
that works with freelance agents. You read WhatsApp group messages (Arabic, English, Franco-Arabic).
For each group, report only what matters: new leads, deal/reservation updates, payment or collection issues,
unanswered questions or blockers, and anything urgent. Skip chit-chat. Be concise. Write in English."""

c = sqlite3.connect(os.getenv("DB_PATH", "/data/messages.db"))
rows = c.execute("SELECT id, COALESCE(group_name, group_jid), sender_name, ts, text "
                 "FROM messages WHERE processed=0 AND text<>'' ORDER BY ts").fetchall()
if not rows:
    print("Nothing new.")
    raise SystemExit

by_group = defaultdict(list)
for _id, gname, sender, ts, text in rows:
    t = datetime.datetime.fromtimestamp(ts).strftime("%d %b %H:%M")
    by_group[gname].append(f"[{t}] {sender or 'unknown'}: {text}")

transcript = "\n\n".join(f"### {g}\n" + "\n".join(lines) for g, lines in by_group.items())
resp = anthropic.Anthropic().messages.create(
    model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
    max_tokens=16000,
    system=SYSTEM,
    messages=[{"role": "user", "content": f"Messages since the last digest:\n\n{transcript}"}],
)
text = "".join(b.text for b in resp.content if b.type == "text")
if not text:
    raise SystemExit(f"No summary returned (stop_reason={resp.stop_reason}); messages left unprocessed.")
print(text)
c.executemany("UPDATE messages SET processed=1 WHERE id=?", [(r[0],) for r in rows])
c.commit()
