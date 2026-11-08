"""Pipeline step: read new chat messages, workbook rows and PDF pages, and propose cell updates to the
master 'App Updates' workbook. Claude only reads values in the sheet's own notation; matching to a row,
choosing the sheet and comparing with the current value are done in code (master.add_proposal)."""
import json, time
import db, llm, master, master_schema as ms, rows

FIELD_HELP = "\n".join([
    "- location: one-letter location code (see codes below)",
    "- ready_to_move: 'rtm' (some units ready), 'all project rtm', a ready unit as '3 bed/16,400,000' or "
    "'area/price', or 'Hold' / 'Hide' for the whole project",
    *[f"- {f}: {ms.UNIT_LABELS[f]} — 'area/price' in sq m and EGP, e.g. '125/6,900,000'; or a status: "
      "so (sold out), de (not offered), rtm, service (serviced apartment), Hold, Hide" for f in ms.UNIT_FIELDS],
    "- payment_plan: down payment and years, e.g. '10% - 8Y', or 'Cash'",
    "- commission: broker commission percent, e.g. '4' or '3.5 net' (keep the word net if stated)",
    "- feedback: a short status note: Launch, Open Inventory, Sold, Hold, or a few words",
])

SYSTEM = f"""You read Egyptian real-estate WhatsApp messages (Arabic, English, Franco-Arabic), price-list rows and
brochure pages, and list values that belong in the broker team's project sheet (one row per developer project).

Fields you may fill (value must be written in the sheet's notation):
{FIELD_HELP}

Unit types: Apartment 1..4 = 1..4 bedrooms. S Villa / Duplex, Town(house), Twin(house), Standalone villa,
Office, Clinic, Shop, Pharmacy, Studio. For each project and unit type give the single price the source quotes
(the latest one if the source gives several over time); for a price list give the starting (lowest) unit of
that type in the list. Area in sq m, price in EGP, no currency text in the value.

Location codes: {', '.join(f'{k}={v}' for k, v in ms.LOCATION_CODES.items())}. Use '' if the source doesn't say.

Rules:
- Record only what the source states. No outside knowledge, no guessing, no calculations beyond writing the number.
- project: the project name as written; developer: the developer if stated, else ''.
- If it is unclear which project a value belongs to, skip it.
- Never output salesperson names or phone numbers.
- quote: the exact words/cells the value came from (verbatim).
- Return an empty list if nothing fits."""

SCHEMA = llm.obj({"updates": {"type": "array", "items": llm.obj({
    "source_id": llm.STR, "developer": llm.STR, "project": llm.STR,
    "location_code": {"type": "string", "enum": ["", *ms.LOCATION_CODES]},
    "field": {"type": "string", "enum": ms.ALL_PROPOSABLE},
    "value": llm.STR, "quote": llm.STR})}})


def _ask(body, log, label):
    try:
        return llm.call_json(SYSTEM, json.dumps(body, ensure_ascii=False), SCHEMA)["updates"]
    except Exception as e:
        log(f"{label} failed: {e}")
        return None


def _store(updates, kind, ts_of):
    n = 0
    with db.connect() as c:
        for u in updates:
            if u["source_id"] not in ts_of:
                continue
            n += master.add_proposal(c, dict(u, source_kind=kind, source_ref=u["source_id"],
                                             source_ts=ts_of[u["source_id"]]))
    return n


def _done(keys):
    with db.connect() as c:
        c.executemany("INSERT OR IGNORE INTO processed_batches VALUES(?,?)", [(k, int(time.time())) for k in keys])


def from_messages(log, batch=60):
    with db.connect() as c:
        scoped = db.scoped_groups(c)
        if not scoped:
            log("No in-scope chats selected.")
            return
        msgs = c.execute(
            "SELECT wa_id, ts, sender_name, text, file_name FROM source_messages "
            f"WHERE group_jid IN ({','.join('?' * len(scoped))}) AND text<>'' "
            "AND 'u:'||wa_id NOT IN (SELECT batch_key FROM processed_batches) ORDER BY group_jid, ts",
            scoped).fetchall()
    log(f"{len(msgs)} new messages to read")
    for i in range(0, len(msgs), batch):
        chunk = msgs[i:i + batch]
        body = {"messages": [{"source_id": m["wa_id"], "time": m["ts"], "text": m["text"],
                              "attached_file": m["file_name"]} for m in chunk]}
        out = _ask(body, log, f"message batch {i // batch + 1}")
        if out is None:
            continue
        n = _store(out, "message", {m["wa_id"]: m["ts"] for m in chunk})
        _done(["u:" + m["wa_id"] for m in chunk])
        log(f"read {min(i + batch, len(msgs))}/{len(msgs)} messages, {n} proposals")


def _file_ts(c, file_id):
    return c.execute("SELECT MAX(m.ts) FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
                     "WHERE o.file_id=?", (file_id,)).fetchone()[0]


def from_files(log, chunk_rows=150):
    with db.connect() as c:
        files = rows.scoped_file_ids(c)
        todo = c.execute("SELECT r.id, r.file_id, r.sheet, r.kind, r.raw_json, f.file_name FROM source_rows r "
                         "JOIN source_files f ON f.id=r.file_id WHERE r.kind IN ('sheet_row','pdf_page') "
                         "AND 'ur:'||r.id NOT IN (SELECT batch_key FROM processed_batches) "
                         "ORDER BY r.file_id, r.sheet, r.row_number").fetchall()
        todo = [r for r in todo if r["file_id"] in files]
        ts = {f: _file_ts(c, f) for f in {r["file_id"] for r in todo}}
    log(f"{len(todo)} new file rows/pages to read")
    groups = {}
    for r in todo:
        groups.setdefault((r["file_id"], r["sheet"], r["kind"]), []).append(r)
    for (file_id, sheet, kind), items in groups.items():
        for i in range(0, len(items), 1 if kind == "pdf_page" else chunk_rows):
            part = items[i:i + (1 if kind == "pdf_page" else chunk_rows)]
            lines = []
            for r in part:
                raw = json.loads(r["raw_json"])
                txt = raw.get("text", "")[:12000] if kind == "pdf_page" else \
                    " | ".join(cell["v"] for cell in raw.get("cells", []) if cell["v"].strip())
                if txt.strip():
                    lines.append({"source_id": str(r["id"]), "text": txt})
            if lines:
                out = _ask({"file": part[0]["file_name"], "sheet": sheet, "rows": lines}, log,
                           f"{part[0]['file_name']} / {sheet}")
                if out is None:
                    continue
                n = _store(out, "row" if kind == "sheet_row" else "pdf_page",
                           {str(r["id"]): ts[file_id] for r in part})
                log(f"{part[0]['file_name']} / {sheet}: {len(lines)} rows, {n} proposals")
            _done(["ur:%d" % r["id"] for r in part])


def propose(log):
    with db.connect() as c:
        if not master.current_import(c):
            log("Upload the master workbook on the Master page first.")
            return
    from_messages(log)
    from_files(log)
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM proposals WHERE state='proposed'").fetchone()[0]
    log(f"{n} proposals waiting on the Updates page")
