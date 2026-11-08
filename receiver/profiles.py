"""Developer profiles: which developer a WhatsApp chat belongs to, and how that developer shares data.

Most developer groups are named "<Developer> & Byit App". The developer is read from the name and matched to the
master's developers; the team can correct it and add the developer's habits on the chat:
- shares: 'inventory' (sends full availability) or 'starting_prices' (only starting prices per unit type)
- on_request: unit types the developer only sends when asked (e.g. ABM: villas) — not expected, not chased
- notes: free text the brain reads (e.g. "Soma Bay is also the location of all their projects")

Extraction, matching and the brain use the profile so a developer's projects stay with that developer, and a
developer name that is also a place (Soma Bay) isn't read as a project or a location.
"""
import difflib, json, re, time
import cell_parse as cp, db, master_schema as ms

NAME_PATTERN = re.compile(r"^\s*(.+?)\s*(?:&|-|\||x|×|\+|with)\s*by\s*it\b", re.I)


def developer_from_name(name):
    """'Soma bay & Byit App' -> 'Soma bay'; None when the name doesn't look like a developer group."""
    m = NAME_PATTERN.match(name or "")
    return m.group(1).strip(" -&|") if m else None


def master_developers(c):
    return sorted({r[0] for r in c.execute("SELECT DISTINCT developer FROM master_rows WHERE developer<>''")})


def match_developer(c, raw, devs=None):
    """Closest master developer for a raw name, or the raw name when none is close."""
    key = cp.normalize_project_key(raw)
    if not key:
        return raw
    best, score = None, 0.0
    for d in devs if devs is not None else master_developers(c):
        dk = cp.normalize_project_key(d)
        if not dk:
            continue
        s = 1.0 if dk == key else 0.9 if (len(key) >= 3 and (key in dk or dk in key)) else \
            difflib.SequenceMatcher(None, key, dk).ratio()
        if s > score:
            best, score = d, s
    return best if best and score >= 0.75 else raw


def get(c, jid, devs=None):
    """Profile for one chat (auto-filled from the chat name the first time). None when not a developer chat."""
    row = c.execute("SELECT * FROM chat_profiles WHERE jid=?", (jid,)).fetchone()
    if row:
        return _json(row)
    g = c.execute("SELECT name FROM groups WHERE jid=?", (jid,)).fetchone()
    raw = developer_from_name(g[0] if g else "")
    if not raw:
        return None
    dev = match_developer(c, raw, devs)
    c.execute("INSERT OR IGNORE INTO chat_profiles VALUES(?,?,?,?,?,?,?)",
              (jid, dev, "", "[]", "", "auto", int(time.time())))
    return _json(c.execute("SELECT * FROM chat_profiles WHERE jid=?", (jid,)).fetchone())


def put(c, jid, developer, shares="", on_request=(), notes=""):
    on_request = [f for f in on_request if f in ms.UNIT_FIELDS]
    c.execute("INSERT OR REPLACE INTO chat_profiles VALUES(?,?,?,?,?,?,?)",
              (jid, (developer or "").strip(), shares if shares in ("inventory", "starting_prices") else "",
               json.dumps(on_request), (notes or "").strip()[:1000], "team", int(time.time())))
    return get(c, jid)


def _json(r):
    return {"jid": r["jid"], "developer": r["developer"] or "", "shares": r["shares"] or "",
            "on_request": json.loads(r["on_request_json"] or "[]"), "notes": r["notes"] or "",
            "source": r["source"], "updated_at": r["updated_at"]}


def for_chats(c, jids):
    """{jid: profile} for many chats (auto-filling as needed)."""
    devs = master_developers(c)
    return {j: p for j in set(jids) if j for p in [get(c, j, devs)] if p}


def by_developer(c):
    """{developer key: [profiles]} — a developer can have several groups."""
    out = {}
    for r in c.execute("SELECT * FROM chat_profiles WHERE developer<>''"):
        out.setdefault(cp.normalize_project_key(r["developer"]), []).append(_json(r))
    return out


def describe(p):
    """One line for prompts."""
    if not p:
        return ""
    bits = [f"this is the developer {p['developer']}'s own group: projects here are {p['developer']}'s unless "
            f"the message names another developer"]
    if p["shares"] == "starting_prices":
        bits.append("they only share starting prices per unit type, not full inventory")
    if p["on_request"]:
        bits.append("they only send " + ", ".join(ms.UNIT_LABELS[f] for f in p["on_request"]) + " prices on request")
    if p["notes"]:
        bits.append(p["notes"])
    return "; ".join(bits)
