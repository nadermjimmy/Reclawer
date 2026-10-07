"""Turn downloaded workbooks, CSVs and PDFs into source_rows, keeping every cell exactly as stored."""
import csv, datetime, io, json, re
import db, rules

PARSER_VERSION = "files-1"

# Header text -> column role. Order matters: the first match wins.
ROLES = [
    ("status", r"status|availab|الحالة|حالة|متاح"),
    ("source_price_per_area", r"per\s*(sq|ft|m\b)|psf|/\s*sq|سعر\s*المتر|للمتر|متر\s*/"),
    ("payment_plan", r"payment|instal+ment|down\s*pay|سداد|تقسيط|مقدم|أقساط|اقساط"),
    ("price", r"price|\baed\b|\begp\b|amount|selling|asking|\bcost\b|\bvalue\b|سعر|ثمن|إجمالي|اجمالي"),
    ("handover", r"handover|hand\s*over|completion|delivery|استلام|تسليم"),
    ("service_charge", r"service|صيانة|وديعة"),
    ("area", r"area|\bbua\b|size|sq\.?\s*ft|sqft|sq\.?\s*m|sqm|ft2|m2|suite|balcony|terrace|gfa|saleable|garden"
             r"|roof|مساحة|المساحة|م2|م²|متر|حديقة|رووف"),
    ("unit_type", r"type|bed|bhk|\bbr\b|category|configuration|layout|نوع|غرف"),
    ("floor", r"floor|level|\blvl\b|الدور|دور|طابق"),
    ("view", r"view|facing|orientation|إطلالة|اطلالة|فيو"),
    ("project", r"^\s*project|project\s*name|مشروع|المشروع|كمبوند"),
    ("phase", r"phase|building|bldg|tower|block|cluster|wing|zone|مرحلة|المرحلة|عمارة|برج|بلوك|زون"),
    ("unit_code", r"unit|\bapt|apartment|villa|plot|property|^no\.?$|number|\bref\b|code|الوحدة|وحدة|كود|رقم"),
    ("notes", r"remark|note|comment|condition|restriction|ملاحظات|ملاحظة"),
]
SQFT = re.compile(r"sq\.?\s*f|sqft|ft2|ft²|square\s*f", re.I)
SQM = re.compile(r"sq\.?\s*m|sqm|m2|m²|square\s*m|م2|م²|متر", re.I)


def role_of(header):
    h = (header or "").strip().lower()
    if not h:
        return None
    for role, pat in ROLES:
        if re.search(pat, h):
            return role
    return None


def cell_text(v):
    """Exact text of a stored cell value (no rounding, no thousands separators)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        return s[:-2] if s.endswith(".0") else s
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    return str(v)


def cell_type(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "b"
    if isinstance(v, (int, float)):
        return "n"
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return "d"
    return "s"


def detect_layout(rows):
    """Pick the header row (most recognised roles in the first 40 rows) and map its columns."""
    best, best_score = None, 1
    for i, cells in enumerate(rows[:40]):
        roles = {role_of(c["v"]) for c in cells if c["t"] == "s"} - {None}
        score = len(roles) + (2 if "unit_code" in roles else 0)
        if score > best_score and ("unit_code" in roles or "price" in roles):
            best, best_score = i, score
    if best is None:
        return None, []
    cols = []
    for idx, c in enumerate(rows[best]):
        header = c["v"].strip()
        if not header:
            continue
        cols.append({"index": idx, "header": header, "role": role_of(header),
                     "area_unit": "sq_ft" if SQFT.search(header) else "sq_m" if SQM.search(header) else "",
                     "currency": rules.CURRENCY if rules.CURRENCY_RE.search(header) else ""})
    # a sheet has one unit-code column; extra matches are kept as notes-like source columns
    seen = set()
    for col in cols:
        if col["role"] in ("unit_code", "project", "phase", "status", "price") and col["role"] in seen:
            col["role"] = "extra"
        seen.add(col["role"])
    return best, cols


def section_label(cells):
    """A row holding a single text value (e.g. a merged title like 'Tower 2' or 'Phase 3 - SOLD OUT')."""
    vals = [c["v"].strip() for c in cells if c["v"].strip()]
    if len(vals) == 1 and cells[[c["v"].strip() for c in cells].index(vals[0])]["t"] == "s":
        return vals[0]
    return None


def store_table(c, file_id, sheet, rows, hidden_rows=(), hidden_sheet=False, kind="sheet_row"):
    header_row, cols = detect_layout(rows)
    label = None
    for i, cells in enumerate(rows):
        if not any(x["v"] for x in cells):
            continue
        s = section_label(cells)
        if s and i != header_row:
            label = s
        c.execute("INSERT OR IGNORE INTO source_rows(file_id,sheet,row_number,kind,section_label,hidden,raw_json)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (file_id, sheet, i + 1, kind, label, 1 if (hidden_sheet or (i + 1) in hidden_rows) else 0,
                   json.dumps({"cells": cells}, ensure_ascii=False)))
    if header_row is not None:
        c.execute("INSERT OR IGNORE INTO sheet_layouts(file_id,sheet,header_row,columns_json,source) "
                  "VALUES(?,?,?,?,'auto')", (file_id, sheet, header_row + 1, json.dumps(cols)))
    return header_row is not None


def parse_workbook(c, f):
    import openpyxl
    wb = openpyxl.load_workbook(f["path"], data_only=True)
    tables = 0
    for ws in wb.worksheets:
        hidden_rows = {i for i, d in ws.row_dimensions.items() if d.hidden}
        rows = []
        for row in ws.iter_rows():
            rows.append([{"c": cell.column_letter, "v": cell_text(cell.value), "t": cell_type(cell.value),
                          "fmt": cell.number_format if cell.value is not None else ""}
                         for cell in row])
        tables += store_table(c, f["id"], ws.title, rows, hidden_rows, ws.sheet_state != "visible")
    return f"{len(wb.worksheets)} sheets, {tables} with a recognisable unit table"


def parse_csv(c, f):
    raw = open(f["path"], "rb").read()
    text = raw.decode("utf-8-sig", errors="replace")
    rows = [[{"c": str(i + 1), "v": v, "t": "s", "fmt": ""} for i, v in enumerate(r)]
            for r in csv.reader(io.StringIO(text))]
    found = store_table(c, f["id"], "csv", rows)
    return "unit table found" if found else "no unit table recognised"


def parse_pdf(c, f):
    from pypdf import PdfReader
    reader = PdfReader(f["path"])
    for n, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        c.execute("INSERT OR IGNORE INTO source_rows(file_id,sheet,row_number,kind,raw_json) VALUES(?,?,?,?,?)",
                  (f["id"], "pdf", n, "pdf_page", json.dumps({"text": text}, ensure_ascii=False)))
    return f"{len(reader.pages)} pages"


PARSERS = {"workbook": parse_workbook, "csv": parse_csv, "pdf": parse_pdf}


def parse_all(log, reparse=False):
    with db.connect() as c:
        q = "SELECT * FROM source_files WHERE kind IN ('workbook','csv','pdf')"
        if not reparse:
            q += " AND parse_status='pending'"
        files = c.execute(q).fetchall()
    for f in files:
        if f["path"].lower().endswith(".xls"):
            status, note = "unsupported", "old .xls format - re-save as .xlsx"
        else:
            try:
                with db.connect() as c:
                    note = PARSERS[f["kind"]](c, f)
                status = "parsed"
            except Exception as e:
                status, note = "failed", str(e)[:500]
        with db.connect() as c:
            c.execute("UPDATE source_files SET parse_status=?, parse_error=?, parser_version=? WHERE id=?",
                      (status, None if status == "parsed" else note, PARSER_VERSION, f["id"]))
        log(f"{f['file_name']}: {status} - {note}")
    log(f"{len(files)} files processed")
