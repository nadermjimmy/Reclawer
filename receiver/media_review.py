"""Propose what each group image shows. Nothing is displayed on a project until a reviewer approves it."""
import base64, json, time
import db, llm, mapping, rows, rules

CLASSES = ["project_render", "project_photo", "unit_photo", "floor_plan", "location_map", "brochure_offer",
           "generic_campaign", "unrelated", "uncertain"]

SYSTEM = f"""You classify an image posted in a WhatsApp chat about {rules.MARKET_NAME} real estate.
Look at the actual pixels and read any visible text, then read the surrounding messages.
- classification: what the image is. A campaign render is not a unit photo; a masterplan is not a photo.
- project_name: only if BOTH the image content and the messages tie it to that project. Otherwise "".
  Never use outside knowledge to recognise a project.
- visual_match: the image itself shows something identifying the project (logo, name, signage, text).
- context_match: the caption/surrounding messages explicitly refer to that project.
- Cars, lifestyle stock photos, and other unrelated pictures are "unrelated" with project_name "".
- confidence "high" only when visual_match and context_match are both true.
- rationale: one or two sentences citing what you saw and which message you used."""

SCHEMA = llm.obj({
    "classification": {"type": "string", "enum": CLASSES}, "project_name": llm.STR, "phase_label": llm.STR,
    "unit_code": llm.STR, "visual_match": llm.BOOL, "context_match": llm.BOOL,
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]}, "rationale": llm.STR})

MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def classify(log, limit=200):
    with db.connect() as c:
        files = rows.scoped_file_ids(c)
        todo = [f for f in c.execute(
            "SELECT * FROM source_files WHERE kind='image' AND id NOT IN (SELECT file_id FROM media_assignments)"
        ).fetchall() if f["id"] in files][:limit]
        known = [r[0] for r in c.execute(
            "SELECT DISTINCT project_name FROM label_mappings WHERE decision='project' AND project_name<>''")]
    log(f"{len(todo)} images to classify")
    for f in todo:
        mt = (f["mimetype"] or "image/jpeg").split(";")[0]
        if mt not in MEDIA_TYPES or (f["size"] or 0) > 4_500_000:
            state, out = "withheld", {"classification": "uncertain", "project_name": "", "phase_label": "",
                                      "unit_code": "", "confidence": "low",
                                      "rationale": "image format/size not reviewable automatically"}
        else:
            with db.connect() as c:
                ctx = mapping.file_messages(c, f["id"], window=3)
            data = base64.b64encode(open(f["path"], "rb").read()).decode()
            content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
                       {"type": "text", "text": json.dumps({"known_projects": known, "messages": ctx},
                                                           ensure_ascii=False)}]
            try:
                out = llm.call_json(SYSTEM, content, SCHEMA, max_tokens=4000)
            except Exception as e:
                log(f"{f['file_name'] or f['sha256'][:8]}: failed: {e}")
                continue
            state = "proposed"
            if not (out["visual_match"] and out["context_match"] and out["confidence"] == "high"):
                state = "withheld"
        blocked = rules.image_blocked(out["rationale"])
        if blocked or out["classification"] == "unrelated":
            state = "excluded"
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO media_assignments VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (f["id"], out["classification"], out["project_name"], out["phase_label"], out["unit_code"],
                       out["rationale"], out["confidence"], state,
                       f"blocklisted: {blocked}" if blocked else None, int(time.time())))
    log("image review proposals done (approve them on the Review page)")
