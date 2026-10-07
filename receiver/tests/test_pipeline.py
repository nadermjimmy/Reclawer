import json
import pytest
import fixtures
from fixtures import UAE, EGY


@pytest.fixture(scope="module")
def built():
    return fixtures.run_pipeline()


def q(db, sql, *a):
    with db.connect() as c:
        return c.execute(sql, a).fetchall()


def test_history_and_files(built):
    assert q(built, "SELECT COUNT(*) FROM source_messages")[0][0] == 7  # only ticked chats are pulled
    # identical workbook posted twice is stored once, both posts recorded
    assert q(built, "SELECT COUNT(*) FROM source_files")[0][0] == 3  # Egypt group not in scope -> not downloaded
    assert q(built, "SELECT COUNT(*) FROM file_occurrences")[0][0] == 4
    assert q(built, "SELECT status FROM media_fetch WHERE wa_id='gone'")[0][0] == "failed"


def test_exact_source_values(built):
    rows = {r["unit_code_raw"]: r for r in q(built, "SELECT * FROM unit_snapshots")}
    assert "0102" in rows  # leading zero kept
    assert rows["0102"]["price_raw"] == "950000" and rows["0102"]["price_value"] == 950000
    assert rows["0104"]["price_raw"] == "1.2M" and rows["0104"]["price_value"] is None
    assert json.loads(rows["0103"]["areas_json"])[0]["raw"] == "1200.5"


def test_hierarchy_and_owner_rules(built):
    names = [r[0] for r in q(built, "SELECT name FROM projects ORDER BY name")]
    assert names == ["Brabus Island", "Verdana"]  # no 'Dubai Hills', no 'Brabus Island Tower 2'
    phases = {r[0] for r in q(built, "SELECT raw_label FROM project_phases f JOIN projects p ON p.id=f.project_id "
                                     "WHERE p.name='Brabus Island'")}
    assert phases == {"Tower 1", "Tower 2"}
    hills = q(built, "SELECT mapping_state FROM unit_snapshots WHERE unit_code_raw='A-1'")[0][0]
    assert hills == "queued"
    reasons = " ".join(r[0] for r in q(built, "SELECT reason FROM review_issues"))
    assert "Dubai Hills' is listed by the owner as not a project" in reasons
    assert q(built, "SELECT COUNT(*) FROM review_issues WHERE issue_type='phase_conflict'")[0][0] == 1
    assert q(built, "SELECT unit_status FROM unit_snapshots WHERE unit_code_raw='V-2'")[0][0] == "available"


def test_low_price_status_and_derived(built):
    r = q(built, "SELECT * FROM unit_snapshots WHERE unit_code_raw='0102'")[0]
    assert r["mapping_state"] == "mapped" and r["unit_status"] == "available"
    assert r["price_per_sqft"] == round(950000 / 800, 2) and "listed_price / area_sq_ft" in r["ppsf_inputs"]
    assert q(built, "SELECT unit_status FROM unit_snapshots WHERE unit_code_raw='0104'")[0][0] == "unknown"
    v = q(built, "SELECT price_per_sqft FROM unit_snapshots WHERE unit_code_raw='V-1'")[0][0]
    assert v is None  # area unit not stated -> not calculated
    assert q(built, "SELECT COUNT(*) FROM review_issues WHERE issue_type='duplicate_unit_row'")[0][0] == 1


def test_facts_and_conflicts(built):
    assert q(built, "SELECT COUNT(*) FROM fact_links")[0][0] == 2
    assert q(built, "SELECT COUNT(*) FROM review_issues WHERE issue_type='fact_conflict'")[0][0] == 1
    missing = q(built, "SELECT reason FROM review_issues WHERE issue_type='missing_facts'")
    assert any("service charge" in r[0] for r in missing)


def test_reconciliation_and_rebuild_idempotent(built):
    import build, jobs
    with built.connect() as c:
        rep = build.reconcile(c)
    assert rep["adds_up"] and rep["unit_rows_in_sources"] == 8
    assert rep["mapped"] + rep["queued"] + rep["excluded_by_scope"] == 8
    before = q(built, "SELECT COUNT(*) FROM units")[0][0]
    jobs.run_now("rebuild", build.rebuild)
    assert q(built, "SELECT COUNT(*) FROM units")[0][0] == before
    assert q(built, "SELECT COUNT(*) FROM source_messages")[0][0] == 7  # only ticked chats are pulled


def test_reviewer_decisions(built):
    import build, jobs
    key = q(built, "SELECT context_key FROM unit_snapshots WHERE unit_code_raw='A-1'")[0][0]
    with built.connect() as c:
        c.execute("UPDATE label_mappings SET project_name='Hills Park', review_state='approved' WHERE context_key=?",
                  (key,))
    jobs.run_now("rebuild", build.rebuild)
    assert q(built, "SELECT mapping_state FROM unit_snapshots WHERE unit_code_raw='A-1'")[0][0] == "mapped"
    r = q(built, "SELECT restriction_raw FROM unit_snapshots WHERE unit_code_raw='A-1'")[0][0]
    assert r == "No Flip or Change the Unit"
    a = q(built, "SELECT price_per_sqft, ppsf_inputs FROM unit_snapshots WHERE unit_code_raw='A-1'")[0]
    assert a[0] == round(1500000 / (100 * 10.7639104167), 2) and "sq m" in a[1]


def test_rules():
    import rules
    assert rules.why_not_a_project("Tower 2") and rules.why_not_a_project("Phase 3")
    assert rules.why_not_a_project("Dubai Hills") and rules.why_not_a_project("No Flip or Change the Unit")
    assert rules.why_not_a_project("Brabus Island") is None
    assert rules.image_blocked("A Mercedes G-Class parked outside") == "g-class"
