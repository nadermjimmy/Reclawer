"""Re-match suggestions against the current master without reading anything again (no Claude calls).

Used after a master upload: the open suggestions are pointed at the new rows (and their 'old value' becomes the
team's current value) instead of being thrown away, and with the accuracy gate on, every suggestion is moved onto its
group's own developer. Suggestions the new master already contains, or that a better source already filled, stay
closed. Only one open suggestion per cell survives (the newest), as always.
"""
import sqlite3, time
import db, master, window
from gate import CHAT_SQL


def candidates(c):
    """Suggestions superseded by the latest master upload (or still open), from the reading window."""
    imp = master.current_import(c)
    since = window.cutoff()
    q = (f"SELECT p.*, {CHAT_SQL} chat_jid FROM proposals p WHERE p.decided_at IS NULL "
         "AND p.state IN ('proposed','superseded') AND COALESCE(p.source_ts,0)>=?")
    args = [since]
    if imp:
        q += " AND (p.state='proposed' OR (p.row_id IS NULL AND p.created_at<=?))"
        args.append(imp["imported_at"])
    return [r["id"] for r in c.execute(q + " ORDER BY COALESCE(p.source_ts,0), p.id", args)]


def run(log=lambda *_: None, ids=None):
    """Re-match `ids` (default: candidates). Returns {open, closed, kept}."""
    with db.connect() as c:
        ids = candidates(c) if ids is None else list(ids)
        opened, closed, skipped = [], 0, 0
        for pid in ids:
            p = c.execute(f"SELECT p.*, {CHAT_SQL} chat_jid FROM proposals p WHERE p.id=?", (pid,)).fetchone()
            if not p or p["decided_at"]:
                continue
            item = {"project": p["project"], "developer": p["developer"] or "", "field": p["field"],
                    "value": p["new_raw"], "location_code": p["location_code"] or "", "quote": p["quote"] or "",
                    "source_kind": p["source_kind"], "source_ref": p["source_ref"], "source_ts": p["source_ts"],
                    "chat_jid": p["chat_jid"]}
            targets, info = master.plan_targets(c, item)
            if not targets:
                if p["state"] == "proposed":
                    c.execute("UPDATE proposals SET state='superseded' WHERE id=?", (pid,))
                    closed += 1
                continue
            t = next((t for t in targets if t[0] == p["sheet"]), targets[0])
            sheet, row, state, label, old_raw, warn = t
            parsed, new_raw, project, key, loc, developer = info
            rkey = master.row_key_of(row) if row is not None else master.normalize.row_key(developer, project, sheet)
            try:
                c.execute("UPDATE proposals SET state='proposed', row_id=?, sheet=?, old_raw=?, match_state=?, "
                          "matched_label=?, warning=?, row_key=?, developer=?, gated_at=NULL WHERE id=?",
                          (row["id"] if row is not None else None, sheet, old_raw, state, label, "; ".join(warn),
                           rkey, developer, pid))
                opened.append(pid)
            except sqlite3.IntegrityError:
                skipped += 1  # an identical suggestion already sits on that cell
        for pid in opened:
            if c.execute("SELECT state FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "proposed":
                master._supersede(c, pid)
        still = [pid for pid in opened
                 if c.execute("SELECT state FROM proposals WHERE id=?", (pid,)).fetchone()[0] == "proposed"]
    log(f"Re-matched {len(ids)} suggestion(s): {len(still)} open on the current master, {closed} no longer needed, "
        f"{skipped} duplicate(s) skipped")
    import gate
    gate.run(log, ids=still)
    return {"open": len(still), "closed": closed, "skipped": skipped}
