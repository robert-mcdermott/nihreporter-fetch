import csv
import io
import json
import sqlite3

import pytest

from nihreporter_fetch import open_sink, print_projects, resolve_output, save_results
from nihreporter_fetch.outputs import CSV_FIELDS, csv_row

INFO = {"source": "test"}


def test_resolve_output():
    assert resolve_output(None, None) == ("projects.json", "json")
    assert resolve_output("x.NDJSON", None) == ("x.NDJSON", "jsonl")
    assert resolve_output("x.db", None) == ("x.db", "sqlite")
    assert resolve_output(None, "csv") == ("projects.csv", "csv")
    assert resolve_output("-", None) == ("-", "json")
    with pytest.raises(ValueError):
        resolve_output("x.json", "xlsx")


def test_csv_row_flattens_records(records):
    row = csv_row({**records[10303004], "publications": [3, 1]})
    assert set(row) == set(CSV_FIELDS)
    assert row["principal_investigators"] == (
        "OLUGBENGA G. OGEDEGBE; MILAGROS C ROSAL; Antoinette M Schoenthaler (contact)"
    )
    assert (row["admin_ic"], row["covid_response"], row["publication_count"], row["publications"]) == (
        "NIMHD",
        "C4",
        2,
        "3; 1",
    )
    assert row["spending_categories"].startswith("Behavioral and Social Science; Cardiovascular")
    row = csv_row(records[11076998])
    # Missing values are empty cells, not "None"; unknown publications have no count.
    assert (row["award_amount"], row["publication_count"], row["org_state"]) == ("", "", "OH")


def test_json_merge_replaces_by_appl_id(tmp_path, records):
    path = str(tmp_path / "out.json")
    first, second = records[11124893], records[11014863]
    assert save_results([first], INFO, path) == 1
    changed = {**first, "project_title": "Updated"}
    assert save_results([changed, second], INFO, path, merge=True) == 2
    doc = json.loads((tmp_path / "out.json").read_text())
    assert doc["schema_version"] == 1 and doc["search_info"]["total_projects"] == 2
    assert [p["project_title"] for p in doc["projects"]] == ["Updated", second["project_title"]]


def test_json_resume_knows_existing_ids(tmp_path, records):
    path = str(tmp_path / "out.json")
    save_results([records[11124893]], INFO, path)
    sink = open_sink(path, "json", resume=True)
    assert sink.existing_ids == {11124893}
    (tmp_path / "bad.json").write_text("[]")
    with pytest.raises(ValueError, match="isn't nihreporter-fetch JSON"):
        open_sink(str(tmp_path / "bad.json"), "json", merge=True)


def test_jsonl_streams_and_merges(tmp_path, records):
    path = str(tmp_path / "out.jsonl")
    a, b, c = records[11124893], records[11014863], records[8444852]
    sink = open_sink(path, "jsonl")
    sink.write([a, b])
    # Each batch is on disk as soon as it's written.
    assert len((tmp_path / "out.jsonl").read_text().splitlines()) == 2
    assert sink.close(INFO) == 2
    assert save_results([{**a, "project_title": "New"}, c], INFO, path, "jsonl", merge=True) == 3
    lines = [json.loads(line) for line in (tmp_path / "out.jsonl").read_text().splitlines()]
    assert [(p["appl_id"], p["project_title"] == "New") for p in lines] == [
        (11124893, True),
        (8444852, False),
        (11014863, False),
    ]


def test_jsonl_resume_drops_partial_line(tmp_path, records, caplog):
    path = tmp_path / "out.jsonl"
    path.write_text(json.dumps(records[11124893]) + "\n" + '{"appl_id": 1')
    sink = open_sink(str(path), "jsonl", resume=True)
    assert sink.existing_ids == {11124893}
    sink.write([records[11014863]])
    assert sink.close(INFO) == 2
    assert [json.loads(line)["appl_id"] for line in path.read_text().splitlines()] == [11124893, 11014863]
    assert "incomplete last line" in caplog.text


def test_csv_merge_and_resume(tmp_path, records):
    path = str(tmp_path / "out.csv")
    save_results([records[11124893], records[11014863]], INFO, path)
    assert save_results([{**records[11124893], "project_title": "New"}], INFO, path, merge=True) == 2
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["appl_id"], r["project_title"]) for r in rows][0] == ("11124893", "New")
    assert len(rows) == 2 and list(rows[0]) == CSV_FIELDS
    sink = open_sink(path, "csv", resume=True)
    assert sink.existing_ids == {11124893, 11014863}
    sink.write([records[8444852]])
    assert sink.close(INFO) == 3


def test_sqlite_upserts_and_normalizes(tmp_path, records):
    path = str(tmp_path / "out.sqlite")
    linked = {**records[10303004], "publications": [111, 222]}
    assert save_results(list(records.values())[:5] + [linked], INFO, path) == 5
    db = sqlite3.connect(path)
    row = db.execute(
        "SELECT project_num, admin_ic, org_name, org_state, award_amount, is_active, covid_response, publication_count "
        "FROM projects WHERE appl_id = 10303004"
    ).fetchone()
    assert row == ("3R01MD013769-03S1", "NIMHD", "NEW YORK UNIVERSITY SCHOOL OF MEDICINE", "NY", 686370, 0, "C4", 2)
    assert db.execute("SELECT COUNT(*) FROM principal_investigators WHERE appl_id = 10303004").fetchone() == (3,)
    assert db.execute("SELECT COUNT(*) FROM terms WHERE appl_id = 11124893").fetchone()[0] == len(
        records[11124893]["pref_terms"]
    )
    assert db.execute("SELECT name FROM spending_categories WHERE category_id = 31 LIMIT 1").fetchone() == ("Aging",)
    assert db.execute("SELECT pmid FROM publications ORDER BY pmid").fetchall() == [(111,), (222,)]
    record = json.loads(db.execute("SELECT record_json FROM projects WHERE appl_id = 11124893").fetchone()[0])
    assert record == records[11124893]
    db.close()

    # A second run replaces records and their child rows; records without publications keep the links.
    changed = {**records[10303004], "project_title": "New", "principal_investigators": [], "publications": None}
    assert save_results([changed], {"source": "again"}, path) == 5
    db = sqlite3.connect(path)
    assert db.execute("SELECT project_title FROM projects WHERE appl_id = 10303004").fetchone() == ("New",)
    assert db.execute("SELECT COUNT(*) FROM principal_investigators WHERE appl_id = 10303004").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM publications").fetchone() == (2,)
    runs = [json.loads(r[0]) for r in db.execute("SELECT search_info FROM runs ORDER BY run_id")]
    assert [(r["source"], r["total_projects"]) for r in runs] == [("test", 5), ("again", 5)]


def test_sqlite_resume_and_stdout_rules(tmp_path, records):
    path = str(tmp_path / "out.db")
    save_results([records[11124893]], INFO, path)
    assert open_sink(path, "sqlite", resume=True).existing_ids == {11124893}
    with pytest.raises(ValueError, match="stdout"):
        open_sink("-", "sqlite")
    with pytest.raises(ValueError, match="can't be used together"):
        open_sink(path, "jsonl", merge=True, resume=True)


def test_json_to_stdout(capsys, records):
    save_results([records[11124893]], INFO, "-")
    doc = json.loads(capsys.readouterr().out)
    assert doc["projects"][0]["appl_id"] == 11124893


def test_print_projects(records, raw_projects):
    out = io.StringIO()
    print_projects([{**records[10303004], "publications": [1, 2]}, raw_projects[-1]], start=3, out=out)
    text = out.getvalue()
    assert "3. " in text and "3R01MD013769-03S1 | FY2021 | NIMHD | R01 | $686,370" in text
    assert "PIs: OLUGBENGA G. OGEDEGBE, MILAGROS C ROSAL, Antoinette M Schoenthaler" in text
    assert "Organization: NEW YORK UNIVERSITY SCHOOL OF MEDICINE (NEW YORK, NY)" in text
    assert "Publications: 2" in text and "URL: https://reporter.nih.gov/project-details/10303004" in text
    # Raw API objects are listed too.
    assert "4. " in text and "project-details/10915693" in text
