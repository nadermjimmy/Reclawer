"""Keep UAE developers' groups out of the Egypt inventory (MARKET=eg).

Every WhatsApp group is read by default. Groups of UAE developers (uae_developers.json) cost tokens, memory and
disk for nothing, so they are unticked. Developers in 'both_markets' (Emaar, Modon, Reportage ...) also sell in Egypt:
their groups are decided from the chat's own recent messages, Egyptian signals (EGP / جنيه, New Cairo, Zayed,
Sahel ...) against UAE signals (AED / درهم, Dubai, Abu Dhabi ...). When it's unclear the group stays ticked.

Never touched: chats a person ticked or unticked themselves (group_scope.note='person'), chats the ops team assigned
to a developer, private chats, and chats whose developer is on the Egypt master workbook (unless that developer is
also a UAE developer, then the messages decide). Every untick is marked note='auto:uae:<developer>' so it can be
undone in one step.
"""
import json, os, re, time
import db, normalize, profiles

LIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uae_developers.json")
EG_SIGNS = re.compile(r"\begp\b|\bl\.?e\.?\b|جنيه|ج\.م|\bpounds?\b|\bmisr\b|\begypt|مصر|cairo|القاهره|التجمع|tagamo|"
                      r"zayed|زايد|october|اكتوبر|sahel|الساحل|north coast|sokhna|السخنه|new capital|العاصمه|"
                      r"galala|الجلاله|mostakbal|مستقبل|shorouk|الشروق|ain sokhna|hurghada|الغردقه|gouna|الجونه", re.I)
UAE_SIGNS = re.compile(r"\baed\b|dirham|درهم|dubai|دبي|abu dhabi|ابوظبي|ابو ظبي|sharjah|الشارقه|ajman|عجمان|"
                       r"ras al khaimah|راس الخيمه|\bdld\b|\bjvc\b|downtown dubai|business bay|dubai marina|"
                       r"emirates hills|yas island|saadiyat|\boqood\b|\buae\b|الامارات", re.I)


def load_lists():
    with open(LIST_FILE, encoding="utf-8") as f:
        d = json.load(f)
    tok = lambda names: [(n, normalize.normalize(n).split()) for n in names if normalize.normalize(n)]
    return tok(d.get("uae_only", [])), tok(d.get("both_markets", []))


def _has_words(words, toks):
    """True when `words` appear in order as whole words in `toks` ('Emaar' in 'Emaar Misr & Byit', not in 'Emaarat')."""
    n = len(words)
    return any(toks[i:i + n] == words for i in range(len(toks) - n + 1))


def developer_in(name, lists):
    toks = normalize.normalize(name).split()
    for label, words in sorted(lists, key=lambda t: -len(t[1])):  # longest name first ('Emaar Misr' before 'Emaar')
        if _has_words(words, toks):
            return label
    return None


def signals(c, jid, days=60, limit=400):
    since = int(time.time()) - days * 86400
    eg = uae = 0
    for (t,) in c.execute("SELECT text FROM source_messages WHERE group_jid=? AND COALESCE(ts,0)>=? AND text<>'' "
                          "ORDER BY ts DESC LIMIT ?", (jid, since, limit)):
        n = normalize.normalize(t)
        eg += len(EG_SIGNS.findall(n))
        uae += len(UAE_SIGNS.findall(n))
    return eg, uae


def classify(c, jid, name, note, lists, master_devs):
    """(action, developer, reason); action: untick / keep / skip."""
    if note == "person":
        return "skip", None, "set by a person"
    if not jid.endswith("@g.us"):
        return "skip", None, "private chat"
    prof = c.execute("SELECT developer, source FROM chat_profiles WHERE jid=?", (jid,)).fetchone()
    if prof and prof["source"] == "team" and prof["developer"]:
        return "skip", None, f"assigned by the team to {prof['developer']}"
    uae_only, both = lists
    dev_both = developer_in(name, both)
    dev_uae = None if dev_both else developer_in(name, uae_only)
    dev = dev_both or dev_uae
    if not dev:
        return "keep", None, "not a UAE developer"
    eg, uae = signals(c, jid)
    if dev_both:
        if uae > eg:
            return "untick", dev, f"{dev} sells in both markets; this chat talks UAE ({uae} UAE vs {eg} Egypt signals)"
        return "keep", dev, f"{dev} sells in both markets; this chat looks Egyptian or unclear ({eg} Egypt vs {uae} UAE)"
    raw = profiles.developer_from_name(name) or ""
    on_master = raw and normalize.compact(profiles.match_developer(c, raw, master_devs)) in \
        {normalize.compact(d) for d in master_devs}
    if on_master:
        return "keep", dev, f"{dev}: this developer is on the Egypt master"
    if eg >= 3 and eg >= 2 * uae:
        return "keep", dev, f"{dev} is a UAE developer, but this chat talks Egypt ({eg} Egypt vs {uae} UAE signals)"
    return "untick", dev, f"{dev} is a UAE developer ({uae} UAE vs {eg} Egypt signals)"


def plan(c, only_new=False):
    """[{jid, name, action, developer, reason, in_scope}] for every group. only_new: groups never decided before."""
    lists = load_lists()
    master_devs = profiles.master_developers(c)
    out = []
    for r in c.execute("SELECT g.jid, g.name, COALESCE(s.in_scope,0) in_scope, s.note FROM groups g "
                       "LEFT JOIN group_scope s ON s.jid=g.jid WHERE g.jid LIKE '%@g.us'").fetchall():
        if only_new and r["note"]:
            continue
        action, dev, why = classify(c, r["jid"], r["name"] or "", r["note"], lists, master_devs)
        out.append({"jid": r["jid"], "name": r["name"] or r["jid"], "action": action, "developer": dev,
                    "reason": why, "in_scope": bool(r["in_scope"])})
    return out


def apply(log=lambda *_: None, only_new=False, dry_run=False):
    """Untick UAE developers' groups. Returns {unticked, kept_both_markets, already_off}."""
    with db.connect() as c:
        items = plan(c, only_new)
        todo = [i for i in items if i["action"] == "untick" and i["in_scope"]]
        if not dry_run:
            for i in todo:
                c.execute("INSERT INTO group_scope(jid,in_scope,note) VALUES(?,0,?) ON CONFLICT(jid) DO UPDATE SET "
                          "in_scope=0, note=excluded.note", (i["jid"], f"auto:uae:{i['developer']}"))
            for i in items:  # remember that the rule looked at the rest, so a sync only checks new groups
                if i["action"] == "keep":
                    c.execute("UPDATE group_scope SET note='auto:checked' WHERE jid=? AND note IS NULL", (i["jid"],))
    kept = [i for i in items if i["action"] == "keep" and i["developer"]]
    for i in todo:
        log(f"{'Would untick' if dry_run else 'Unticked'}: {i['name']} - {i['reason']}")
    for i in kept:
        log(f"Kept: {i['name']} - {i['reason']}")
    log(f"{len(todo)} UAE developer group(s) {'would be ' if dry_run else ''}unticked, {len(kept)} kept on purpose. "
        "Undo: Pipeline > 'Re-tick UAE groups', or tick a chat on the Chats page.")
    return {"unticked": len(todo), "kept": len(kept), "items": todo + kept}


def undo(log=lambda *_: None):
    with db.connect() as c:
        n = c.execute("UPDATE group_scope SET in_scope=1, note='auto:checked' WHERE note LIKE 'auto:uae:%'").rowcount
    log(f"Re-ticked {n} group(s) the UAE rule had unticked")
    return n
