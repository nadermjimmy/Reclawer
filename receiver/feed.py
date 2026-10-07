"""Read-only channel feed: every chat rendered like a WhatsApp channel (JSON API + page)."""
import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
import db, history, parse_files

router = APIRouter()
SKIP_TYPES = ("reactionMessage", "protocolMessage", "senderKeyDistributionMessage", "pollUpdateMessage",
              "messageContextInfo", "editedMessage")
VISIBLE = f"(m.text<>'' OR m.has_media=1 OR m.file_name IS NOT NULL) AND m.message_type NOT IN {SKIP_TYPES}"
PREVIEW_ROWS, PREVIEW_COLS = 400, 40


def kind_of_message(m):
    t = m["message_type"] or ""
    if t == "imageMessage":
        return "image"
    if t == "videoMessage":
        return "video"
    if t == "documentMessage" or m["file_name"]:
        return history.kind_of(m["file_name"], m["mimetype"]) if m["file_name"] or m["mimetype"] else "document"
    return "text"


@router.get("/feed", response_class=HTMLResponse)
def feed_page(request: Request):
    from web import page
    return page(request, "feed.html")


@router.get("/api/feed/channels")
def channels(scope: str = "all"):
    sql = (f"SELECT g.jid, g.name, COALESCE(s.in_scope,0) in_scope, COUNT(m.wa_id) n, MAX(m.ts) last_ts, "
           f"SUM(m.has_media) media FROM groups g LEFT JOIN group_scope s ON s.jid=g.jid "
           f"JOIN source_messages m ON m.group_jid=g.jid AND {VISIBLE} ")
    if scope == "ticked":
        sql += "WHERE s.in_scope=1 "
    sql += "GROUP BY g.jid ORDER BY last_ts DESC"
    with db.connect() as c:
        rows = c.execute(sql).fetchall()
        out = []
        for r in rows:
            last = c.execute(f"SELECT m.text, m.file_name, m.message_type, m.sender_name FROM source_messages m "
                             f"WHERE m.group_jid=? AND {VISIBLE} ORDER BY m.ts DESC LIMIT 1", (r["jid"],)).fetchone()
            preview = (last["text"] or last["file_name"] or
                       {"imageMessage": "Photo", "videoMessage": "Video"}.get(last["message_type"], "Attachment"))
            out.append({"jid": r["jid"], "name": r["name"] or r["jid"], "kind": "group" if r["jid"].endswith("@g.us")
                        else "private", "in_scope": bool(r["in_scope"]), "count": r["n"], "media": r["media"] or 0,
                        "last_ts": r["last_ts"], "preview": preview[:140], "media_preview": not last["text"]})
    return out


def _file_info(c, file_id):
    f = c.execute("SELECT id, kind, file_name, mimetype, size FROM source_files WHERE id=?", (file_id,)).fetchone()
    return dict(f) if f else None


KIND_SQL = {
    "image": "m.message_type='imageMessage'",
    "pdf": "(lower(m.file_name) LIKE '%.pdf' OR m.mimetype='application/pdf')",
    "workbook": "(lower(m.file_name) LIKE '%.xls%' OR lower(m.file_name) LIKE '%.csv' OR m.mimetype LIKE '%sheet%' "
                "OR m.mimetype LIKE '%excel%' OR m.mimetype='text/csv')",
}


@router.get("/api/feed/messages")
def messages(jid: str, before: int = 0, q: str = "", kind: str = "", limit: int = 60):
    limit = max(10, min(limit, 150))
    sql = f"SELECT m.* FROM source_messages m WHERE m.group_jid=? AND {VISIBLE}"
    args = [jid]
    if before:
        sql += " AND m.ts<?"
        args.append(before)
    if q:
        sql += " AND (m.text LIKE ? OR m.file_name LIKE ?)"
        args += [f"%{q}%"] * 2
    if kind in KIND_SQL:
        sql += " AND " + KIND_SQL[kind]
    with db.connect() as c:
        rows = c.execute(sql + " ORDER BY m.ts DESC LIMIT ?", args + [limit + 1]).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        files = {r["wa_id"]: r["file_id"] for r in c.execute(
            f"SELECT wa_id, file_id FROM file_occurrences WHERE wa_id IN ({','.join('?' * len(rows))})",
            [r["wa_id"] for r in rows])} if rows else {}
        failed = {r[0] for r in c.execute(
            f"SELECT wa_id FROM media_fetch WHERE status='failed' AND wa_id IN ({','.join('?' * len(rows))})",
            [r["wa_id"] for r in rows])} if rows else set()
        out = []
        for m in reversed(rows):
            kind = kind_of_message(m)
            fid = files.get(m["wa_id"])
            out.append({"id": m["wa_id"], "ts": m["ts"], "sender": m["sender_name"], "text": m["text"] or "",
                        "kind": kind, "file_name": m["file_name"], "mimetype": m["mimetype"],
                        "file": _file_info(c, fid) if fid else None,
                        "media": "ok" if fid else "failed" if m["wa_id"] in failed else
                                 ("none" if kind == "text" else "pending")})
    return {"messages": out, "has_more": more}


@router.post("/api/feed/media/{wa_id}")
def fetch_media(wa_id: str):
    try:
        file_id = history.fetch_media(wa_id)
    except Exception as e:
        return JSONResponse({"error": str(e)[:300]}, 422)
    with db.connect() as c:
        return {"file": _file_info(c, file_id)}


@router.get("/api/feed/preview/{file_id}")
def preview(file_id: int):
    """Sheet grid for workbooks/CSVs (cells exactly as stored), page count for PDFs."""
    with db.connect() as c:
        f = c.execute("SELECT * FROM source_files WHERE id=?", (file_id,)).fetchone()
    if not f or not os.path.exists(f["path"]):
        return JSONResponse({"error": "file not found"}, 404)
    try:
        if f["kind"] == "workbook" and not f["path"].lower().endswith(".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(f["path"], read_only=True, data_only=True)
            sheets = []
            for ws in wb.worksheets:
                rows, total = [], 0
                for row in ws.iter_rows(values_only=True):
                    vals = [parse_files.cell_text(v) for v in row[:PREVIEW_COLS]]
                    if not any(vals):
                        continue
                    total += 1
                    if len(rows) < PREVIEW_ROWS:
                        rows.append(vals)
                width = max((len(r) for r in rows), default=0)
                while width and not any(len(r) >= width and r[width - 1] for r in rows):
                    width -= 1
                sheets.append({"name": ws.title, "rows": [r[:width] + [""] * (width - len(r)) for r in rows],
                               "total_rows": total, "hidden": ws.sheet_state != "visible"})
            wb.close()
            return {"type": "sheets", "sheets": sheets}
        if f["kind"] == "csv":
            import csv, io
            text = open(f["path"], "rb").read().decode("utf-8-sig", errors="replace")
            rows = [r[:PREVIEW_COLS] for r in csv.reader(io.StringIO(text)) if any(r)]
            return {"type": "sheets", "sheets": [{"name": "CSV", "rows": rows[:PREVIEW_ROWS], "total_rows": len(rows),
                                                  "hidden": False}]}
        if f["kind"] == "pdf":
            from pypdf import PdfReader
            return {"type": "pdf", "pages": len(PdfReader(f["path"]).pages)}
    except Exception as e:
        return JSONResponse({"error": f"couldn't read this file: {e}"[:300]}, 422)
    return {"type": "other"}
