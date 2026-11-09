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
    cells = {}
    for r in q(built, "SELECT raw_json FROM source_rows WHERE kind='sheet_row'"):
        row = [c["v"] for c in json.loads(r[0])["cells"]]
        cells[row[0]] = row
    assert "0102" in cells  # leading zero kept
    assert cells["0102"][5] == "950000" and cells["0104"][5] == "1.2M" and cells["0103"][4] == "1200.5"
