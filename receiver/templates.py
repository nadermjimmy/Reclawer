"""Spreadsheet templates (Phase 5): once a person confirms which column of a developer's price list holds which master
field, later sheets with the same layout are read in code, with no LLM call.

fingerprint = hash(normalized header cells + column count + developer key). The confirmed mapping is
{column index: role}, role = project / developer / location / a proposable master field. Rows are turned into the
same update dicts Claude returns and go through master.add_proposal, so the reading-order gate, rounding, matching
and the brain behave exactly as for Claude's output. Behind TEMPLATES=1 (default off).
"""
import hashlib, json, os, time
import db, master_schema as ms, normalize

TEMPLATES = os.getenv("TEMPLATES", "0") == "1"
ROLES = ("project", "developer", "location", *[f for f in ms.ALL_PROPOSABLE if f != "location"])


def _cells(raw_json):
    return [x.get("v", "") for x in json.loads(raw_json or "{}").get("cells", [])]


def header(c, file_id, sheet):
    """(header row number, [header texts]) from the detected layout, else the sheet's first non-empty row."""
    lay = c.execute("SELECT header_row FROM sheet_layouts WHERE file_id=? AND sheet=?", (file_id, sheet)).fetchone()
    q = "SELECT row_number, raw_json FROM source_rows WHERE file_id=? AND sheet=? AND kind='sheet_row'"
    r = (c.execute(q + " AND row_number=?", (file_id, sheet, lay[0])).fetchone() if lay and lay[0] else None) or \
        c.execute(q + " ORDER BY row_number LIMIT 1", (file_id, sheet)).fetchone()
    return (r["row_number"], _cells(r["raw_json"])) if r else (None, [])


def fingerprint(headers, developer=""):
    norm = [normalize.normalize(h) for h in headers]
    while norm and not norm[-1]:
        norm.pop()
    return hashlib.sha1(json.dumps([norm, len(norm), normalize.compact(developer)]).encode()).hexdigest()[:16]


def suggest(headers):
    """Best guess per column from the master's own header names (the person confirms or corrects it)."""
    out = {}
    for i, h in enumerate(headers):
        f = ms.field_of(h)
        if f in ROLES:
            out[str(i)] = f
    return out


def get(c, fp):
    r = c.execute("SELECT * FROM sheet_templates WHERE fingerprint=?", (fp,)).fetchone()
    return dict(r, mapping=json.loads(r["mapping_json"])) if r else None


def describe(c, file_id, sheet, developer=""):
    hrow, headers = header(c, file_id, sheet)
    fp = fingerprint(headers, developer)
    t = get(c, fp)
    return {"fingerprint": fp, "header_row": hrow, "headers": headers, "developer": developer,
            "mapping": t["mapping"] if t else suggest(headers), "confirmed": bool(t),
            "confirmed_by": t["confirmed_by"] if t else None, "roles": list(ROLES)}


def confirm(c, file_id, sheet, mapping, developer="", by="team"):
    """Store a person's column mapping. Needs a project column and at least one proposable field; never sales data."""
    mapping = {str(k): v for k, v in (mapping or {}).items() if v}
    bad = [v for v in mapping.values() if v not in ROLES]
    if bad:
        raise ValueError("Unknown column role(s): " + ", ".join(bad))
    if "project" not in mapping.values() or not any(v not in ("project", "developer", "location") for v in mapping.values()):
        raise ValueError("Map a project column and at least one value column")
    hrow, headers = header(c, file_id, sheet)
    fp = fingerprint(headers, developer)
    c.execute("INSERT OR REPLACE INTO sheet_templates(fingerprint,developer_key,headers_json,mapping_json,confirmed_by,"
              "confirmed_at,example_file_id,example_sheet) VALUES(?,?,?,?,?,?,?,?)",
              (fp, normalize.compact(developer), json.dumps(headers, ensure_ascii=False), json.dumps(mapping), by,
               int(time.time()), file_id, sheet))
    return fp


def updates_for(rows, mapping, header_row):
    """Claude-shaped updates for every data row: [{source_id, developer, project, location_code, field, value, quote}]."""
    out = []
    for r in rows:
        if r["row_number"] == header_row:
            continue
        cells = _cells(r["raw_json"])
        get_ = lambda role: next((cells[int(i)].strip() for i, v in mapping.items()
                                  if v == role and int(i) < len(cells) and cells[int(i)].strip()), "")
        project = get_("project")
        if not project:
            continue
        loc = get_("location").lower()
        for i, field in mapping.items():
            if field in ("project", "developer", "location") or int(i) >= len(cells) or not cells[int(i)].strip():
                continue
            out.append({"source_id": str(r["id"]), "developer": get_("developer"), "project": project,
                        "location_code": loc if loc in ms.LOCATION_CODES else "", "field": field,
                        "value": cells[int(i)].strip(), "quote": " | ".join(x for x in cells if x.strip())[:300]})
    return out


def local_updates(c, file_id, sheet, rows, developer=""):
    """Under TEMPLATES=1 with a confirmed template: the updates read in code, else None (Claude reads it)."""
    if not TEMPLATES:
        return None
    hrow, headers = header(c, file_id, sheet)
    t = get(c, fingerprint(headers, developer))
    if not t:
        return None
    c.execute("UPDATE sheet_templates SET used=COALESCE(used,0)+1 WHERE fingerprint=?", (t["fingerprint"],))
    return updates_for(rows, t["mapping"], hrow)
