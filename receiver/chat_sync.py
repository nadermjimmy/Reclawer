"""Sync one chat on demand (Chats > Sync now): pull its latest history, download its files, read new files and
suggest sheet updates from that chat only. Runs as one job (jobs.start), so it shares the one-job-at-a-time lock.

The job log carries `PROGRESS {"stage": n, "of": 4, "label": ...}` lines for the stepper and ends with
`RESULT {"messages", "files", "files_failed", "suggestions", "failed_batches", ...}`. Listen-only: nothing here
sends anything to WhatsApp.
"""
import json, time
import db, history, master, parse_files, proposals

STAGES = ("Pull latest messages", "Download files", "Read files", "Suggest updates")


def _progress(log, n, **extra):
    log("PROGRESS " + json.dumps({"stage": n, "of": len(STAGES), "label": STAGES[n - 1], "done": n - 1,
                                  "total": len(STAGES), **extra}, ensure_ascii=False))


def sync_chat(log, jid, add_to_scope=False):
    with db.connect() as c:
        if add_to_scope:
            c.execute("INSERT INTO group_scope(jid,in_scope) VALUES(?,1) ON CONFLICT(jid) DO UPDATE SET in_scope=1",
                      (jid,))
        name = (c.execute("SELECT name FROM groups WHERE jid=?", (jid,)).fetchone() or [jid])[0]
    if add_to_scope:  # log only after the transaction above is committed (the log writes on its own connection)
        log("Added this chat to the inventory chats")
    log(f"Syncing {name}")
    result = {"messages": 0, "files": 0, "files_failed": 0, "suggestions": 0, "failed_batches": 0}

    def finish():
        log("RESULT " + json.dumps(result))
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO chat_syncs VALUES(?,?,?,?)",
                      (jid, getattr(log, "job_id", None), int(time.time()), json.dumps(result)))

    _progress(log, 1)
    try:
        result["messages"] = history.pull_history(log, jids=[jid])
    except Exception as e:
        log(f"Couldn't reach the WhatsApp bridge: {e}")
        result["error"] = "bridge_unreachable"
        log("RESULT " + json.dumps(result))
        raise

    _progress(log, 2)
    result["files"], result["files_failed"] = history.download_media(log, jids=[jid])

    _progress(log, 3)
    parse_files.parse_all(log)  # only files still pending

    _progress(log, 4)
    with db.connect() as c:
        has_master = master.current_import(c) is not None
    if not has_master:
        log("Skipped suggestions: upload the master workbook on the Master page to get suggestions")
        result["skipped"] = "no_master"
    else:
        for step in (proposals.from_messages, proposals.from_files):
            n, failed = step(log, jids=[jid])
            result["suggestions"] += n
            result["failed_batches"] += failed
    log("PROGRESS " + json.dumps({"stage": len(STAGES) + 1, "of": len(STAGES), "label": "Finished",
                                  "done": len(STAGES), "total": len(STAGES)}))
    finish()
