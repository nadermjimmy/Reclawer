"""Read-only export of every suggestion (all states) with its chat, quote, sheet row and the brain's verdict, as CSV
on stdout. Run on the server:  railway ssh --service receiver python export_suggestions.py > suggestions.csv"""
import csv, json, os, sqlite3, sys

H = ["id", "state", "decided_by", "reject_reason", "field", "old_raw", "new_raw", "project", "developer", "sheet",
     "match_state", "matched_label", "row_id", "warning", "quote", "source_kind", "source_ref", "source_ts",
     "created_at", "decided_at"]


def main(path=os.getenv("DB_PATH", "/data/messages.db"), out=sys.stdout):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # read-only: nothing on the server can change
    c.row_factory = sqlite3.Row
    groups = {r[0]: r[1] for r in c.execute("SELECT jid, name FROM groups")}
    msg_chat = {r[0]: r[1] for r in c.execute("SELECT wa_id, group_jid FROM source_messages")}
    row_chat = {str(r[0]): r[1] for r in c.execute(
        "SELECT sr.id, sm.group_jid FROM source_rows sr JOIN file_occurrences fo ON fo.file_id=sr.file_id "
        "JOIN source_messages sm ON sm.wa_id=fo.wa_id")}
    brain = {r[0]: r for r in c.execute("SELECT proposal_id, verdict, confidence, action, reason FROM brain_reviews")}
    rows = {r[0]: r for r in c.execute("SELECT id, developer, project FROM master_rows")}
    w = csv.writer(out)
    w.writerow(H + ["row_developer", "row_project", "chat_jid", "chat_name", "brain_verdict", "brain_confidence",
                    "brain_action", "brain_reason", "queue", "approval_chance", "gate_reasons", "group_developer"])
    cols = {r[1] for r in c.execute("PRAGMA table_info(proposals)")}
    for p in c.execute("SELECT * FROM proposals ORDER BY id"):
        jid = msg_chat.get(p["source_ref"]) if p["source_kind"] == "message" else row_chat.get(str(p["source_ref"]))
        r, b = rows.get(p["row_id"]), brain.get(p["id"])
        g = json.loads(p["gate_json"] or "{}") if "gate_json" in cols and p["gate_json"] else {}
        w.writerow([p[k] for k in H] + [r["developer"] if r else "", r["project"] if r else "", jid or "",
                                        groups.get(jid, ""), *(tuple(b)[1:] if b else ("", "", "", "")),
                                        p["tier"] if "tier" in cols else "", p["p_approve"] if "p_approve" in cols else "",
                                        " | ".join(g.get("reasons", [])), g.get("group_developer") or ""])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", newline="")
    main()
