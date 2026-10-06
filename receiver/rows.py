"""Read unit rows out of source_rows using each sheet's column layout."""
import json
import db


def layouts(c):
    return {(r["file_id"], r["sheet"]): (r["header_row"], json.loads(r["columns_json"] or "[]"))
            for r in c.execute("SELECT * FROM sheet_layouts")}


def scoped_file_ids(c):
    """Files posted at least once in an in-scope group."""
    scoped = db.scoped_groups(c)
    if not scoped:
        return set()
    q = ("SELECT DISTINCT o.file_id FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
         f"WHERE m.group_jid IN ({','.join('?' * len(scoped))})")
    return {r[0] for r in c.execute(q, scoped)}


def values(cells, cols):
    """{role: [(column, header, value, area_unit, currency), ...]} for one row."""
    out = {}
    for col in cols:
        if col["index"] >= len(cells):
            continue
        v = cells[col["index"]]["v"].strip()
        out.setdefault(col["role"] or "extra", []).append(
            (cells[col["index"]]["c"], col["header"], v, col.get("area_unit", ""), col.get("currency", "")))
    return out


def first(vals, role):
    return next((x[2] for x in vals.get(role, []) if x[2]), "")


def data_rows(c, file_ids=None):
    """Yield (source_row, vals) for every row that holds a unit."""
    lay = layouts(c)
    q = "SELECT * FROM source_rows WHERE kind='sheet_row' ORDER BY file_id, sheet, row_number"
    for r in c.execute(q).fetchall():
        if file_ids is not None and r["file_id"] not in file_ids:
            continue
        header_row, cols = lay.get((r["file_id"], r["sheet"]), (None, []))
        if header_row is None or r["row_number"] <= header_row:
            continue
        cells = json.loads(r["raw_json"])["cells"]
        vals = values(cells, cols)
        code = first(vals, "unit_code")
        headers = {col["header"] for col in cols}
        if code and code in headers:  # repeated header row inside the sheet
            continue
        if not code and not (first(vals, "price") and (first(vals, "area") or first(vals, "unit_type"))):
            continue
        filled = sum(1 for col in cols if col["index"] < len(cells) and cells[col["index"]]["v"].strip())
        if filled < 2:  # a lone value is a title or note, not a unit
            continue
        yield r, vals


def context_key(r, vals):
    return json.dumps([r["file_id"], r["sheet"], r["section_label"] or "", first(vals, "project"),
                       first(vals, "phase")], ensure_ascii=False)
