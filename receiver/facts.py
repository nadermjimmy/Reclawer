"""Extract sourced project facts (location, service charge, handover, ...) from group messages and PDFs."""
import json, time
import db, llm, rules

FACT_TYPES = ["location", "service_charge", "payment_plan", "completion_rate", "amenities", "handover",
              "phase_status", "unit_status", "launch", "other"]

SYSTEM = f"""You extract factual statements about {rules.MARKET_NAME} real-estate developer projects from WhatsApp messages
(Arabic, English, Franco-Arabic) and brochure/PDF text.

Rules:
- Record only what the source text itself states. Never add outside knowledge or fill gaps.
- project_label: the project exactly as the text names it. If the text doesn't make the project clear from
  the message itself or the immediately surrounding messages in this batch, skip the statement.
- phase_label: the phase/building/tower exactly as written, or "".
- A location (city/district/area) is a location fact, never a project_label by itself.
- Instructions like "No flip or change the unit" are restrictions: record them as fact_type "other".
- raw_value: the exact words from the source (verbatim, original language).
- parsed_value: a short clean reading of raw_value (e.g. "Q4 2027", "{rules.CURRENCY} 20 per {rules.METRIC_LABEL}", "60/40",
  "sold out"), or "" if it can't be read without guessing.
- ambiguous: true if the statement is unclear, conditional, or could refer to another project/phase.
- updates_previous: true only if the text explicitly corrects or updates an earlier statement.
- phase_status / unit_status: availability statements ("Phase 2 sold out", "unit 1403 sold").
  Keep phase and unit statements separate; never infer one from the other.
- Skip greetings, chit-chat and questions without answers. Return an empty list if there are no facts."""

SCHEMA = llm.obj({"facts": {"type": "array", "items": llm.obj({
    "source_id": llm.STR, "project_label": llm.STR, "phase_label": llm.STR,
    "fact_type": {"type": "string", "enum": FACT_TYPES},
    "raw_value": llm.STR, "parsed_value": llm.STR, "ambiguous": llm.BOOL, "updates_previous": llm.BOOL})}})


def _store(c, f, kind, ts):
    status = "needs_validation" if f["ambiguous"] else "source_stated_unverified"
    c.execute("INSERT OR IGNORE INTO fact_assertions(raw_project_label,phase_label,fact_type,raw_value,"
              "parsed_value,source_kind,source_ref,observed_ts,validation_status,updates_previous) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (f["project_label"].strip(), f["phase_label"].strip(), f["fact_type"], f["raw_value"],
               f["parsed_value"], kind, f["source_id"], ts, status, int(f["updates_previous"])))


def extract_messages(log, batch=60):
    with db.connect() as c:
        scoped = db.scoped_groups(c)
        if not scoped:
            log("No in-scope groups selected.")
            return
        msgs = c.execute(
            "SELECT wa_id, group_jid, ts, sender_name, text, file_name FROM source_messages "
            f"WHERE group_jid IN ({','.join('?' * len(scoped))}) AND (text<>'' OR file_name IS NOT NULL) "
            "AND 'm:'||wa_id NOT IN (SELECT batch_key FROM processed_batches) ORDER BY group_jid, ts",
            scoped).fetchall()
    log(f"{len(msgs)} new messages to read")
    for i in range(0, len(msgs), batch):
        chunk = msgs[i:i + batch]
        lines = [{"source_id": m["wa_id"], "time": m["ts"], "from": m["sender_name"], "text": m["text"],
                  "attached_file": m["file_name"]} for m in chunk]
        try:
            out = llm.call_json(SYSTEM, json.dumps({"messages": lines}, ensure_ascii=False), SCHEMA)
        except Exception as e:
            log(f"batch {i // batch + 1} failed: {e}")
            continue
        ids = {m["wa_id"]: m["ts"] for m in chunk}
        with db.connect() as c:
            for f in out["facts"]:
                if f["source_id"] in ids:
                    _store(c, f, "message", ids[f["source_id"]])
            c.executemany("INSERT OR IGNORE INTO processed_batches VALUES(?,?)",
                          [("m:" + k, int(time.time())) for k in ids])
        log(f"read {min(i + batch, len(msgs))}/{len(msgs)} messages, {len(out['facts'])} facts in this batch")


def extract_pdfs(log):
    import rows
    with db.connect() as c:
        files = rows.scoped_file_ids(c)
        pages = [p for p in c.execute(
            "SELECT r.id, r.file_id, r.row_number, r.raw_json, f.file_name FROM source_rows r "
            "JOIN source_files f ON f.id=r.file_id WHERE r.kind='pdf_page' "
            "AND 'p:'||r.id NOT IN (SELECT batch_key FROM processed_batches)").fetchall()
            if p["file_id"] in files]
    log(f"{len(pages)} new PDF pages to read")
    for p in pages:
        text = json.loads(p["raw_json"]).get("text", "")
        if text.strip():
            body = {"messages": [{"source_id": str(p["id"]), "text": text[:12000],
                                  "attached_file": f"{p['file_name']} page {p['row_number']}"}]}
            try:
                out = llm.call_json(SYSTEM, json.dumps(body, ensure_ascii=False), SCHEMA)
            except Exception as e:
                log(f"{p['file_name']} p{p['row_number']} failed: {e}")
                continue
            with db.connect() as c:
                ts = c.execute("SELECT MIN(m.ts) FROM file_occurrences o JOIN source_messages m "
                               "ON m.wa_id=o.wa_id WHERE o.file_id=?", (p["file_id"],)).fetchone()[0]
                for f in out["facts"]:
                    f["source_id"] = str(p["id"])
                    _store(c, f, "pdf_page", ts)
        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO processed_batches VALUES(?,?)", ("p:%d" % p["id"], int(time.time())))
    log("PDF pages done")


def extract_all(log):
    extract_messages(log)
    extract_pdfs(log)
