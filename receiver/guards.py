"""Price guards (Phase 6): is a suggested price plausible for this project?

History = EGP per sq m of every unit-type cell of the project (all its sheet rows): the master cells as uploaded plus
approved changes. Residential and commercial units (office, clinic, shop, pharmacy) are kept apart. With fewer than
MIN_POINTS points the same developer's rows at the same location code are used; below that, the fixed
BRAIN_PRICE_BAND check against the current price (today's rule).

A value passes when its robust z-score (median / MAD) is within GUARD_Z. Used by brain.safe_to_approve under
GUARDS_V2=1 only; the owner's exclusions (commission, feedback, location, Hold/Hide, new rows) stay in brain.py.
"""
import json, os, statistics
import cell_parse as cp, master_schema as ms

MIN_POINTS = 5
GUARD_Z = float(os.getenv("GUARD_Z", "3.5"))
PRICE_BAND = float(os.getenv("BRAIN_PRICE_BAND", "0.20"))
COMMERCIAL = {"office", "clinic", "shop", "pharmacy"}


def _family(field):
    return "commercial" if field in COMMERCIAL else "residential"


def ppsm(field, parsed):
    """EGP per sq m of a parsed unit / Ready To Move value, or None."""
    if field == "ready_to_move":
        a, p = parsed.get("rtm_area_sqm"), parsed.get("rtm_price_egp")
    else:
        a, p = parsed.get("area_sqm"), parsed.get("price_egp")
    return p / a if a and p and a > 0 else None


def _points(c, row_ids, family):
    if not row_ids:
        return []
    marks = ",".join("?" * len(row_ids))
    fields = [f for f in ms.UNIT_FIELDS if _family(f) == family]
    out = []
    for r in c.execute(f"SELECT field, parsed_json FROM master_cells WHERE row_id IN ({marks}) "
                       f"AND field IN ({','.join('?' * len(fields))})", [*row_ids, *fields]):
        v = ppsm(r["field"], json.loads(r["parsed_json"] or "{}"))
        if v:
            out.append(v)
    for r in c.execute(f"SELECT field, new_parsed_json FROM proposals WHERE state='approved' AND row_id IN ({marks}) "
                       f"AND field IN ({','.join('?' * len(fields))})", [*row_ids, *fields]):
        v = ppsm(r["field"], json.loads(r["new_parsed_json"] or "{}"))
        if v:
            out.append(v)
    return out


def history(c, row, field):
    """(points, scope) for a row: the project's own, else developer + location, else ([], 'none')."""
    fam = _family(field)
    ids = [r[0] for r in c.execute("SELECT id FROM master_rows WHERE project_key=?", (row["project_key"],))]
    pts = _points(c, ids, fam)
    if len(pts) >= MIN_POINTS:
        return pts, "project"
    dev = cp.normalize_project_key(row["developer"])
    if dev and row["location_code"]:
        ids = [r["id"] for r in c.execute("SELECT id, developer FROM master_rows WHERE location_code=?",
                                          (row["location_code"],)) if cp.normalize_project_key(r["developer"]) == dev]
        pts = _points(c, ids, fam)
        if len(pts) >= MIN_POINTS:
            return pts, "developer+location"
    return pts, "none"


def robust_z(x, pts):
    med = statistics.median(pts)
    mad = statistics.median(abs(p - med) for p in pts)
    mad = max(mad, 0.05 * med)  # identical history (all the same price) shouldn't make every change an outlier
    return 0.6745 * (x - med) / mad, med


def check(c, row_id, field, new):
    """(ok, why_not) for a suggested value on a row."""
    row = c.execute("SELECT * FROM master_rows WHERE id=?", (row_id,)).fetchone()
    if not row:
        return False, "row not found"
    x = ppsm(field, new)
    if x is None:  # a status, or a price without an area: today's band against the current price
        import master
        _, old = master.current(c, row_id, field)
        k = "rtm_price_egp" if field == "ready_to_move" else "price_egp"
        a, b = old.get(k), new.get(k)
        if a and b and abs(b - a) / a > PRICE_BAND:
            return False, f"price moves {((b - a) / a) * 100:+.0f}%, more than ±{PRICE_BAND * 100:.0f}%"
        return True, ""
    pts, scope = history(c, row, field)
    if scope == "none":
        import master
        _, old = master.current(c, row_id, field)
        y = ppsm(field, old)
        if y and abs(x - y) / y > PRICE_BAND:
            return False, (f"EGP/sq m moves {((x - y) / y) * 100:+.0f}%, more than ±{PRICE_BAND * 100:.0f}% "
                           f"(only {len(pts)} price point(s) for this project)")
        return True, ""
    z, med = robust_z(x, pts)
    if abs(z) > GUARD_Z:
        return False, (f"{x:,.0f} EGP/sq m is unusual for this {'project' if scope == 'project' else 'developer here'} "
                       f"(median {med:,.0f}, robust z {z:+.1f})")
    return True, ""
