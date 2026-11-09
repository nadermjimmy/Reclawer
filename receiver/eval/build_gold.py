"""Freeze person-made decisions into a gold set (JSONL), split by source kind.

Only decisions a PERSON made count (decided_by IS NULL; note `decided_by != 'brain'` in SQL silently drops them, because
a person's decided_by is NULL). Brain decisions are not gold; Reopen of a brain decision puts it back on Updates and the
person's later decision becomes gold.

matching.jsonl   one line per decided suggestion: what the chat said -> label
                 label.type: row (its row_key) | new_project | noise | wrong_project | rejected_unknown
extraction.jsonl one line per decided suggestion: the source (text, or file reference) -> field/value, decision
meta.json        counts per source kind and label type, date range, master import used

Run in the container:  python -m eval.build_gold [--out DIR]
"""
import argparse, datetime, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db, normalize, profiles  # noqa: E402

KINDS = ("message", "row", "pdf_page", "image")
CHAT_SQL = ("(CASE WHEN p.source_kind='message' THEN (SELECT sm.group_jid FROM source_messages sm "
            "WHERE sm.wa_id=p.source_ref) ELSE (SELECT sm.group_jid FROM source_rows sr "
            "JOIN file_occurrences fo ON fo.file_id=sr.file_id JOIN source_messages sm ON sm.wa_id=fo.wa_id "
            "WHERE sr.id=CAST(p.source_ref AS INTEGER) ORDER BY sm.ts LIMIT 1) END)")


def label_of(p):
    """The human label of one decided suggestion."""
    if p["state"] == "approved":
        # created a new row: it started as 'new' and ended on that same project (the sheet may have been chosen
        # at approval, so compare the project part of the key, not the whole key)
        proj = lambda k: (k or "||").split("|")[1]
        created_new = p["first_match_state"] == "new" and proj(p["row_key"]) == proj(p["first_row_key"])
        return {"type": "new_project"} if created_new else {"type": "row", "row_key": p["row_key"]}
    reason = p["reject_reason"]
    if reason in ("not_an_offer", "duplicate"):
        return {"type": "noise", "reason": reason}
    if reason == "wrong_project":
        return {"type": "wrong_project", "not_row_key": p["row_key"]}
    if reason == "wrong_value":  # the row was right, the value wasn't
        return {"type": "row", "row_key": p["row_key"], "value_wrong": True}
    return {"type": "rejected_unknown"}


def source_text(c, kind, ref, limit=4000):
    """The text the suggestion came from (messages, sheet rows, PDF page text); images carry a file reference."""
    if kind == "message":
        m = c.execute("SELECT text FROM source_messages WHERE wa_id=?", (ref,)).fetchone()
        return {"text": (m[0] or "")[:limit] if m else None}
    r = c.execute("SELECT r.kind, r.raw_json, r.file_id, r.row_number, f.file_name FROM source_rows r "
                  "JOIN source_files f ON f.id=r.file_id WHERE r.id=?", (int(ref) if str(ref).isdigit() else -1,)).fetchone()
    if not r:
        return {"text": None}
    raw = json.loads(r["raw_json"] or "{}")
    if r["kind"] == "sheet_row":
        return {"text": " | ".join(x.get("v", "") for x in raw.get("cells", []) if x.get("v", "").strip())[:limit],
                "file_id": r["file_id"], "file": r["file_name"]}
    if r["kind"] == "pdf_page":
        return {"text": (raw.get("text") or "")[:limit], "file_id": r["file_id"], "page": r["row_number"],
                "file": r["file_name"]}
    return {"text": None, "file_id": r["file_id"], "file": r["file_name"]}


def collect(c, since=None):
    q = (f"SELECT p.*, {CHAT_SQL} chat_jid FROM proposals p WHERE p.state IN ('approved','rejected') "
         "AND p.decided_by IS NULL AND p.decided_at IS NOT NULL")
    args = []
    if since:
        q += " AND p.decided_at>=?"
        args.append(since)
    matching, extraction = [], []
    devs = profiles.master_developers(c)
    for p in c.execute(q + " ORDER BY p.decided_at, p.id", args).fetchall():
        prof = profiles.get(c, p["chat_jid"], devs) if p["chat_jid"] else None
        base = {"id": p["id"], "source_kind": p["source_kind"], "source_ref": p["source_ref"],
                "project": p["project"], "developer": p["developer"] or "", "chat_jid": p["chat_jid"],
                "chat_developer": prof["developer"] if prof else "", "field": p["field"], "sheet": p["sheet"] or "",
                "value": p["new_raw"], "quote": (p["quote"] or "")[:400], "created_at": p["created_at"],
                "decided_at": p["decided_at"], "first_row_key": p["first_row_key"],
                "first_match_state": p["first_match_state"], "script": "ar" if normalize.is_arabic(p["project"]) else "lat"}
        matching.append(dict(base, label=label_of(p)))
        extraction.append(dict(base, decision=p["state"], reason=p["reject_reason"], row_key=p["row_key"],
                               source=source_text(c, p["source_kind"], p["source_ref"])))
    return matching, extraction


def meta_of(matching):
    by_kind = {k: 0 for k in KINDS}
    by_label = {}
    for m in matching:
        by_kind[m["source_kind"]] = by_kind.get(m["source_kind"], 0) + 1
        by_label[m["label"]["type"]] = by_label.get(m["label"]["type"], 0) + 1
    ts = [m["decided_at"] for m in matching]
    return {"total": len(matching), "by_source_kind": by_kind, "by_label": by_label,
            "from": min(ts) if ts else None, "to": max(ts) if ts else None, "built_at": int(time.time())}


def build(out_dir=None, since=None):
    with db.connect() as c:
        matching, extraction = collect(c, since)
        imp = c.execute("SELECT id, file_name, imported_at FROM master_imports ORDER BY id DESC LIMIT 1").fetchone()
    meta = dict(meta_of(matching), master=dict(imp) if imp else None)
    if out_dir is None:
        out_dir = os.path.join(db.DATA_DIR, "eval", "gold-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M"))
    os.makedirs(out_dir, exist_ok=True)
    for name, items in (("matching.jsonl", matching), ("extraction.jsonl", extraction)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return out_dir, meta


def load(gold_dir):
    def read(name):
        with open(os.path.join(gold_dir, name), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    with open(os.path.join(gold_dir, "meta.json")) as f:
        meta = json.load(f)
    return read("matching.jsonl"), read("extraction.jsonl"), meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out")
    a = ap.parse_args()
    db.init()
    path, meta = build(a.out)
    print(path)
    print(json.dumps(meta, indent=2))
