"""Rebuild the inventory (projects, phases, units, snapshots, review queue) from sources + mapping decisions.

Deterministic: the same sources and decisions always give the same inventory, so it can be re-run any time.
Source values are copied verbatim; anything calculated is stored in clearly derived fields."""
import json, re
import db, rows, rules

SQM_TO_SQFT = 10.7639104167
REQUIRED_FACTS = ["location", "service_charge", "payment_plan", "completion_rate", "amenities", "handover"]
UNIT_STATUS = [("sold", r"\bsold\b"), ("reserved", r"reserv|booked|\bhold\b|block"),
               ("under_process", r"process|pending"), ("available", r"avail|\bopen\b|ready|vacant|for sale"),
               ("announced", r"announc|coming soon|launch")]
CUR = r"(aed|egp|l\.?e\.?|د\.إ|ج\.م)"
NUMBER = re.compile(rf"^\s*{CUR}?\s*([0-9][0-9,]*(\.[0-9]+)?)\s*{CUR}?\s*$", re.I)


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def unit_status(raw):
    if not raw.strip():
        return "unknown"
    low = raw.lower()
    if re.search(r"not\s+avail|unavail", low):
        return "sold" if "sold" in low else "unknown"
    return next((s for s, pat in UNIT_STATUS if re.search(pat, low)), "unknown")


def parse_number(raw):
    """A plain number (optionally with commas / a currency). Anything else (1.2M, ranges, text) is not parsed."""
    m = NUMBER.match(raw or "")
    return float(m.group(2).replace(",", "")) if m else None


def price_per_area(price, currency, area):
    """Derived price per sq m (Egypt) or per sq ft (UAE). Returns (value, inputs_json)."""
    value, unit = area["value"], area["unit"]
    if rules.METRIC == "sq_ft":
        size, conv = (value * SQM_TO_SQFT, "area converted: sq m x 10.7639104167") if unit == "sq_m" else (value, "")
    else:
        size, conv = (value / SQM_TO_SQFT, "area converted: sq ft / 10.7639104167") if unit == "sq_ft" else (value, "")
    formula = f"listed_price / area_{rules.METRIC}" + (f" ({conv})" if conv else "")
    return round(price / size, 2), json.dumps(
        {"formula": formula, "listed_price": price, "currency": currency, "area_column": area["label"],
         "area_raw": area["raw"], "area_unit": unit}, ensure_ascii=False)


def effective(m):
    """(state, project, phase, developer, reason) for a context's mapping decision."""
    if m is None:
        return "queued", None, None, None, "not mapped yet"
    if m["review_state"] == "rejected":
        return "queued", None, None, None, "proposal rejected by reviewer"
    approved = m["review_state"] == "approved"
    if m["decision"] == "exclude_scope" and (approved or m["is_uae"] == "no"):
        return "excluded", None, None, None, f"outside {rules.MARKET_NAME} scope"
    if m["decision"] != "project":
        return "queued", None, None, None, m["rationale"] or "parent project not established"
    if not approved and (m["confidence"] != "high" or m["is_uae"] != "yes"):
        return "queued", None, None, None, f"low-confidence proposal ({m['confidence']}, in {rules.MARKET_SHORT}: {m['is_uae']})"
    bad = rules.why_not_a_project(m["project_name"])
    if bad:
        return "queued", None, None, None, bad
    return "mapped", m["project_name"], m["phase_label"] or "", m["developer"] or "", m["rationale"]


def canonical_names(names, approved):
    """Collapse names that belong to one parent (Brabus, Verdana, ...) into one canonical name."""
    out = {}
    groups = {}
    for n in names:
        g = rules.parent_group(n)
        if g:
            groups.setdefault(g, []).append(n)
        else:
            out[n] = (n, "")
    for g, members in groups.items():
        chosen = [n for n in members if n in approved] or members
        canon = min(chosen, key=lambda n: (len(n), n))
        for n in members:
            rest = n.replace(canon, "").strip(" -–:/") if canon.lower() in n.lower() else n
            out[n] = (canon, rest if n != canon else "")
    return out


def rebuild(log):
    with db.connect() as c:
        for t in ("developers", "projects", "project_aliases", "project_phases", "units", "unit_snapshots",
                  "review_issues", "fact_links"):
            c.execute(f"DELETE FROM {t}")
        issues = []

        def issue(kind, severity, ref_kind, ref_id, reason, **detail):
            issues.append((kind, severity, ref_kind, str(ref_id), reason, json.dumps(detail, ensure_ascii=False)))

        maps = {r["context_key"]: r for r in c.execute("SELECT * FROM label_mappings")}
        files = rows.scoped_file_ids(c)
        data = list(rows.data_rows(c, files))
        ctx_state = {}
        for r, vals in data:
            k = rows.context_key(r, vals)
            if k not in ctx_state:
                ctx_state[k] = effective(maps.get(k))
        approved = {m["project_name"] for m in maps.values() if m["review_state"] == "approved"}
        canon = canonical_names({s[1] for s in ctx_state.values() if s[0] == "mapped"}, approved)

        projects, phases = {}, {}
        for k, (state, name, phase, dev, why) in ctx_state.items():
            ctx = c.execute("SELECT * FROM label_contexts WHERE key=?", (k,)).fetchone()
            if state != "mapped":
                kind = "scope_excluded" if state == "excluded" else "unmapped_rows"
                issue(kind, "info" if state == "excluded" else "high", "context", k, why,
                      file_id=json.loads(k)[0], sheet=json.loads(k)[1], rows=ctx["row_count"] if ctx else None)
                continue
            cname, phase_from_name = canon[name]
            phase = phase or phase_from_name
            if cname not in projects:
                dev_id = None
                if dev:
                    c.execute("INSERT OR IGNORE INTO developers(name) VALUES(?)", (dev,))
                    dev_id = c.execute("SELECT id FROM developers WHERE name=?", (dev,)).fetchone()[0]
                slug, n = slugify(cname), 2
                while c.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
                    slug, n = f"{slugify(cname)}-{n}", n + 1
                projects[cname] = c.execute("INSERT INTO projects(slug,name,developer_id) VALUES(?,?,?)",
                                            (slug, cname, dev_id)).lastrowid
            pid = projects[cname]
            m = maps[k]
            for raw, typ in ((name, "mapped_name"), (ctx["raw_project"] if ctx else "", "project_cell"),
                             (ctx["section_label"] if ctx else "", "section_title")):
                if raw and not c.execute("SELECT 1 FROM project_aliases WHERE project_id=? AND raw_label=?",
                                         (pid, raw)).fetchone():
                    c.execute("INSERT INTO project_aliases(project_id,raw_label,alias_type,context_key,rationale,"
                              "confidence,review_state) VALUES(?,?,?,?,?,?,?)",
                              (pid, raw, typ, k, m["rationale"], m["confidence"], m["review_state"]))
            if phase and (pid, phase) not in phases:
                phases[(pid, phase)] = c.execute(
                    "INSERT INTO project_phases(project_id,raw_label,display_label) VALUES(?,?,?)",
                    (pid, phase, phase)).lastrowid
                limit = rules.phase_limit(cname)
                num = rules.phase_number(phase)
                if limit and num and num > limit:
                    issue("phase_conflict", "high", "project", pid,
                          f"{cname}: source label '{phase}' is above the owner's {limit} phases; units kept as-is",
                          phase=phase)
            ctx_state[k] = (state, cname, phase, dev, why)

        # units and dated observations
        file_ts = {r[0]: r[1] for r in c.execute(
            "SELECT o.file_id, MIN(m.ts) FROM file_occurrences o JOIN source_messages m ON m.wa_id=o.wa_id "
            "GROUP BY o.file_id")}
        file_names = {r[0]: r[1] or "" for r in c.execute("SELECT id, file_name FROM source_files")}
        seen = {}
        no_unit_area, bad_price = {}, {}
        for r, vals in data:
            k = rows.context_key(r, vals)
            state, cname, phase, _, _ = ctx_state[k]
            code = rows.first(vals, "unit_code")
            unit_id = None
            if state == "mapped" and code:
                pid = projects[cname]
                c.execute("INSERT OR IGNORE INTO units(project_id,phase_key,phase_id,unit_code) VALUES(?,?,?,?)",
                          (pid, phase, phases.get((pid, phase)), code))
                unit_id = c.execute("SELECT id FROM units WHERE project_id=? AND phase_key=? AND unit_code=?",
                                    (pid, phase, code)).fetchone()[0]
                ident = (unit_id, r["file_id"])
                if ident in seen:
                    issue("duplicate_unit_row", "medium", "row", r["id"],
                          f"unit {code} appears more than once in the same file (rows {seen[ident]} and "
                          f"{r['row_number']}); both kept", unit=code)
                seen[ident] = r["row_number"]
            elif state == "mapped":
                state = "queued"
                issue("missing_unit_code", "medium", "row", r["id"], "row has price/area but no unit code")

            price = vals.get("price", [("", "", "", "", "")])[0]
            price_raw = price[2]
            price_value = parse_number(price_raw)
            ctx_text = " ".join([price[1], file_names.get(r["file_id"], ""), r["sheet"], price_raw])
            currency = price[4] or (rules.CURRENCY if rules.CURRENCY_RE.search(ctx_text) else "")
            if price_raw and price_value is None:
                bad_price.setdefault((r["file_id"], r["sheet"]), []).append(r["row_number"])
            areas = [{"label": h, "raw": v, "value": parse_number(v), "unit": u}
                     for (_, h, v, u, _) in vals.get("area", []) if v]
            ppsf, inputs = None, None
            primary = next((a for a in areas if a["unit"] and a["value"] and
                            re.search(r"total|saleable|bua|built|^area|size|suite", a["label"], re.I)), None) \
                or next((a for a in areas if a["unit"] and a["value"]), None)
            if areas and not any(a["unit"] for a in areas):
                no_unit_area.setdefault((r["file_id"], r["sheet"]), 0)
                no_unit_area[(r["file_id"], r["sheet"])] += 1
            if price_value and currency and primary:
                ppsf, inputs = price_per_area(price_value, currency, primary)
            status_raw = rows.first(vals, "status")
            notes = " | ".join(x[2] for x in vals.get("notes", []) if x[2])
            restriction = " | ".join(t for t in (r["section_label"] or "", rows.first(vals, "project"), notes)
                                     if t and re.search(r"flip|change\s+the\s+unit|no\s+resale", t, re.I))
            extra = {x[1]: x[2] for role in ("extra", "payment_plan", "handover", "service_charge",
                                             "source_price_per_area", "phase", "project")
                     for x in vals.get(role, []) if x[2]}
            c.execute(
                "INSERT INTO unit_snapshots(source_row_id,unit_id,context_key,snapshot_ts,unit_code_raw,"
                "unit_type_raw,floor_raw,view_raw,price_raw,price_value,currency,areas_json,status_raw,"
                "unit_status,restriction_raw,notes_raw,price_per_sqft,ppsf_inputs,mapping_state,extra_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], unit_id, k, file_ts.get(r["file_id"]), code, rows.first(vals, "unit_type"),
                 rows.first(vals, "floor"), rows.first(vals, "view"), price_raw, price_value, currency,
                 json.dumps(areas, ensure_ascii=False), status_raw, unit_status(status_raw), restriction or None,
                 notes or None, ppsf, inputs, state, json.dumps(extra, ensure_ascii=False)))

        for (fid, sheet), n in no_unit_area.items():
            issue("unknown_area_unit", "medium", "sheet", f"{fid}:{sheet}",
                  f"{n} rows have areas but the sheet doesn't say sq ft or sq m; price per {rules.METRIC_LABEL} not "
                  "calculated (set the unit on the table's review page if you know it)",
                  file=file_names.get(fid), sheet=sheet)
        for (fid, sheet), rs in bad_price.items():
            issue("price_not_plain_number", "low", "sheet", f"{fid}:{sheet}",
                  f"{len(rs)} listed prices aren't plain numbers (kept exactly as written; not used for "
                  f"calculations)", file=file_names.get(fid), sheet=sheet, rows=rs[:20])

        link_facts(c, issue)
        for f in c.execute("SELECT * FROM source_files WHERE parse_status IN ('failed','unsupported')"):
            issue("file_not_parsed", "high", "file", f["id"], f"{f['file_name']}: {f['parse_error']}")
        n_failed = c.execute("SELECT COUNT(*) FROM media_fetch WHERE status='failed'").fetchone()[0]
        if n_failed:
            issue("attachments_unavailable", "medium", "media", "all",
                  f"{n_failed} attachments could not be downloaded from WhatsApp (see Sources)")
        c.executemany("INSERT INTO review_issues(issue_type,severity,ref_kind,ref_id,reason,detail_json) "
                      "VALUES(?,?,?,?,?,?)", issues)
        n_units = c.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    log(f"Inventory rebuilt: {len(projects)} projects, {n_units} units, {len(data)} unit rows, "
        f"{len(issues)} review items")


def link_facts(c, issue):
    """Attach each fact to a project via the alias table; flag conflicts and missing required facts."""
    aliases = {}
    for r in c.execute("SELECT a.raw_label, a.project_id FROM project_aliases a"):
        aliases.setdefault(r[0].strip().lower(), set()).add(r[1])
    names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects")}
    for pid, n in names.items():
        aliases.setdefault(n.lower(), set()).add(pid)
    for f in c.execute("SELECT id, raw_project_label FROM fact_assertions WHERE validation_status<>'rejected'"
                       ).fetchall():
        label = (f["raw_project_label"] or "").strip().lower()
        hits = aliases.get(label) or {pid for pid, n in names.items() if label and
                                      (n.lower() in label or label in n.lower())}
        if len(hits) == 1:
            c.execute("INSERT INTO fact_links VALUES(?,?)", (f["id"], next(iter(hits))))
        elif label:
            issue("fact_project_unmapped", "low", "fact", f["id"],
                  f"fact mentions '{f['raw_project_label']}', which doesn't match exactly one project")
    for pid, name in names.items():
        facts = c.execute("SELECT a.* FROM fact_assertions a JOIN fact_links l ON l.fact_id=a.id "
                          "WHERE l.project_id=? AND a.validation_status<>'rejected'", (pid,)).fetchall()
        have = {f["fact_type"] for f in facts}
        missing = [t for t in REQUIRED_FACTS if t not in have]
        if missing:
            issue("missing_facts", "low", "project", pid, f"{name}: not supplied in the group - "
                  + ", ".join(t.replace("_", " ") for t in missing))
        for t, phase in {(f["fact_type"], f["phase_label"] or "") for f in facts}:
            if t in ("other", "unit_status"):
                continue
            vals = {(f["parsed_value"] or f["raw_value"]).strip().lower() for f in facts
                    if f["fact_type"] == t and (f["phase_label"] or "") == phase}
            if len(vals) > 1:
                issue("fact_conflict", "high" if t == "handover" else "medium", "project", pid,
                      f"{name}{' / ' + phase if phase else ''}: {len(vals)} different {t.replace('_', ' ')} "
                      "statements", fact_type=t, phase=phase)
        sold_phases = {(f["phase_label"] or "").lower() for f in facts
                       if f["fact_type"] == "phase_status" and "sold" in (f["parsed_value"] + f["raw_value"]).lower()}
        for ph in sold_phases:
            n = c.execute("SELECT COUNT(*) FROM unit_snapshots s JOIN units u ON u.id=s.unit_id WHERE u.project_id=? "
                          "AND lower(u.phase_key)=? AND s.unit_status='available'", (pid, ph)).fetchone()[0]
            if n:
                issue("status_conflict", "medium", "project", pid,
                      f"{name}: a message says phase '{ph or 'unspecified'}' is sold out, but {n} unit rows in it "
                      "are listed as available; both kept")


def reconcile(c):
    """Counts that must add up: every in-scope unit row is mapped, queued or excluded."""
    files = rows.scoped_file_ids(c)
    total_rows = sum(1 for _ in rows.data_rows(c, files))
    by_state = dict(c.execute("SELECT mapping_state, COUNT(*) FROM unit_snapshots GROUP BY mapping_state").fetchall())
    stats = c.execute("SELECT MIN(price_value), MAX(price_value), SUM(price_value<1000000), "
                      "SUM(price_value IS NULL AND price_raw<>'') FROM unit_snapshots").fetchone()
    return {
        "in_scope_files": len(files),
        "files_by_status": dict(c.execute("SELECT parse_status, COUNT(*) FROM source_files "
                                          "WHERE kind IN ('workbook','csv','pdf') GROUP BY parse_status").fetchall()),
        "attachments_unavailable": c.execute("SELECT COUNT(*) FROM media_fetch WHERE status='failed'").fetchone()[0],
        "unit_rows_in_sources": total_rows,
        "mapped": by_state.get("mapped", 0), "queued": by_state.get("queued", 0),
        "excluded_by_scope": by_state.get("excluded", 0),
        "adds_up": total_rows == sum(by_state.values()),
        "distinct_units": c.execute("SELECT COUNT(*) FROM units").fetchone()[0],
        "projects": c.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "min_price": stats[0], "max_price": stats[1], "rows_under_1m": stats[2] or 0,
        "prices_not_plain_numbers": stats[3] or 0,
    }
