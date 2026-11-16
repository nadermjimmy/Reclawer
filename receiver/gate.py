"""Accuracy gate: puts every open suggestion in one queue, with the reasons, so the team only reviews what is very
likely right.

- ready:  the group's own developer's existing row, exact project name, value in the sheet's format, sane price,
          not already on the team's sheet, no learned warning, and (once trained) a high approval probability.
- check:  probably right but needs a person: near name inside the right developer, a big price change, the catalog
          says another developer owns the project, an older value changed on the sheet, ...
- new:    a project that isn't on the sheet yet (a monthly catalog task, not daily work).
- hidden: never shown to the team: UAE (AED / sq ft / 'Byit App / X' groups), another developer's row, already on
          the sheet, an AED-scale price. Kept for learning and for the admin.

Nothing here approves or rejects anything: the gate only sorts and explains. Turned on and off on More > Brain
(meta 'accuracy_gate'); the queues are always computed so the admin can compare before switching on.
"""
import json, math, os, re, time, collections
import cell_parse as cp, db, master, master_schema as ms, names, normalize

N = normalize.normalize
AED = re.compile(r"\baed\b|dirham|درهم|dubai|دبي|abu dhabi|ابوظبي|ابو ظبي|sharjah|الشارقه|ras al khaimah|\buae\b|"
                 r"\bsq\.? ?ft\b|sqft|ft²|\busd\b", re.I)
EGP = re.compile(r"\begp\b|جنيه|ج\.م|\bl\.?e\b|مليون|cairo|القاهره|التجمع|زايد|zayed|sahel|الساحل|sokhna|"
                 r"العاصمه|new capital|october|اكتوبر|مصر|\bmisr\b|egypt", re.I)
READY_P = float(os.getenv("READY_P", "0.9"))
HIDE = {"aed_in_quote", "uae_group", "other_developer_row", "already_on_sheet", "aed_scale"}
CHECK = {"near_name", "project_other_developer", "price_too_low", "price_too_high", "price_jump", "area_odd",
         "bad_format", "lifts_hold", "group_developer_unknown", "exists_on_sheet", "learned_warning", "low_probability",
         "old_value_changed", "needs_sheet"}
LABELS = {
    "aed_in_quote": "the quote is in AED / sq ft (UAE)",
    "uae_group": "a UAE developer's group",
    "other_developer_row": "it was put on another developer's row",
    "already_on_sheet": "your sheet already has this value",
    "aed_scale": "the price looks like AED (about 1/13 of the sheet's)",
    "near_name": "the project name is only close to the sheet's",
    "project_other_developer": "the catalog says another developer owns this project",
    "price_too_low": "price per m² is lower than 99% of your sheet",
    "price_too_high": "price per m² is higher than 99% of your sheet",
    "price_jump": "price per m² is less than half or more than double the current value",
    "area_odd": "the area is unusual for this unit type",
    "bad_format": "the value isn't in the sheet's format",
    "lifts_hold": "approving lifts Hold/Hide on Sheet 2",
    "group_developer_unknown": "the group isn't assigned to a developer",
    "exists_on_sheet": "suggested as new, but the project is already on your sheet",
    "learned_warning": "similar suggestions were usually rejected",
    "low_probability": "the learned approval chance is low",
    "old_value_changed": "the sheet's current value differs from the one this was read against",
    "needs_sheet": "a new project with no known sheet",
}


def enabled(c):
    return master.gate_on(c)


def set_enabled(c, on):
    c.execute("INSERT OR REPLACE INTO meta VALUES('accuracy_gate', ?)", ("1" if on else "0",))


def family(field):
    return "commercial" if field in ("office", "clinic", "shop", "pharmacy") else "residential"


def ppsm(field, p):
    if field == "ready_to_move":
        a, pr = p.get("rtm_area_sqm"), p.get("rtm_price_egp")
    else:
        a, pr = p.get("area_sqm"), p.get("price_egp")
    return pr / a if a and pr and a > 0 else None


def _pct(vals, q):
    return vals[min(len(vals) - 1, max(0, int(len(vals) * q)))] if vals else None


class Context:
    """Everything a run needs, built once: catalog, price ranges, chat facts, priors, lessons, model."""

    def __init__(self, c):
        import acceptance, catalog, lessons
        self.cat = catalog.get(c)
        pp, ar = collections.defaultdict(list), collections.defaultdict(list)
        for r in c.execute("SELECT field, parsed_json FROM master_cells"):
            f = r["field"]
            if f in ms.UNIT_FIELDS or f == "ready_to_move":
                p = json.loads(r["parsed_json"] or "{}")
                v = ppsm(f, p)
                if v:
                    pp[family(f)].append(v)
                a = p.get("rtm_area_sqm") if f == "ready_to_move" else p.get("area_sqm")
                if a:
                    ar[f].append(a)
        self.ppsm = {k: (_pct(sorted(v), .01), _pct(sorted(v), .5), _pct(sorted(v), .99)) for k, v in pp.items()
                     if len(v) >= 30}
        self.area = {f: (_pct(sorted(v), .01), _pct(sorted(v), .99)) for f, v in ar.items() if len(v) >= 20}
        self.chats = {}
        self.priors = acceptance.Priors(c)
        self.lessons = lessons.load(c)
        self.model, self.model_ver = acceptance.model()
        self.groups = {r["jid"]: r["name"] for r in c.execute("SELECT jid, name FROM groups")}

    def strip_dubai(self, text):
        for dn in self.cat.dubai_names:
            text = text.replace(dn, " ")
        return text

    def chat(self, c, jid):
        if jid in self.chats:
            return self.chats[jid]
        import catalog
        name = self.groups.get(jid, "") if jid else ""
        dev, src = catalog.group_developer(c, jid, name, self.cat) if jid else (None, None)
        aed = egp = 0
        if jid:
            since = int(time.time()) - 60 * 86400
            for (t,) in c.execute("SELECT text FROM source_messages WHERE group_jid=? AND COALESCE(ts,0)>=? "
                                  "AND text<>'' ORDER BY ts DESC LIMIT 400", (jid, since)):
                n = self.strip_dubai(N(t))
                aed += len(AED.findall(n))
                egp += len(EGP.findall(n))
        uae_name = names.uae_style_name(name)
        uae = (uae_name and not (egp > aed and egp >= 2)) or (not dev and aed > egp and aed >= 2)
        info = {"name": name, "developer": dev, "dev_source": src, "aed": aed, "egp": egp, "uae": bool(uae)}
        self.chats[jid] = info
        return info


def orig_old(p):
    g = json.loads(p["gate_json"] or "{}") if "gate_json" in p.keys() and p["gate_json"] else {}
    return g.get("orig_old_raw", p["old_raw"])


def evaluate(c, p, ctx, row=None):
    """{tier, flags: [code], reasons: [text], features: {..}, p_approve} for one proposal row (with chat_jid)."""
    import acceptance, lessons
    field = p["field"]
    flags = []
    jid = p["chat_jid"] if "chat_jid" in p.keys() else None
    ch = ctx.chat(c, jid)
    if row is None and p["row_id"]:
        row = c.execute("SELECT * FROM master_rows WHERE id=?", (p["row_id"],)).fetchone()
    text = ctx.strip_dubai(N((p["quote"] or "") + " " + (p["new_raw"] or "")))
    if AED.search(text):
        flags.append("aed_in_quote")
    elif ch["uae"]:
        flags.append("uae_group")
    gdev = ch["developer"]
    if row is not None and gdev and not names.same_company(row["developer"] or "", gdev):
        flags.append("other_developer_row")
    pc = ctx.cat.project(p["project"], gdev, strong=True) if not ctx.cat.empty else None
    if gdev and pc and not names.same_company(pc[2], gdev):
        flags.append("project_other_developer")
    if not gdev:
        flags.append("group_developer_unknown")
    parsed = json.loads(p["new_parsed_json"] or "{}")
    if p["new_parsed_json"] and not parsed.get("ok"):
        flags.append("bad_format")
    if "Hold/Hide" in (p["warning"] or ""):
        flags.append("lifts_hold")
    cur_raw, cur = ("", {})
    price_ratio = None
    if row is not None:
        cur_raw, cur = master.current(c, row["id"], field)
        if ms.same_value(field, cur, parsed):
            flags.append("already_on_sheet")
        elif (orig_old(p) or "").strip() != (cur_raw or "").strip():
            flags.append("old_value_changed")
        if p["match_state"] == "near":
            flags.append("near_name")
    else:
        if not p["sheet"]:
            flags.append("needs_sheet")
        if gdev:  # 'new' but the group's developer already has it on the sheet
            sheets = [p["sheet"]] if p["sheet"] else master._sheets_for(field)
            if any(master.own_row(c, p["project"], gdev, s) for s in sheets):
                flags.append("exists_on_sheet")
    v = ppsm(field, parsed) if (field in ms.UNIT_FIELDS or field == "ready_to_move") else None
    rng = ctx.ppsm.get(family(field))
    z = None
    if v and rng:
        lo, mid, hi = rng
        z = math.log(v / mid)
        if v < lo * 0.8:
            flags.append("price_too_low")
        elif v > hi * 1.25:
            flags.append("price_too_high")
    a = (parsed.get("rtm_area_sqm") if field == "ready_to_move" else parsed.get("area_sqm")) if parsed else None
    if a and field in ctx.area:
        alo, ahi = ctx.area[field]
        if a < alo * 0.7 or a > ahi * 1.5:
            flags.append("area_odd")
    if v and cur:
        tv = ppsm(field, cur)
        if tv:
            price_ratio = v / tv
            if 0.04 <= price_ratio <= 0.12:
                flags.append("aed_scale")
            elif price_ratio < 0.5 or price_ratio > 2:
                flags.append("price_jump")
    scope = {"group": jid or "", "developer": names.key(gdev or (row["developer"] if row is not None else "") or ""),
             "field": field, "source": p["source_kind"], "flags": flags}
    hits = lessons.matching(ctx.lessons, scope)
    if any(l["outcome"] == "rejected" and l["rate"] >= 0.7 and l["support"] >= 5 for l in hits):
        flags.append("learned_warning")
    feats = acceptance.features(p, flags, scope, ctx.priors, price_ratio=price_ratio, ppsm_z=z,
                                group_known=bool(gdev), group_team=ch["dev_source"] == "team",
                                catalog_owned=bool(pc and gdev and names.same_company(pc[2], gdev)))
    prob, contrib = acceptance.predict(ctx.model, feats)
    if prob is not None and prob < READY_P and not (set(flags) & HIDE) and p["row_id"]:
        flags.append("low_probability")
    fl = set(flags)
    if fl & HIDE:
        tier = "hidden"
    elif p["row_id"] is None and "exists_on_sheet" not in fl:
        tier = "new"
    elif fl & CHECK or p["match_state"] not in ("exact", "alias"):
        tier = "check"
    else:
        tier = "ready"
    reasons = [LABELS[f] for f in flags if f in LABELS]
    reasons += [l["text"] for l in hits[:3]]
    return {"tier": tier, "flags": flags, "reasons": reasons, "features": feats, "p_approve": prob,
            "contrib": contrib, "current_raw": cur_raw, "group_developer": gdev, "model": ctx.model_ver}


CHAT_SQL = master_chat_sql = ("(CASE WHEN p.source_kind='message' THEN (SELECT sm.group_jid FROM source_messages sm "
                              "WHERE sm.wa_id=p.source_ref) ELSE (SELECT sm.group_jid FROM source_rows sr "
                              "JOIN file_occurrences fo ON fo.file_id=sr.file_id JOIN source_messages sm "
                              "ON sm.wa_id=fo.wa_id WHERE sr.id=CAST(p.source_ref AS INTEGER) ORDER BY sm.ts "
                              "DESC LIMIT 1) END)")


def run(log=lambda *_: None, ids=None, only_new=False):
    """Sort every open suggestion (or only `ids`) into a queue. Also refreshes 'old value' to the sheet's current
    value so the team always compares against their own sheet. Returns {tier: count}."""
    with db.connect() as c:
        ctx = Context(c)
        q = (f"SELECT p.*, {CHAT_SQL} chat_jid, b.verdict brain_verdict, b.confidence brain_confidence "
             "FROM proposals p LEFT JOIN brain_reviews b ON b.proposal_id=p.id WHERE p.state='proposed'")
        args = []
        if only_new:
            q += " AND p.gated_at IS NULL"
        if ids is not None:
            if not ids:
                return {}
            q += f" AND p.id IN ({','.join('?' * len(ids))})"
            args = list(ids)
        todo = c.execute(q, args).fetchall()
        rows = {r["id"]: r for r in c.execute("SELECT * FROM master_rows")}
        counts = collections.Counter()
        now = int(time.time())
        for p in todo:
            e = evaluate(c, p, ctx, rows.get(p["row_id"]) if p["row_id"] else None)
            counts[e["tier"]] += 1
            upd = {"tier": e["tier"], "p_approve": e["p_approve"], "gated_at": now,
                   "gate_json": json.dumps(dict({k: e[k] for k in ("flags", "reasons", "contrib", "group_developer",
                                                                   "model")}, orig_old_raw=orig_old(p)),
                                           ensure_ascii=False)}
            if "old_value_changed" in e["flags"] and p["row_id"]:
                upd["old_raw"] = e["current_raw"]  # show the team's current value, not the one at read time
            c.execute(f"UPDATE proposals SET {', '.join(k + '=?' for k in upd)} WHERE id=?", (*upd.values(), p["id"]))
    log("Queues: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return dict(counts)
