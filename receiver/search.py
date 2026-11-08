"""Instant search over chat messages (text, file name, sender) and chat names, Arabic-aware.

Index: an FTS5 table `source_messages_fts` whose rowid is the source_messages rowid. It holds *normalised* copies
of text / file_name / sender_name, written by history.store_message (the single insert path) and caught up from
source_messages on start-up and before each search, so rows inserted any other way are found too.
Normalisation (index and query alike): NFKD + drop combining marks (Arabic tashkeel, hamza/madda on alef, Latin
accents), drop tatweel, ى→ي, ة→ه, ٱ→ا, Arabic-Indic digits → 0-9, case-fold. Without FTS5 the same normalisation
runs through a LIKE scan (slow but correct).
"""
import re, sqlite3, unicodedata

FTS = "source_messages_fts"
_EXTRA = {0x0640: None, ord("ى"): "ي", ord("ة"): "ه", ord("ٱ"): "ا"}
_EXTRA.update({0x0660 + i: str(i) for i in range(10)})
_EXTRA.update({0x06F0 + i: str(i) for i in range(10)})
_TABLE = {cp: None for cp in range(0x10000) if unicodedata.category(chr(cp)) == "Mn"}
_TABLE.update(_EXTRA)
TOKEN = re.compile(r"\w+")


def normalize(s):
    if not s:
        return ""
    return unicodedata.normalize("NFKD", s).casefold().translate(_TABLE)


def normalize_map(s):
    """(normalised text, original index of every normalised char), for highlight ranges in the original."""
    out, idx = [], []
    for i, ch in enumerate(s or ""):
        n = normalize(ch)
        out.append(n)
        idx.extend([i] * len(n))
    return "".join(out), idx


def tokens(q):
    return TOKEN.findall(normalize(q))


def ranges_in(text, toks):
    """Merged [start, end) ranges in the original `text` where any token occurs (normalised substring match)."""
    if not text or not toks:
        return []
    norm, idx = normalize_map(text)
    found = []
    for t in set(toks):
        start = norm.find(t)
        while start != -1:
            end = start + len(t)
            found.append((idx[start], idx[end - 1] + 1))
            start = norm.find(t, end)
    found.sort()
    merged = []
    for a, b in found:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def snippet(text, ranges, width=160, before=50):
    """Window of `text` around the first hit; ranges shifted to the snippet (an ellipsis counts as 1 char)."""
    if len(text) <= width or not ranges:
        cut = text[:width]
        return cut + ("…" if len(text) > width else ""), [r for r in ranges if r[1] <= len(cut)]
    start = max(0, ranges[0][0] - before)
    if start:
        sp = text.rfind(" ", start - 15, start)
        start = sp + 1 if sp != -1 else start
    end = min(len(text), start + width)
    pre = "…" if start else ""
    out = pre + text[start:end] + ("…" if end < len(text) else "")
    shift = len(pre) - start
    return out, [[a + shift, b + shift] for a, b in ranges if a >= start and b <= end]


# ---------- index ----------
def available(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (FTS,)).fetchone())


def ensure(c):
    """Create the FTS table if SQLite has FTS5 (idempotent) and index anything not indexed yet."""
    try:
        c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS} USING fts5(text, file_name, sender, wa_id UNINDEXED, "
                  "tokenize='unicode61 remove_diacritics 2')")
    except sqlite3.OperationalError:
        return False  # no FTS5 in this SQLite build: search falls back to LIKE
    catch_up(c)
    return True


def index_row(c, rowid, row):
    if rowid and available(c):
        c.execute(f"INSERT OR REPLACE INTO {FTS}(rowid, text, file_name, sender, wa_id) VALUES(?,?,?,?,?)",
                  (rowid, normalize(row.get("text")), normalize(row.get("file_name")),
                   normalize(row.get("sender_name")), row.get("wa_id")))


def catch_up(c):
    """Index source_messages rows newer than the last indexed rowid (direct inserts, older databases)."""
    if not available(c):
        return 0
    last = c.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {FTS}").fetchone()[0]
    todo = c.execute("SELECT rowid, wa_id, text, file_name, sender_name FROM source_messages WHERE rowid>? "
                     "AND (text<>'' OR file_name IS NOT NULL)", (last,)).fetchall()
    c.executemany(f"INSERT OR REPLACE INTO {FTS}(rowid, text, file_name, sender, wa_id) VALUES(?,?,?,?,?)",
                  [(r[0], normalize(r[2]), normalize(r[3]), normalize(r[4]), r[1]) for r in todo])
    return len(todo)


def clear(c):
    if available(c):
        c.execute(f"DELETE FROM {FTS}")


# ---------- query ----------
VISIBLE_TYPES = ("reactionMessage", "protocolMessage", "senderKeyDistributionMessage", "pollUpdateMessage",
                 "messageContextInfo", "editedMessage")


def find_messages(c, q, jid="", limit=50):
    """Matching messages, newest first: [{wa_id, jid, ts, sender, text, file_name}], plus has_more."""
    toks = tokens(q)
    if not toks:
        return [], False
    args = []
    if available(c):
        catch_up(c)
        match = " AND ".join(f'"{t}"*' for t in toks)
        sql = (f"SELECT m.wa_id, m.group_jid, m.ts, m.sender_name, m.text, m.file_name FROM {FTS} f "
               f"JOIN source_messages m ON m.rowid=f.rowid AND m.wa_id=f.wa_id WHERE {FTS} MATCH ?")
        args.append(match)
    else:
        c.create_function("rcv_norm", 1, normalize, deterministic=True)
        hay = "(rcv_norm(m.text)||' '||rcv_norm(m.file_name)||' '||rcv_norm(m.sender_name))"
        sql = ("SELECT m.wa_id, m.group_jid, m.ts, m.sender_name, m.text, m.file_name FROM source_messages m WHERE "
               + " AND ".join(f"{hay} LIKE ?" for _ in toks))
        args += [f"%{t}%" for t in toks]
    sql += f" AND m.message_type NOT IN {VISIBLE_TYPES}"
    if jid:
        sql += " AND m.group_jid=?"
        args.append(jid)
    rows = c.execute(sql + " ORDER BY m.ts DESC, m.rowid DESC LIMIT ?", args + [limit + 1]).fetchall()
    return rows[:limit], len(rows) > limit


def message_hit(r, toks):
    """Result row with a snippet and highlight ranges: text first, else the file name."""
    text, fname = r["text"] or "", r["file_name"] or ""
    rng = ranges_in(text, toks)
    field = "text"
    if rng or not fname:
        snip, rng = snippet(text, rng)
    else:
        field, snip = "file", fname
        rng = ranges_in(fname, toks)
        if not rng:
            field, (snip, rng) = "sender", snippet(text, [])
    return {"wa_id": r["wa_id"], "jid": r["group_jid"], "ts": r["ts"], "sender": r["sender_name"] or "",
            "snippet": snip, "match_ranges": rng, "field": field, "file_name": fname or None}
