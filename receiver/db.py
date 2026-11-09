"""SQLite schema for the bridge. Source tables are append-only; inventory tables are rebuilt by build.py."""
import os, sqlite3

DB = os.getenv("DB_PATH", "/data/messages.db")
DATA_DIR = os.path.dirname(DB) or "."
MEDIA_DIR = os.path.join(DATA_DIR, "media")
MASTER_DIR = os.path.join(DATA_DIR, "master")

SCHEMA = """
-- digest tables (kept as before)
CREATE TABLE IF NOT EXISTS messages(
    id TEXT PRIMARY KEY, group_jid TEXT, group_name TEXT, sender TEXT,
    sender_name TEXT, ts INTEGER, type TEXT, text TEXT, processed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS groups(jid TEXT PRIMARY KEY, name TEXT);

-- which chats feed the inventory
CREATE TABLE IF NOT EXISTS group_scope(jid TEXT PRIMARY KEY, in_scope INTEGER NOT NULL DEFAULT 0, note TEXT);

-- immutable sources
CREATE TABLE IF NOT EXISTS source_messages(
    wa_id TEXT PRIMARY KEY, group_jid TEXT NOT NULL, sender TEXT, sender_name TEXT, ts INTEGER,
    message_type TEXT, text TEXT, file_name TEXT, mimetype TEXT, has_media INTEGER DEFAULT 0,
    raw_json TEXT, origin TEXT, ingested_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_sm_group_ts ON source_messages(group_jid, ts);

CREATE TABLE IF NOT EXISTS source_files(
    id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, file_name TEXT, mimetype TEXT, size INTEGER,
    path TEXT, kind TEXT, parse_status TEXT DEFAULT 'pending', parse_error TEXT, parser_version TEXT,
    ingested_at INTEGER);
CREATE TABLE IF NOT EXISTS file_occurrences(
    wa_id TEXT PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES source_files(id), file_name TEXT);
CREATE TABLE IF NOT EXISTS media_fetch(
    wa_id TEXT PRIMARY KEY, status TEXT, error TEXT, attempted_at INTEGER);

CREATE TABLE IF NOT EXISTS source_rows(
    id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES source_files(id), sheet TEXT NOT NULL,
    row_number INTEGER NOT NULL, kind TEXT NOT NULL, section_label TEXT, hidden INTEGER DEFAULT 0,
    is_data INTEGER DEFAULT 0, raw_json TEXT NOT NULL,
    UNIQUE(file_id, sheet, row_number, kind));
CREATE TABLE IF NOT EXISTS sheet_layouts(
    file_id INTEGER NOT NULL, sheet TEXT NOT NULL, header_row INTEGER, columns_json TEXT,
    source TEXT DEFAULT 'auto', PRIMARY KEY(file_id, sheet));

-- interpretation decisions (kept across rebuilds)
CREATE TABLE IF NOT EXISTS label_contexts(
    key TEXT PRIMARY KEY, file_id INTEGER, sheet TEXT, section_label TEXT, raw_project TEXT,
    raw_phase TEXT, evidence_json TEXT, row_count INTEGER);
CREATE TABLE IF NOT EXISTS label_mappings(
    context_key TEXT PRIMARY KEY, decision TEXT, developer TEXT, project_name TEXT, phase_label TEXT,
    confidence TEXT, rationale TEXT, is_uae TEXT, source TEXT, review_state TEXT DEFAULT 'proposed',
    updated_at INTEGER);
CREATE TABLE IF NOT EXISTS fact_assertions(
    id INTEGER PRIMARY KEY, raw_project_label TEXT, phase_label TEXT, fact_type TEXT, raw_value TEXT,
    parsed_value TEXT, excerpt TEXT, source_kind TEXT, source_ref TEXT, observed_ts INTEGER,
    validation_status TEXT, updates_previous INTEGER DEFAULT 0, reviewer_note TEXT,
    UNIQUE(source_kind, source_ref, fact_type, raw_project_label, phase_label, raw_value));
CREATE TABLE IF NOT EXISTS processed_batches(batch_key TEXT PRIMARY KEY, processed_at INTEGER);
CREATE TABLE IF NOT EXISTS media_assignments(
    file_id INTEGER PRIMARY KEY, classification TEXT, project_name TEXT, phase_label TEXT,
    unit_code TEXT, rationale TEXT, confidence TEXT, review_state TEXT DEFAULT 'proposed',
    excluded_reason TEXT, updated_at INTEGER);

-- derived inventory (rebuilt from the tables above)
CREATE TABLE IF NOT EXISTS developers(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS projects(
    id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT UNIQUE, developer_id INTEGER);
CREATE TABLE IF NOT EXISTS project_aliases(
    id INTEGER PRIMARY KEY, project_id INTEGER, raw_label TEXT, alias_type TEXT, context_key TEXT,
    rationale TEXT, confidence TEXT, review_state TEXT);
CREATE TABLE IF NOT EXISTS project_phases(
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, raw_label TEXT NOT NULL, display_label TEXT,
    UNIQUE(project_id, raw_label));
CREATE TABLE IF NOT EXISTS units(
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, phase_key TEXT NOT NULL DEFAULT '',
    phase_id INTEGER, unit_code TEXT NOT NULL, UNIQUE(project_id, phase_key, unit_code));
CREATE TABLE IF NOT EXISTS unit_snapshots(
    id INTEGER PRIMARY KEY, source_row_id INTEGER UNIQUE NOT NULL, unit_id INTEGER, context_key TEXT,
    snapshot_ts INTEGER, unit_code_raw TEXT, unit_type_raw TEXT, floor_raw TEXT, view_raw TEXT,
    price_raw TEXT, price_value REAL, currency TEXT, areas_json TEXT, status_raw TEXT,
    unit_status TEXT, restriction_raw TEXT, notes_raw TEXT, price_per_sqft REAL, ppsf_inputs TEXT,
    mapping_state TEXT, extra_json TEXT);
CREATE INDEX IF NOT EXISTS ix_us_unit ON unit_snapshots(unit_id);
CREATE TABLE IF NOT EXISTS fact_links(fact_id INTEGER PRIMARY KEY, project_id INTEGER);
CREATE TABLE IF NOT EXISTS review_issues(
    id INTEGER PRIMARY KEY, issue_type TEXT, severity TEXT, ref_kind TEXT, ref_id TEXT,
    reason TEXT, detail_json TEXT);

-- master 'App Updates' workbook (uploaded by the owner) and the cell-level updates proposed from chats.
-- Not touched by build.rebuild.
CREATE TABLE IF NOT EXISTS master_imports(
    id INTEGER PRIMARY KEY, file_name TEXT, sha256 TEXT, path TEXT, imported_at INTEGER, summary_json TEXT);
CREATE TABLE IF NOT EXISTS master_sheets(
    import_id INTEGER NOT NULL, sheet TEXT NOT NULL, title TEXT NOT NULL, kind TEXT, columns_json TEXT,
    PRIMARY KEY(import_id, sheet));
CREATE TABLE IF NOT EXISTS master_rows(
    id INTEGER PRIMARY KEY, import_id INTEGER, sheet TEXT NOT NULL, kind TEXT, row_number INTEGER,
    developer TEXT, project TEXT, project_key TEXT, location_code TEXT, state TEXT DEFAULT 'active',
    target_sheet TEXT, note TEXT, created_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_mr_key ON master_rows(project_key);
CREATE TABLE IF NOT EXISTS master_cells(
    row_id INTEGER NOT NULL, field TEXT NOT NULL, raw_text TEXT, parsed_json TEXT, PRIMARY KEY(row_id, field));
CREATE TABLE IF NOT EXISTS master_aliases(alias_key TEXT PRIMARY KEY, project_key TEXT, label TEXT,
    confirmed_at INTEGER);
CREATE TABLE IF NOT EXISTS master_issues(
    id INTEGER PRIMARY KEY, import_id INTEGER, row_id INTEGER, issue_type TEXT, reason TEXT,
    state TEXT DEFAULT 'open', decided_at INTEGER);
CREATE TABLE IF NOT EXISTS proposals(
    id INTEGER PRIMARY KEY, row_id INTEGER, sheet TEXT, developer TEXT, project TEXT, project_key TEXT,
    field TEXT NOT NULL, old_raw TEXT, new_raw TEXT, new_parsed_json TEXT, quote TEXT, source_kind TEXT,
    source_ref TEXT, source_ts INTEGER, match_state TEXT, matched_label TEXT, location_code TEXT,
    state TEXT DEFAULT 'proposed', warning TEXT, created_at INTEGER, decided_at INTEGER,
    UNIQUE(source_kind, source_ref, sheet, project_key, field, new_raw));
CREATE INDEX IF NOT EXISTS ix_prop_row ON proposals(row_id, field, state);

-- last single-chat sync per chat (Chats > Sync now)
CREATE TABLE IF NOT EXISTS chat_syncs(jid TEXT PRIMARY KEY, job_id INTEGER, synced_at INTEGER, result_json TEXT);

-- reading order run: text first, then spreadsheets, PDFs, images (each fills only what earlier sources didn't)
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ingest_runs(
    id INTEGER PRIMARY KEY, job_id INTEGER, stage INTEGER DEFAULT 0, batch_id TEXT, status TEXT,
    started_at INTEGER, updated_at INTEGER, totals_json TEXT);
CREATE TABLE IF NOT EXISTS ingest_items(
    custom_id TEXT PRIMARY KEY, run_id INTEGER, batch_id TEXT, stage TEXT, label TEXT, sources_json TEXT,
    keys_json TEXT, state TEXT DEFAULT 'pending');
CREATE INDEX IF NOT EXISTS ix_ii_run ON ingest_items(run_id, state);

-- brain: second look at open suggestions (re-targets false new rows, approves safe updates, rejects noise)
CREATE TABLE IF NOT EXISTS brain_reviews(
    proposal_id INTEGER PRIMARY KEY, verdict TEXT, row_id INTEGER, confidence TEXT, reason TEXT, action TEXT,
    model TEXT, created_at INTEGER);

-- developer profile per chat (profiles.py): who the chat belongs to and how that developer shares data
CREATE TABLE IF NOT EXISTS chat_profiles(
    jid TEXT PRIMARY KEY, developer TEXT, shares TEXT, on_request_json TEXT, notes TEXT, source TEXT,
    updated_at INTEGER);

-- ops-team assignment of a chat to its developer's projects (Phase 0). Keyed by normalized developer + project so
-- it survives a master re-upload (profiles.relink_projects re-checks it on import).
CREATE TABLE IF NOT EXISTS chat_projects(
    jid TEXT NOT NULL, developer_key TEXT, project_key TEXT NOT NULL, project_label TEXT, sheet TEXT,
    status TEXT DEFAULT 'ok', assigned_by TEXT, assigned_at INTEGER, PRIMARY KEY(jid, project_key));
-- every team change to a chat's developer / projects: the learning label (append-only)
CREATE TABLE IF NOT EXISTS assignment_log(
    id INTEGER PRIMARY KEY, jid TEXT, field TEXT, old TEXT, new TEXT, by TEXT, at INTEGER);

-- measured API usage per call (Phase 1 cost reporting)
CREATE TABLE IF NOT EXISTS llm_usage(
    id INTEGER PRIMARY KEY, ts INTEGER, purpose TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
    cache_read INTEGER, cache_write INTEGER, batch INTEGER DEFAULT 0);

-- Phase 2: developer-scoped aliases (a name can mean different projects for different developers; '' = any)
CREATE TABLE IF NOT EXISTS dev_aliases(
    alias_key TEXT NOT NULL, developer_key TEXT NOT NULL DEFAULT '', project_key TEXT, label TEXT,
    confirmed_at INTEGER, PRIMARY KEY(alias_key, developer_key));
-- MATCHER=shadow: where the new matcher disagrees with the legacy one (proposals still use legacy)
CREATE TABLE IF NOT EXISTS matcher_shadow(
    id INTEGER PRIMARY KEY, ts INTEGER, project TEXT, developer TEXT, sheet TEXT, legacy_key TEXT,
    legacy_state TEXT, v2_key TEXT, v2_state TEXT, v2_p REAL);

-- Phase 3: learning memory. One row per PERSON decision (and Reopen of a brain decision); keyed by row_key and
-- developer, never by master_rows.id, so it survives a master re-upload.
CREATE TABLE IF NOT EXISTS learned_examples(
    id INTEGER PRIMARY KEY, proposal_id INTEGER, source_kind TEXT, source_ref TEXT, input_text TEXT,
    normalized_text TEXT, chat_jid TEXT, chat_developer_key TEXT, developer_key TEXT, project_key TEXT, row_key TEXT,
    field TEXT, output_json TEXT, decision TEXT, reason TEXT, created_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_le_kind ON learned_examples(source_kind, id);

CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY, name TEXT, status TEXT, started_at INTEGER, finished_at INTEGER, log TEXT);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _add_columns(c, table, cols):
    have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    for col in cols:
        if col.split()[0] not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")


def init():
    with connect() as c:
        c.executescript(SCHEMA)
        import search
        search.ensure(c)  # FTS5 message index (created once, then caught up with any unindexed rows)
        c.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")  # container restarted mid-job
        pcols = {r[1] for r in c.execute("PRAGMA table_info(chat_profiles)")}
        for col in ("assigned_by TEXT", "assigned_at INTEGER"):
            if col.split()[0] not in pcols:
                c.execute(f"ALTER TABLE chat_profiles ADD COLUMN {col}")
        _add_columns(c, "proposals", ["row_key TEXT", "first_row_key TEXT", "first_match_state TEXT",
                                      "reject_reason TEXT", "label_row_id INTEGER"])
        _add_columns(c, "brain_reviews", ["row_key TEXT"])
        _add_columns(c, "master_aliases", ["developer_key TEXT"])
        # developer-scoped aliases start from the global ones (developer_key '' = any developer, today's behaviour)
        c.execute("INSERT OR IGNORE INTO dev_aliases(alias_key,developer_key,project_key,label,confirmed_at) "
                  "SELECT alias_key, COALESCE(developer_key,''), project_key, label, confirmed_at FROM master_aliases")
        cols = {r[1] for r in c.execute("PRAGMA table_info(proposals)")}
        if "decided_by" not in cols:  # 'brain' when the brain approved or rejected it, else a person
            c.execute("ALTER TABLE proposals ADD COLUMN decided_by TEXT")
        if not c.execute("SELECT 1 FROM meta WHERE key='groups_in_scope_v1'").fetchone():
            # owner decision: every WhatsApp group is read; private chats stay opt-in. A group can still be unticked.
            c.execute("UPDATE group_scope SET in_scope=1 WHERE jid LIKE '%@g.us'")
            c.execute("INSERT INTO meta VALUES('groups_in_scope_v1', '1')")


def reset():
    """Delete every message, file, decision and inventory row, and the downloaded media. Schema stays."""
    with connect() as c:
        import search
        # FTS5 keeps its own shadow tables (<name>_data, _config, ...); clearing the virtual table clears them
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                          "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE ?", (search.FTS + "_%",))]
        c.execute("PRAGMA foreign_keys=OFF")
        for t in tables:
            c.execute(f"DELETE FROM {t}")
    for folder in (MEDIA_DIR, MASTER_DIR):
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    os.remove(path)
    c = sqlite3.connect(DB)
    c.execute("VACUUM")
    c.close()
    return len(tables)


def scoped_groups(c):
    return [r[0] for r in c.execute("SELECT jid FROM group_scope WHERE in_scope=1")]
