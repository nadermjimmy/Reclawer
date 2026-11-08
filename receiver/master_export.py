"""Two downloads (decision 14):
(a) team_file: the uploaded master with approved changes written into the same cells (styles, filters,
    other sheets untouched); payment plans normalised to '10% - 8Y'; new/moved projects appended.
(b) sql_file: the same rows as flat, typed columns (one row per project per sheet) plus a long `units` sheet.
"""
import copy, datetime, io, json
import openpyxl
import cell_parse as cp, db, master, master_schema as ms


def _cell_value(field, raw, parsed):
    """What to write into the team file for one field."""
    if field == "payment_plan" and parsed.get("ok") and parsed.get("down_payment_pct") is not None:
        return cp.format_payment_plan(parsed)
    if field == "commission" and parsed.get("ok") and parsed.get("pct") is not None and not parsed.get("net"):
        return parsed["pct"]  # stays numeric, as the team types it
    if field == "ready_to_move" and parsed.get("ok") and parsed.get("rtm_price_egp") is not None \
            and parsed.get("rtm_area_sqm") is None and not parsed.get("rtm_bedrooms"):
        return parsed["rtm_price_egp"]
    return raw


def _last_data_row(ws, cols):
    keys = [cols[f] for f in ("developer", "project") if f in cols]
    for r in range(ws.max_row, 1, -1):
        if any(ws.cell(r, k).value not in (None, "") for k in keys):
            return r
    return 1


def _date_format(ws, col):
    for r in range(2, min(ws.max_row, 200) + 1):
        cell = ws.cell(r, col)
        if isinstance(cell.value, (datetime.date, datetime.datetime)):
            return cell.number_format
    return "d-mmm"


def team_file():
    with db.connect() as c:
        imp = master.current_import(c)
        if not imp:
            return None
        wb = openpyxl.load_workbook(imp["path"])
        sheets = {r["sheet"]: (r["title"], json.loads(r["columns_json"]))
                  for r in c.execute("SELECT * FROM master_sheets WHERE import_id=?", (imp["id"],))}
        changed = 0
        for r in c.execute("SELECT * FROM master_rows WHERE state NOT IN ('dropped')").fetchall():
            approved = {p["field"] for p in c.execute(
                "SELECT DISTINCT field FROM proposals WHERE row_id=? AND state='approved'", (r["id"],))}
            appended = r["row_number"] is None or r["state"] == "moved"
            target = master.home_sheet(r)
            if target not in sheets:
                continue
            title, cols = sheets[target]
            ws = wb[title]
            if appended:
                if not approved and r["state"] == "new":
                    continue
                row_no = _last_data_row(ws, cols) + 1
                for col in range(1, ws.max_column + 1):  # look like the row above
                    ws.cell(row_no, col)._style = copy.copy(ws.cell(row_no - 1, col)._style)
                for f in ("developer", "project"):
                    if f in cols and r[f]:
                        ws.cell(row_no, cols[f], r[f])
                fields = master.row_values(c, r)
            else:
                row_no = r["row_number"]
                fields = {f: master.current(c, r["id"], f) for f in approved}
                if "payment_plan" in cols:  # decision 10: normalise every payment plan
                    fields.setdefault("payment_plan", master.current(c, r["id"], "payment_plan"))
            for f, (raw, parsed) in fields.items():
                if f in cols and f != "date":
                    ws.cell(row_no, cols[f], _cell_value(f, raw, parsed))
            day = master.approval_date(c, r["id"])
            if "date" in cols and (day or appended):
                cell = ws.cell(row_no, cols["date"])
                fmt = _date_format(ws, cols["date"])
                cell.value = datetime.datetime.combine(day, datetime.time()) if day else cell.value
                cell.number_format = fmt
            if approved or appended:
                changed += 1
            if appended and ws.auto_filter.ref:
                start, _, end = ws.auto_filter.ref.replace("$", "").partition(":")
                col_letters = "".join(ch for ch in end if ch.isalpha())
                last = int("".join(ch for ch in end if ch.isdigit()) or 0)
                if row_no > last:
                    ws.auto_filter.ref = f"{start}:{col_letters}{row_no}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


UNIT_COLS = [s for u in ms.UNIT_FIELDS for s in (f"{u}_raw", f"{u}_area_sqm", f"{u}_price_egp", f"{u}_status")]
COLUMNS = {
    ms.GRID: ["location_code", "location_name", "rtm_raw", "row_status", "rtm_scope", "rtm_bedrooms",
              "rtm_area_sqm", "rtm_price_egp", "product_tags", "rtm_notes", *UNIT_COLS, "payment_plan_raw",
              "payment_plan", "down_payment_pct", "years"],
    ms.COMMISSION: ["voice_note", "commission_raw", "commission_pct", "commission_net", "sales_name",
                    "sales_contact", "feedback", "id_note", "row_status"],
    ms.SEPARATED: [s for u in ms.UNIT_FIELDS[1:5] for s in (f"{u}_raw", f"{u}_area_sqm", f"{u}_price_egp",
                                                             f"{u}_status")]
                  + ["commission_raw", "commission_pct", "commission_net", "sales_name", "sales_contact"],
}
BASE = ["master_sheet", "master_row", "row_state", "developer", "project", "project_key"]
TAIL = ["master_date", "last_approved_date"]
UNITS = ["master_sheet", "master_row", "developer", "project", "location_code", "unit_type", "area_sqm",
         "price_egp", "status", "raw", "down_payment_pct", "years"]


def _flat(kind, vals):
    get = lambda f: vals.get(f, ("", ms.parse(f, None)))
    out = {}
    for u in ms.UNIT_FIELDS:
        raw, p = get(u)
        out.update({f"{u}_raw": raw, f"{u}_area_sqm": p.get("area_sqm"), f"{u}_price_egp": p.get("price_egp"),
                    f"{u}_status": p.get("status")})
    raw, p = get("ready_to_move")
    out.update(rtm_raw=raw, row_status=p.get("row_status"), rtm_scope=p.get("rtm_scope"),
               rtm_bedrooms=p.get("rtm_bedrooms"), rtm_area_sqm=p.get("rtm_area_sqm"),
               rtm_price_egp=p.get("rtm_price_egp"), product_tags=",".join(p.get("product_tags") or []),
               rtm_notes=p.get("notes"))
    raw, p = get("payment_plan")
    out.update(payment_plan_raw=raw, payment_plan=cp.format_payment_plan(p) if p.get("ok") else "",
               down_payment_pct=p.get("down_payment_pct"), years=p.get("years"))
    raw, p = get("commission")
    out.update(commission_raw=raw, commission_pct=p.get("pct"), commission_net=p.get("net") if raw else None)
    for f in ("voice_note", "sales_name", "sales_contact", "feedback", "id_note"):
        out[f] = get(f)[0]
    raw, p = get("date")
    out["master_date"] = p.get("date")
    if kind == ms.COMMISSION:
        out["row_status"] = p.get("row_status")
    code = get("location")[0].strip().lower()
    out.update(location_code=code, location_name=ms.LOCATION_CODES.get(code, ""))
    return out


def sql_file():
    wb = openpyxl.Workbook(write_only=True)
    with db.connect() as c:
        if not master.current_import(c):
            return None
        sheets = {}
        units = wb.create_sheet("units")
        units.append(UNITS)
        for r in c.execute("SELECT * FROM master_rows WHERE state NOT IN ('dropped') ORDER BY id").fetchall():
            sheet = master.home_sheet(r)
            kind = ms.sheet_kind(sheet)
            if kind is None:
                continue
            if sheet not in sheets:
                sheets[sheet] = wb.create_sheet(sheet[:31])
                sheets[sheet].append(BASE + COLUMNS[kind] + TAIL)
            vals = master.row_values(c, r)
            if r["state"] == "new" and not vals:
                continue
            flat = _flat(kind, vals)
            day = master.approval_date(c, r["id"])
            flat.update(master_sheet=sheet, master_row=r["row_number"], row_state=r["state"], developer=r["developer"],
                        project=r["project"], project_key=r["project_key"],
                        last_approved_date=day.isoformat() if day else None)
            sheets[sheet].append([flat.get(k) for k in BASE + COLUMNS[kind] + TAIL])
            for u in ms.PROPOSABLE[kind]:
                if u in ms.UNIT_FIELDS and u in vals and vals[u][0]:
                    p = vals[u][1]
                    units.append([sheet, r["row_number"], r["developer"], r["project"], flat.get("location_code"),
                                  ms.UNIT_LABELS[u], p.get("area_sqm"), p.get("price_egp"), p.get("status"),
                                  vals[u][0], flat.get("down_payment_pct"), flat.get("years")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
