"""Fake Evolution data + fake Claude answers used by the tests."""
import base64, io, json
import openpyxl

UAE, EGY = "111@g.us", "222@g.us"


def workbook(sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = ["Unit No", "Type", "Floor", "View", "Area (sq ft)", "Price (AED)", "Status"]
BRABUS = workbook({
    "Tower 1": [["BRABUS ISLAND - TOWER 1"], HEADER,
                ["0102", "1BR", "1", "Sea", 800, 950000, "Available"],
                ["0103", "2BR", "1", "Sea", 1200.5, 2100000, "Sold"],
                ["0104", "2BR", "1", "City", 1100, "1.2M", ""],
                ["0102", "1BR", "1", "Sea", 800, 950000, "Available"]],
    "Tower 2": [["Tower 2"], HEADER, ["T2-501", "3BR", "5", "Sea", 2000, 4000000, "Reserved"]],
})
HILLS = workbook({"Dubai Hills": [["No Flip or Change the Unit"], ["Unit", "Size (sqm)", "Price AED", "Status"],
                                  ["A-1", 100, 1500000, "available"]]})
VERDANA = workbook({"Availability": [["Unit", "Phase", "Area", "Price AED", "Status"],
                                     ["V-1", "Phase 3", 900, 700000, "Available"],
                                     ["V-2", "Phase 7", 950, 750000, "Available"]]})
EGYPT = workbook({"Sheet1": [["Unit", "Price", "Status"], ["E-1", 5000000, "Available"]]})
FILES = {"doc1": ("Brabus availability.xlsx", BRABUS), "doc2": ("Hills list.xlsx", HILLS),
         "doc3": ("Verdana.xlsx", VERDANA), "doc4": ("egypt.xlsx", EGYPT), "doc5": ("Brabus copy.xlsx", BRABUS)}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def rec(jid, wid, ts, text="", doc=None, sender="Ali"):
    msg = {"conversation": text} if not doc else {
        "documentWithCaptionMessage": {"message": {"documentMessage": {
            "fileName": FILES[doc][0], "mimetype": XLSX, "caption": text}}}}
    return {"key": {"remoteJid": jid, "id": wid, "participant": "9715@s.whatsapp.net"}, "pushName": sender,
            "messageTimestamp": ts, "messageType": "documentWithCaptionMessage" if doc else "conversation",
            "message": msg}


HISTORY = {
    UAE: [rec(UAE, "m1", 1000, "Brabus Island handover Q4 2027"),
          rec(UAE, "doc1", 1010, "Brabus Island availability", doc="doc1"),
          rec(UAE, "doc2", 1020, "Hills units, no flip", doc="doc2"),
          rec(UAE, "doc3", 1030, "Verdana latest", doc="doc3"),
          rec(UAE, "m2", 1040, "Correction? Brabus Island handover Q2 2028"),
          rec(UAE, "doc5", 1050, "resend", doc="doc5"),
          rec(UAE, "gone", 1060, "old file", doc="doc1")],
    EGY: [rec(EGY, "doc4", 1100, "Egypt list", doc="doc4")],
}


class FakeEvolution:
    def fetch_groups(self):
        return [{"id": UAE, "subject": "UAE Inventory"}, {"id": EGY, "subject": "Egypt"}]

    def find_messages(self, jid, page, page_size=100):
        return HISTORY.get(jid, []), 1

    def media_base64(self, wa_id):
        if wa_id == "gone":
            raise RuntimeError("404 media expired")
        name, data = FILES[wa_id]
        return {"base64": base64.b64encode(data).decode(), "fileName": name, "mimetype": XLSX}


def fake_llm(system, content, schema, max_tokens=16000):
    if "map rows of real-estate availability" in system:
        out = []
        for ctx in json.loads(content)["contexts"]:
            f, sheet, section = ctx["file_name"], ctx["sheet"], ctx["section_title"]
            m = {"key": ctx["key"], "decision": "project", "developer": "", "project_name": "",
                 "phase_label": "", "confidence": "high", "is_uae": "yes", "rationale": f"file {f}"}
            if f.startswith("Brabus"):
                m.update(project_name="Brabus Island" if sheet == "Tower 1" else "Brabus Island Tower 2",
                         phase_label="Tower 1" if sheet == "Tower 1" else "")
            elif f.startswith("Hills"):
                m.update(project_name="Dubai Hills")  # owner rule must reject this
            elif f.startswith("Verdana"):
                m.update(project_name="Verdana", phase_label=ctx["phase_cell"])
            out.append(m)
        return {"mappings": out}
    if "extract factual statements" in system:
        facts = []
        for m in json.loads(content)["messages"]:
            if "handover" in (m.get("text") or ""):
                facts.append({"source_id": m["source_id"], "project_label": "Brabus Island", "phase_label": "",
                              "fact_type": "handover", "raw_value": m["text"].split("handover ")[1],
                              "parsed_value": m["text"].split("handover ")[1], "ambiguous": False,
                              "updates_previous": False})
        return {"facts": facts}
    raise AssertionError("unexpected prompt")


_done = False


def run_pipeline():
    """Run every pipeline step once against the fakes (shared by all test modules)."""
    global _done
    import db, history, parse_files, mapping, facts, build, llm, evolution, jobs
    fake = FakeEvolution()
    for name in ("fetch_groups", "find_messages", "media_base64"):
        setattr(evolution, name, getattr(fake, name))
    llm.call_json = fake_llm
    if _done:
        return db
    db.init()
    jobs.run_now("sync", history.sync_groups)
    with db.connect() as c:
        c.execute("UPDATE group_scope SET in_scope=1 WHERE jid=?", (UAE,))
    for step in (history.pull_history, history.download_media, parse_files.parse_all, mapping.propose,
                 facts.extract_all, build.rebuild):
        jobs.run_now(step.__name__, step)
    _done = True
    return db
