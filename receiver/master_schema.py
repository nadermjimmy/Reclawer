"""Layout of the master 'App Updates' workbook: which sheets the app reads, what each column means,
which columns ingestion may propose, and which location codes belong to which price-grid sheet.

Location codes and sheet routing can be overridden in owner_rules_<market>.json
("master_location_codes", "master_location_sheets") without a code change.
"""
import re
import cell_parse as cp, rules

GRID, COMMISSION, SEPARATED = "grid", "commission", "separated"

# exact (stripped) sheet title -> kind. Other sheets in the workbook are ignored and left untouched.
SHEETS = {"New Cairo": GRID, "Sokhna&new zayed": GRID, "New Capital": GRID,
          "Sheet 2": COMMISSION, "Separated update": SEPARATED}
GRID_SHEETS = [s for s, k in SHEETS.items() if k == GRID]

UNIT_FIELDS = ["studio", "apartment_1", "apartment_2", "apartment_3", "apartment_4", "villa", "town", "twin",
               "standalone", "office", "clinic", "shop", "pharmacy"]
UNIT_LABELS = {"studio": "Studio", "apartment_1": "Apartment 1", "apartment_2": "Apartment 2",
               "apartment_3": "Apartment 3", "apartment_4": "Apartment 4", "villa": "S Villa / Duplex",
               "town": "Town", "twin": "Twin", "standalone": "Standalone", "office": "Office", "clinic": "Clinic",
               "shop": "Shop", "pharmacy": "Pharmacy"}

# normalized header text -> field
HEADERS = {
    "developer": "developer", "developers": "developer", "project": "project", "location": "location",
    "readytomove": "ready_to_move", "studio": "studio", "apartemnt1": "apartment_1", "apartment1": "apartment_1",
    "apartment2": "apartment_2", "apartment3": "apartment_3", "apartment4": "apartment_4",
    "svilladublex": "villa", "dublexsvilla": "villa", "svilladuplex": "villa", "duplexsvilla": "villa",
    "town": "town", "twin": "twin", "standalone": "standalone", "office": "office", "clinic": "clinic",
    "shop": "shop", "pharmacy": "pharmacy", "paymentplan": "payment_plan", "date": "date",
    "voicenote": "voice_note", "commission": "commission", "salesname": "sales_name",
    "salescontact": "sales_contact", "number": "sales_contact", "feedback": "feedback", "id": "id_note",
}

# what ingestion may propose, per sheet kind (decisions 7, 9, 11, 12). Never: sales_*, voice_note, id_note.
PROPOSABLE = {
    GRID: ["location", "ready_to_move", *UNIT_FIELDS, "payment_plan"],
    COMMISSION: ["commission", "feedback"],
    SEPARATED: ["apartment_1", "apartment_2", "apartment_3", "apartment_4", "commission"],
}
NEVER = {"sales_name", "sales_contact", "voice_note", "id_note", "developer", "project", "date"}
ALL_PROPOSABLE = sorted({f for fs in PROPOSABLE.values() for f in fs} | {"feedback"})

LOCATION_CODES = rules.RULES.get("master_location_codes", {
    "e": "East / New Cairo", "g": "Galala", "n": "North Coast", "o": "October", "z": "New Zayed",
    "s": "Sokhna", "r": "Red Sea", "c": "New Capital"})
LOCATION_SHEETS = rules.RULES.get("master_location_sheets", {
    "New Cairo": ["e", "g", "n", "o"], "Sokhna&new zayed": ["z", "s", "r", "l"], "New Capital": ["c"]})


def sheet_kind(title):
    return SHEETS.get((title or "").strip())


def field_of(header):
    return HEADERS.get(re.sub(r"[^a-z0-9]", "", (header or "").lower()))


def location_code(raw):
    return cp.text(raw).strip().lower()


def sheet_for_location(code):
    """Grid sheet a location code belongs to, or None when unknown."""
    code = (code or "").strip().lower()
    return next((s for s, codes in LOCATION_SHEETS.items() if code in codes), None)


def known_location(code):
    return (code or "").strip().lower() in LOCATION_CODES


PARSERS = {"ready_to_move": (cp.parse_rtm, cp.format_rtm), "payment_plan": (cp.parse_payment_plan,
           cp.format_payment_plan), "commission": (cp.parse_commission, cp.format_commission),
           "date": (cp.parse_date, lambda p: p.get("raw", ""))}
for _f in UNIT_FIELDS:
    PARSERS[_f] = (cp.parse_unit_cell, cp.format_unit_cell)


def parse(field, raw):
    fn = PARSERS.get(field, (cp.parse_text, cp.format_text))[0]
    return fn(raw)


def fmt(field, parsed):
    fn = PARSERS.get(field, (cp.parse_text, cp.format_text))[1]
    return fn(parsed)


def same_value(field, a, b):
    """True when two parsed values mean the same thing (so no proposal is needed)."""
    keys = {"ready_to_move": ["row_status", "rtm_scope", "rtm_bedrooms", "rtm_area_sqm", "rtm_price_egp"],
            "payment_plan": ["down_payment_pct", "years", "cash"], "commission": ["pct", "net"]}
    keys.update({f: ["area_sqm", "price_egp", "status"] for f in UNIT_FIELDS})
    if field in keys and a.get("ok") and b.get("ok"):
        def norm(p, k):
            v = p.get(k)
            return cp.round_price(v) if k in ("price_egp", "rtm_price_egp") and v else v
        return all(norm(a, k) == norm(b, k) for k in keys[field])
    return (a.get("raw") or "").strip().lower() == (b.get("raw") or "").strip().lower()


def round_prices(field, parsed):
    """Apply the team's rounding to a suggested value's prices (unit cells and Ready To Move)."""
    if not parsed.get("ok"):
        return parsed
    for k in ("price_egp", "rtm_price_egp"):
        if parsed.get(k):
            parsed = dict(parsed, **{k: cp.round_price(parsed[k])})
    return parsed
