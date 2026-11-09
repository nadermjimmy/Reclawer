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
import cell_parse as cp, master_schema as ms

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
        out = _json(row)
        out["projects"] = [r[0] for r in c.execute(
            "SELECT project_label FROM chat_projects WHERE jid=? AND status='ok' ORDER BY project_label", (jid,))]
        return out
    g = c.execute("SELECT name FROM groups WHERE jid=?", (jid,)).fetchone()
    raw = developer_from_name(g[0] if g else "")
    if not raw:
        return None
    dev = match_developer(c, raw, devs)
    c.execute("INSERT OR IGNORE INTO chat_profiles(jid,developer,shares,on_request_json,notes,source,updated_at) "
              "VALUES(?,?,?,?,?,?,?)", (jid, dev, "", "[]", "", "auto", int(time.time())))
    out = _json(c.execute("SELECT * FROM chat_profiles WHERE jid=?", (jid,)).fetchone())
    out["projects"] = []
    return out


def put(c, jid, developer=None, shares="", on_request=(), notes=""):
    """Save the developer's habits. developer=None keeps the assigned developer (set it with `assign`)."""
    get(c, jid)
    on_request = [f for f in on_request if f in ms.UNIT_FIELDS]
    cur = c.execute("SELECT * FROM chat_profiles WHERE jid=?", (jid,)).fetchone()
    dev = (developer or "").strip() if developer is not None else (cur["developer"] if cur else "")
    source = "team" if developer is not None or not cur else cur["source"]
    c.execute("INSERT INTO chat_profiles(jid,developer,shares,on_request_json,notes,source,updated_at) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(jid) DO UPDATE SET developer=excluded.developer, "
              "shares=excluded.shares, on_request_json=excluded.on_request_json, notes=excluded.notes, "
              "source=excluded.source, updated_at=excluded.updated_at",
              (jid, dev, shares if shares in ("inventory", "starting_prices") else "", json.dumps(on_request),
               (notes or "").strip()[:1000], source, int(time.time())))
    return get(c, jid)


# ---------- ops-team assignment: chat -> developer + projects (Phase 0) ----------
TEAM = "team"  # the app has one shared login; the log still records each change and when


def developer_projects(c, developer):
    """The developer's rows on the master, one entry per project (a project can sit on several sheets)."""
    import master
    dk = cp.normalize_project_key(developer)
    out = {}
    for r in c.execute(f"SELECT * FROM master_rows WHERE state IN ({','.join('?' * len(master.USABLE))}) "
                       "ORDER BY sheet, row_number", master.USABLE).fetchall():
        if dk and master.same_developer(dk, r):
            e = out.setdefault(r["project_key"], {"project_key": r["project_key"], "project": r["project"],
                                                  "sheets": []})
            sheet = master.home_sheet(r)
            if sheet not in e["sheets"]:
                e["sheets"].append(sheet)
    return sorted(out.values(), key=lambda e: e["project"].lower())


def assigned_projects(c, jid):
    return [dict(r) for r in c.execute("SELECT * FROM chat_projects WHERE jid=? ORDER BY project_label", (jid,))]


def assigned_keys(c, jid):
    return {r[0] for r in c.execute("SELECT project_key FROM chat_projects WHERE jid=?", (jid,))} if jid else set()


def _log(c, jid, field, old, new, by):
    if old != new:
        c.execute("INSERT INTO assignment_log(jid,field,old,new,by,at) VALUES(?,?,?,?,?,?)",
                  (jid, field, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), by,
                   int(time.time())))


def assign(c, jid, developer, project_keys=None, by=TEAM, allow_new=False):
    """Team assignment. developer must be one of the master's developers (or '' to clear) unless allow_new
    ('Not on the sheet yet'). project_keys=None leaves the projects as they are; a list replaces them and must
    belong to that developer. Returns the profile, or raises ValueError with a message for the user."""
    developer = (developer or "").strip()
    if developer and not allow_new and developer not in master_developers(c):
        raise ValueError(f"'{developer}' isn't a developer on the master sheet")
    p = get(c, jid) or {"developer": "", "source": ""}
    old_dev = p["developer"] if p.get("source") == "team" else ""
    now = int(time.time())
    c.execute("INSERT INTO chat_profiles(jid,developer,shares,on_request_json,notes,source,updated_at,assigned_by,"
              "assigned_at) VALUES(?,?,'','[]','','team',?,?,?) ON CONFLICT(jid) DO UPDATE SET "
              "developer=excluded.developer, source='team', updated_at=excluded.updated_at, "
              "assigned_by=excluded.assigned_by, assigned_at=excluded.assigned_at", (jid, developer, now, by, now))
    _log(c, jid, "developer", old_dev, developer, by)
    dk = cp.normalize_project_key(developer)
    old_keys = sorted(assigned_keys(c, jid))
    if developer != old_dev and project_keys is None:
        project_keys = []  # a new developer: the old developer's projects no longer apply
    if project_keys is not None:
        valid = {e["project_key"]: e for e in developer_projects(c, developer)}
        bad = [k for k in project_keys if k not in valid]
        if bad:
            raise ValueError("Some projects don't belong to this developer on the master sheet")
        c.execute("DELETE FROM chat_projects WHERE jid=?", (jid,))
        for k in project_keys:
            e = valid[k]
            c.execute("INSERT INTO chat_projects(jid,developer_key,project_key,project_label,sheet,status,"
                      "assigned_by,assigned_at) VALUES(?,?,?,?,?,'ok',?,?)",
                      (jid, dk, k, e["project"], ", ".join(e["sheets"]), by, now))
        _log(c, jid, "projects", old_keys, sorted(project_keys), by)
    return get(c, jid)


def relink_projects(c):
    """After a master re-upload: re-check every assigned project against the new rows (by normalized developer +
    project). Missing ones are kept, marked 'missing', and returned for review."""
    missing = []
    by_dev = {}
    for r in c.execute("SELECT * FROM chat_projects").fetchall():
        dev = c.execute("SELECT developer FROM chat_profiles WHERE jid=?", (r["jid"],)).fetchone()
        devname = dev[0] if dev else ""
        if devname not in by_dev:
            by_dev[devname] = {e["project_key"]: e for e in developer_projects(c, devname)}
        e = by_dev[devname].get(r["project_key"])
        if e:
            c.execute("UPDATE chat_projects SET status='ok', project_label=?, sheet=? WHERE jid=? AND project_key=?",
                      (e["project"], ", ".join(e["sheets"]), r["jid"], r["project_key"]))
        else:
            c.execute("UPDATE chat_projects SET status='missing' WHERE jid=? AND project_key=?",
                      (r["jid"], r["project_key"]))
            missing.append({"jid": r["jid"], "project": r["project_label"]})
    return missing


def _json(r):
    keys = r.keys()
    return {"jid": r["jid"], "developer": r["developer"] or "", "shares": r["shares"] or "",
            "on_request": json.loads(r["on_request_json"] or "[]"), "notes": r["notes"] or "",
            "source": r["source"], "updated_at": r["updated_at"],
            "assigned_by": r["assigned_by"] if "assigned_by" in keys else None,
            "assigned_at": r["assigned_at"] if "assigned_at" in keys else None}


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
    if p.get("projects"):
        bits.append("the Byit team assigned this group to these projects: " + ", ".join(p["projects"]))
    if p["shares"] == "starting_prices":
        bits.append("they only share starting prices per unit type, not full inventory")
    if p["on_request"]:
        bits.append("they only send " + ", ".join(ms.UNIT_LABELS[f] for f in p["on_request"]) + " prices on request")
    if p["notes"]:
        bits.append(p["notes"])
    return "; ".join(bits)
