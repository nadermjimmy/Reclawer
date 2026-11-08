"""Parse and format the cell conventions used in the master 'App Updates' workbook.

Pure functions only (no DB, no LLM). Every parser keeps the original text and returns structured parts;
`ok` is False when the text can't be read without guessing, so the caller sends it to review.
"""
import datetime, re, unicodedata

# status words written inside unit / RTM cells -> canonical code
STATUS_WORDS = [
    (r"sold\s*out|^sold\b|^so\b", "sold_out"),
    (r"^de\b", "not_offered"),
    (r"all\s*project\s*rtm", "rtm_all"),
    (r"\brtm\b|ready\s*to\s*move", "rtm"),
    (r"serv", "serviced"),
    (r"h+old", "hold"),
    (r"hide", "hide"),
]
# canonical code -> how the team writes it in a cell
STATUS_TEXT = {"sold_out": "so", "not_offered": "de", "rtm": "rtm", "rtm_all": "all project rtm",
               "serviced": "service", "hold": "Hold", "hide": "Hide"}
ROW_STATUSES = {"hold", "hide"}

_NUM = r"\d[\d,]*(?:\.\d+)?"
_PP = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*[-–]?\s*(\d+(?:[.,]\d+)?)\s*(?:y|yr|yrs|year|years)?\b", re.I)


def text(v):
    """Cell value -> the text the team sees (5.0 -> '5', datetimes -> ISO date)."""
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def to_number(s):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def fmt_number(v):
    if v is None:
        return ""
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def normalize_project_key(name):
    """'La Vista ', 'LaVista' and 'lavista' -> 'lavista'. Arabic letters are kept."""
    s = unicodedata.normalize("NFKC", str(name or "")).lower()
    return "".join(ch for ch in s if ch.isalnum())


def status_of(s):
    """First recognised status word in s, or ''."""
    low = (s or "").strip().lower()
    for pat, code in STATUS_WORDS:
        if re.search(pat, low):
            return code
    return ""


# ---------- unit-type cells: '125/6,900,000' ----------
_UNIT = re.compile(rf"^\s*({_NUM})\s*/\s*({_NUM})\s*(.*)$")


def parse_unit_cell(raw):
    raw_t = text(raw).strip()
    out = {"raw": raw_t, "area_sqm": None, "price_egp": None, "status": "", "note": "", "ok": True}
    if not raw_t:
        return out
    m = _UNIT.match(raw_t)
    if m:
        out["area_sqm"], out["price_egp"] = to_number(m.group(1)), to_number(m.group(2))
        rest = m.group(3).strip()
        if rest:
            out["note"] = rest
            out["status"] = status_of(rest)
        if out["price_egp"] is not None and out["price_egp"] < 10000:
            out["ok"] = False
            out["note"] = (out["note"] + " price looks incomplete").strip()
        return out
    out["status"] = status_of(raw_t)
    # 'Hold', 'so', 'Service Ap.' are plain statuses; arrows, digits ('Sold out phase 1') or no status -> review
    plain = out["status"] and not re.search(r"[\d<>]", raw_t)
    if not plain:
        out["note"] = raw_t
        out["ok"] = False  # arrows, payment plans, floors etc. written in a unit column
    return out


def format_unit_cell(p):
    if p.get("area_sqm") is not None and p.get("price_egp") is not None:
        return f"{p['area_sqm']:g}/{fmt_number(p['price_egp'])}"
    if p.get("status"):
        return STATUS_TEXT[p["status"]]
    return p.get("raw", "")


# ---------- payment plan: '10% - 8Y' ----------
def parse_payment_plan(raw):
    raw_t = text(raw).strip()
    out = {"raw": raw_t, "down_payment_pct": None, "years": None, "cash": False, "ok": True}
    if not raw_t:
        return out
    if re.fullmatch(r"cash", raw_t, re.I):
        out.update(cash=True, down_payment_pct=100.0, years=0.0)
        return out
    m = _PP.search(raw_t)
    if not m:
        out["ok"] = False
        return out
    out["down_payment_pct"] = float(m.group(1).replace(",", "."))
    out["years"] = float(m.group(2).replace(",", "."))
    leftover = (raw_t[:m.start()] + raw_t[m.end():]).strip(" -<>")
    if leftover:
        out["ok"] = False  # e.g. '10% - 7Y Villa': readable, but carries extra meaning -> review
        out["note"] = leftover
    return out


def format_payment_plan(p):
    if p.get("cash"):
        return "Cash"
    if p.get("down_payment_pct") is None:
        return p.get("raw", "")
    return f"{p['down_payment_pct']:g}% - {p['years']:g}Y"


# ---------- commission: 4 / '4 net' / '1,5 net' ----------
def parse_commission(raw):
    raw_t = text(raw).strip()
    out = {"raw": raw_t, "pct": None, "net": False, "ok": True}
    if not raw_t:
        return out
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*%?\s*(net)?", raw_t, re.I)
    if not m:
        out["ok"] = False
        return out
    out["pct"] = float(m.group(1).replace(",", "."))
    out["net"] = bool(m.group(2))
    return out


def format_commission(p):
    if p.get("pct") is None:
        return p.get("raw", "")
    return f"{p['pct']:g}" + (" net" if p.get("net") else "")


# ---------- Ready To Move column ----------
_BEDS = re.compile(rf"^\s*(\d+)\s*(?:b|bed|beds|bedroom|br)\b\s*/?\s*({_NUM})\s*$", re.I)
_RTM_PRICE = re.compile(rf"^\s*rtm\s*/?\s*({_NUM})\s*$", re.I)
_TAGS = [("serviced", r"serv"), ("admin", r"admin"), ("clinic", r"clinic")]


def parse_rtm(raw):
    raw_t = text(raw).strip()
    out = {"raw": raw_t, "row_status": "", "rtm_scope": "none", "rtm_bedrooms": None, "rtm_area_sqm": None,
           "rtm_price_egp": None, "product_tags": [], "notes": "", "ok": True}
    if not raw_t:
        return out
    low = raw_t.lower()
    if re.fullmatch(_NUM, raw_t):
        out.update(rtm_scope="some", rtm_price_egp=to_number(raw_t))
    elif (m := _UNIT.match(raw_t)) and not m.group(3).strip():
        out.update(rtm_scope="some", rtm_area_sqm=to_number(m.group(1)), rtm_price_egp=to_number(m.group(2)))
        if out["rtm_price_egp"] < 10000:
            out["ok"] = False
    elif m := _BEDS.match(raw_t):
        out.update(rtm_scope="some", rtm_bedrooms=int(m.group(1)), rtm_price_egp=to_number(m.group(2)))
    elif m := _RTM_PRICE.match(raw_t):
        out.update(rtm_scope="some", rtm_price_egp=to_number(m.group(1)))
    elif re.search(r"all\s*project\s*rtm", low):
        out["rtm_scope"] = "all"
    elif re.fullmatch(r"rtm", low):
        out["rtm_scope"] = "some"
    elif re.fullmatch(r"h+old|hide", low):
        out["row_status"] = "hold" if "old" in low else "hide"
    else:
        out["product_tags"] = [t for t, pat in _TAGS if re.search(pat, low)]
        if not out["product_tags"] or re.search(r"[<>]", raw_t):
            out["notes"] = raw_t
            out["ok"] = bool(out["product_tags"]) and not re.search(r"[<>]", raw_t)
    return out


def format_rtm(p):
    if p.get("row_status"):
        return STATUS_TEXT[p["row_status"]]
    price = p.get("rtm_price_egp")
    if p.get("rtm_scope") == "all":
        return "all project rtm"
    if p.get("rtm_bedrooms") and price is not None:
        return f"{p['rtm_bedrooms']} bed/{fmt_number(price)}"
    if p.get("rtm_area_sqm") is not None and price is not None:
        return f"{p['rtm_area_sqm']:g}/{fmt_number(price)}"
    if price is not None:
        return fmt_number(price)
    if p.get("rtm_scope") == "some":
        return "rtm"
    if p.get("product_tags") and not p.get("notes"):
        return " / ".join("service" if t == "serviced" else t for t in p["product_tags"])
    return p.get("raw", "")


# ---------- free text (feedback) and location ----------
def parse_text(raw):
    t = text(raw).strip()
    return {"raw": t, "ok": True}


def format_text(p):
    return p.get("raw", "")


def parse_date(raw):
    """Date column: a date, or a status word (Hide/Hold) written instead of a date."""
    if isinstance(raw, (datetime.datetime, datetime.date)):
        return {"raw": text(raw), "date": text(raw), "row_status": "", "ok": True}
    t = text(raw).strip()
    st = status_of(t)
    return {"raw": t, "date": None, "row_status": st if st in ROW_STATUSES else "", "ok": not t or bool(st)}


# ---------- team rounding rule (owner decision) ----------
def round_price(v):
    """Prices the team writes: 1,000,000 EGP and above round UP to the next 50,000; below that UP to the next 1,000."""
    if v is None:
        return None
    import math
    step = 50000 if v >= 1_000_000 else 1000
    return float(math.ceil(v / step - 1e-9) * step)
