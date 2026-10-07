"""Group unit rows by their labelling context and ask Claude to propose the parent project / phase."""
import json, time
import db, llm, rows, rules

SYSTEM = f"""You map rows of real-estate availability workbooks, shared in WhatsApp chats about the
{rules.MARKET_NAME} market, to a developer -> parent project -> phase/building/tower hierarchy.

Use ONLY the evidence given (file name, sheet name, section titles, cell labels, the WhatsApp messages around
the file). Never use outside knowledge, websites or guesses to name a project, developer or location.

Owner rules (mandatory):
- A location (city, district, area) is not a project; never output one as a project name.
- Instructions such as "No Flip or Change the Unit" are not projects; they are restrictions.
- Towers, phases, buildings, blocks and clusters are children of a parent project, never projects themselves.
  Put the child label (exactly as written in the source) in phase_label.
- A worksheet tab name, availability status, campaign title or price-list title is not a project by itself.
- Reuse an existing canonical project name from the list provided when the evidence refers to the same project.
{rules.prompt_rules()}
- Scope is {rules.MARKET_NAME}. If the evidence shows the material is about another country, set in_market to
  "no". If unclear, "uncertain".

decision values:
- "project": the evidence names the parent project clearly (give project_name; phase_label may be "").
- "unresolved": the parent project can't be established from the evidence.
- "exclude_scope": the material is clearly outside {rules.MARKET_NAME}.
confidence: "high" only when the evidence explicitly names the project for these rows; otherwise "medium" or "low".
Use "" for unknown developer/phase. rationale: one or two sentences quoting the evidence used.
Owner rules file for reference: {json.dumps({k: v for k, v in rules.RULES.items() if not k.startswith('_')},
                                             ensure_ascii=False)}"""

SCHEMA = llm.obj({"mappings": {"type": "array", "items": llm.obj({
    "key": llm.STR,
    "decision": {"type": "string", "enum": ["project", "unresolved", "exclude_scope"]},
    "developer": llm.STR, "project_name": llm.STR, "phase_label": llm.STR,
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "in_market": {"type": "string", "enum": ["yes", "no", "uncertain"]},
    "rationale": llm.STR})}})


def file_messages(c, file_id, window=6):
    """The messages that carried the file plus a few before/after in the same group."""
    out = []
    cols = "wa_id, ts, sender_name, text, file_name"
    for occ in c.execute("SELECT o.wa_id, m.group_jid, m.ts FROM file_occurrences o "
                         "JOIN source_messages m ON m.wa_id=o.wa_id WHERE o.file_id=? ORDER BY m.ts LIMIT 3",
                         (file_id,)).fetchall():
        g, ts = occ["group_jid"], occ["ts"] or 0
        before = c.execute(f"SELECT {cols} FROM source_messages WHERE group_jid=? AND ts<=? "
                           "ORDER BY ts DESC LIMIT ?", (g, ts, window + 1)).fetchall()
        after = c.execute(f"SELECT {cols} FROM source_messages WHERE group_jid=? AND ts>? "
                          "ORDER BY ts LIMIT ?", (g, ts, window)).fetchall()
        out.append({"file_message": occ["wa_id"],
                    "messages": [{"id": n["wa_id"], "time": n["ts"], "from": n["sender_name"],
                                  "text": (n["text"] or "")[:600], "file": n["file_name"]}
                                 for n in list(reversed(before)) + list(after)]})
    return out


def collect_contexts(log):
    """Deterministic: one context per distinct (file, sheet, section, project cell, phase cell)."""
    with db.connect() as c:
        files = rows.scoped_file_ids(c)
        ctx = {}
        for r, vals in rows.data_rows(c, files):
            k = rows.context_key(r, vals)
            e = ctx.setdefault(k, {"file_id": r["file_id"], "sheet": r["sheet"], "section": r["section_label"] or "",
                                   "project": rows.first(vals, "project"), "phase": rows.first(vals, "phase"),
                                   "units": [], "n": 0})
            e["n"] += 1
            if len(e["units"]) < 6:
                e["units"].append(rows.first(vals, "unit_code"))
        for k, e in ctx.items():
            f = c.execute("SELECT file_name FROM source_files WHERE id=?", (e["file_id"],)).fetchone()
            evidence = {"file_name": f["file_name"], "sheet": e["sheet"], "section_title": e["section"],
                        "project_cell": e["project"], "phase_cell": e["phase"], "sample_units": e["units"],
                        "messages": file_messages(c, e["file_id"])}
            c.execute("INSERT OR REPLACE INTO label_contexts VALUES(?,?,?,?,?,?,?,?)",
                      (k, e["file_id"], e["sheet"], e["section"], e["project"], e["phase"],
                       json.dumps(evidence, ensure_ascii=False), e["n"]))
    log(f"{len(ctx)} labelling contexts found across {len(files)} in-scope files")
    return len(ctx)


def propose(log, redo=False, batch=8):
    collect_contexts(log)
    with db.connect() as c:
        q = "SELECT x.* FROM label_contexts x LEFT JOIN label_mappings m ON m.context_key=x.key "
        q += ("WHERE m.context_key IS NULL OR m.review_state='proposed'" if redo else "WHERE m.context_key IS NULL")
        todo = c.execute(q).fetchall()
        known = [r[0] for r in c.execute(
            "SELECT DISTINCT project_name FROM label_mappings WHERE decision='project' AND project_name<>''")]
    log(f"{len(todo)} contexts to map")
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        payload = {"existing_projects": known,
                   "contexts": [{"key": r["key"], **json.loads(r["evidence_json"])} for r in chunk]}
        try:
            out = llm.call_json(SYSTEM, json.dumps(payload, ensure_ascii=False), SCHEMA)
        except Exception as e:
            log(f"batch {i // batch + 1} failed: {e}")
            continue
        keys = {r["key"] for r in chunk}
        with db.connect() as c:
            for m in out["mappings"]:
                if m["key"] not in keys:
                    continue
                c.execute("INSERT OR REPLACE INTO label_mappings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (m["key"], m["decision"], m["developer"], m["project_name"].strip(),
                           m["phase_label"].strip(), m["confidence"], m["rationale"], m["in_market"],
                           "claude", "proposed", int(time.time())))
                if m["decision"] == "project" and m["project_name"] and m["project_name"] not in known:
                    known.append(m["project_name"])
        log(f"mapped {min(i + batch, len(todo))}/{len(todo)}")
