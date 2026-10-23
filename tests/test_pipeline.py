import json
import sqlite3
from datetime import date, timedelta

import pytest
from conftest import FakeResponse, FakeSession, fake_api, projects_handler, sample_projects, synthetic_project

from nihreporter_fetch import Options, QuerySpec, ReporterClient, ReporterError, count_matches, run
from nihreporter_fetch.pipeline import EXIT_OK, EXIT_PARTIAL


def options(tmp_path, **kwargs):
    defaults = {"spec": QuerySpec(org_names=["FRED HUTCH*"]), "display": False}
    return Options(**{**defaults, "output_file": str(tmp_path / "out.json"), **kwargs})


def test_run_writes_complete_output(tmp_path, raw_projects):
    result = run(options(tmp_path), session=fake_api())
    assert result.exit_code == EXIT_OK and result.output == str(tmp_path / "out.json")
    doc = json.loads((tmp_path / "out.json").read_text())
    info = doc["search_info"]
    assert info["tool"]["name"] == "nihreporter-fetch" and info["generated_on"].endswith("Z")
    assert (info["api"], info["data_timestamp"]) == ("NIH RePORTER API v2", max(p["date_added"] for p in raw_projects))
    assert (info["complete"], info["total_projects"], info["retrieved"], info["total_found"]) == (True, 16, 16, 16)
    assert info["with_publications"] is False and info["criteria"] == {"org_names": ["FRED HUTCH*"]}
    assert doc["projects"][0]["appl_id"] == 100336


def test_latest_update_failure_does_not_stop_the_run(tmp_path):
    handler = projects_handler()

    def no_latest_update(url, body):
        return handler(url, body) if body["criteria"] else FakeResponse(status=503)

    session = FakeSession({"projects/search": no_latest_update})
    result = run(options(tmp_path), session=session)
    assert result.search_info["data_timestamp"] is None and result.exit_code == EXIT_OK


def test_run_reports_partial_results(tmp_path, monkeypatch):
    monkeypatch.setattr(ReporterClient, "page_size", 5)
    result = run(options(tmp_path), session=fake_api(fail_offsets=(5,)))
    assert result.exit_code == EXIT_PARTIAL
    assert result.search_info["complete"] is False and result.search_info["total_projects"] == 5


def test_run_fails_when_nothing_could_be_fetched(tmp_path):
    session = FakeSession({"projects/search": lambda u, b: FakeResponse(status=503)})
    with pytest.raises(ReporterError, match="Error searching"):
        run(options(tmp_path), session=session)
    assert not (tmp_path / "out.json").exists()


def test_run_writes_empty_output_for_no_matches(tmp_path, capsys):
    run(options(tmp_path, display=True), session=fake_api(projects=[]))
    doc = json.loads((tmp_path / "out.json").read_text())
    assert doc["projects"] == [] and doc["search_info"]["total_projects"] == 0
    assert "No projects found." in capsys.readouterr().out


def test_listing_is_printed_unless_data_goes_to_stdout(tmp_path, capsys):
    run(options(tmp_path, display=True, max_results=2), session=fake_api())
    out = capsys.readouterr().out
    assert "1. " in out and "2. " in out and "URL: https://reporter.nih.gov/project-details/" in out
    run(options(tmp_path, display=True, max_results=2, output_file="-"), session=fake_api())
    assert json.loads(capsys.readouterr().out)["search_info"]["retrieved"] == 2


def test_days_become_an_award_notice_date_range(tmp_path):
    session = fake_api()
    run(options(tmp_path, days=30), session=session)
    today = date.today()
    expected = {"from_date": (today - timedelta(days=30)).isoformat(), "to_date": f"{today}T23:59:59"}
    assert session.searches()[0]["criteria"]["award_notice_date"] == expected


def added_projects():
    return [
        synthetic_project(0, date_added="2024-01-05T17:03:54"),
        synthetic_project(1, date_added="2024-02-10T17:03:54"),
        synthetic_project(2, date_added="2024-03-15T17:03:54"),
    ]


def test_incremental_runs(tmp_path, caplog):
    state = tmp_path / "state.json"
    out = str(tmp_path / "out.sqlite")
    session = fake_api(projects=added_projects())
    # The first run has no state, so it fetches every match as a baseline.
    first = run(options(tmp_path, since_last_run=str(state), output_file=out), session=session)
    assert "date_added" not in session.searches()[0]["criteria"] and first.search_info["retrieved"] == 3
    saved = json.loads(state.read_text())
    assert (saved["last_end_date"], saved["date_field"]) == (date.today().isoformat(), "added")
    assert saved["criteria"] == {"org_names": ["FRED HUTCH*"]} and saved["last_total_found"] == 3

    # Later runs start from the stored date, on date_added unless another date field is chosen.
    state.write_text(json.dumps({**saved, "last_end_date": "2024-02-10"}))
    session = fake_api(projects=added_projects())
    second = run(options(tmp_path, since_last_run=str(state), output_file=out), session=session)
    assert session.searches()[0]["criteria"]["date_added"] == {"from_date": "2024-02-10"}
    assert second.search_info["retrieved"] == 2 and second.search_info["total_projects"] == 3
    assert json.loads(state.read_text())["last_end_date"] == date.today().isoformat()

    # A state file for a different search still works, with a warning.
    run(options(tmp_path, spec=QuerySpec(org_names=["OTHER*"]), since_last_run=str(state), output_file=out), session)
    assert "different search" in caplog.text


def test_incremental_state_not_advanced_when_truncated_or_failed(tmp_path, monkeypatch, caplog):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"last_end_date": "2024-01-01", "criteria": {}, "date_field": "added"}))
    run(options(tmp_path, since_last_run=str(state), max_results=1), session=fake_api(projects=added_projects()))
    assert json.loads(state.read_text())["last_end_date"] == "2024-01-01"
    assert "not every match was retrieved" in caplog.text

    monkeypatch.setattr(ReporterClient, "page_size", 2)
    session = fake_api(projects=added_projects(), fail_offsets=(2,))
    result = run(options(tmp_path, since_last_run=str(state)), session=session)
    assert result.exit_code == EXIT_PARTIAL
    assert json.loads(state.read_text())["last_end_date"] == "2024-01-01"
    assert "some batches failed" in caplog.text


def test_missing_publications_make_the_run_partial_but_advance_the_state(tmp_path, caplog):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"last_end_date": "2024-01-01", "criteria": {}, "date_field": "added"}))
    session = fake_api(projects=added_projects())
    session.routes["publications/search"] = lambda url, body: FakeResponse(status=500)
    result = run(options(tmp_path, since_last_run=str(state), publications=True), session=session)
    assert result.exit_code == EXIT_PARTIAL and result.search_info["complete"] is False
    assert len(result.search_info["failed_publications"]) == 3 and not result.search_info["failed_batches"]
    assert json.loads(state.read_text())["last_end_date"] == date.today().isoformat()
    assert "Publications of 3 core project(s) are missing" in caplog.text


def test_resume_skips_existing_records(tmp_path):
    out = str(tmp_path / "out.jsonl")
    run(options(tmp_path, output_file=out, max_results=4), session=fake_api())
    result = run(options(tmp_path, output_file=out, resume=True), session=fake_api())
    assert (result.search_info["skipped_existing"], result.search_info["total_projects"]) == (4, 16)
    lines = (tmp_path / "out.jsonl").read_text().splitlines()
    assert len(lines) == len({json.loads(line)["appl_id"] for line in lines}) == 16


def test_publications_are_attached(tmp_path):
    session = fake_api()
    result = run(options(tmp_path, publications=True, output_file=str(tmp_path / "out.sqlite")), session=session)
    assert result.search_info["with_publications"] is True
    db = sqlite3.connect(tmp_path / "out.sqlite")
    assert db.execute("SELECT publication_count FROM projects WHERE appl_id = 10303004").fetchone() == (18,)
    assert db.execute("SELECT COUNT(*) FROM publications WHERE core_project_num = 'R01MD013769'").fetchone() == (18,)
    assert db.execute("SELECT COUNT(*) FROM projects WHERE publication_count IS NULL").fetchone() == (0,)


def test_pmids_mode_attaches_publications(tmp_path):
    linked = [synthetic_project(0, appl_id=11325011, core_project_num="T32DA007292")]
    result = run(options(tmp_path, spec=QuerySpec(), pmids=["33298401"]), session=fake_api(projects=linked))
    doc = json.loads((tmp_path / "out.json").read_text())
    assert [(p["appl_id"], p["publications"]) for p in doc["projects"]] == [(11325011, [33298401])]
    assert (result.search_info["source"], result.search_info["with_publications"]) == ("pmids", True)


def test_ids_mode(tmp_path):
    result = run(options(tmp_path, spec=QuerySpec(), appl_ids=[11124893, "99999999"]), session=fake_api())
    assert (result.search_info["total_projects"], result.search_info["missing_ids"]) == (1, [99999999])


def test_from_raw_needs_no_network(tmp_path):
    raw = tmp_path / "old.json"
    raw.write_text(json.dumps({"meta": {}, "results": sample_projects()}))
    result = run(options(tmp_path, spec=QuerySpec(), from_raw=str(raw)), session=FakeSession({}))
    assert (result.search_info["source"], result.search_info["total_projects"]) == ("raw", 16)
    assert result.search_info["data_timestamp"] is None


def test_summary_is_written(tmp_path):
    summary = tmp_path / "summary.json"
    run(options(tmp_path, summary=str(summary)), session=fake_api())
    assert json.loads(summary.read_text())["total_projects"] == 16


def test_count_matches():
    session = fake_api()
    assert count_matches(Options(spec=QuerySpec(org_names=["X"])), session=session) == 16
    with pytest.raises(ValueError, match="searches only"):
        count_matches(Options(appl_ids=[1]), session=session)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"spec": QuerySpec()}, "No search specified"),
        ({"appl_ids": [1]}, "only one of"),
        ({"spec": QuerySpec(), "appl_ids": [1], "sort": "appl_id"}, "searches only"),
        ({"sort": "project_title"}, "Can't sort"),
        ({"spec": QuerySpec(), "pmids": [1], "since_last_run": "s.json"}, "only one of"),
        ({"spec": QuerySpec(org_names=["X"], date_from=date(2024, 1, 1)), "days": 5}, "--days and --date-from"),
        ({"max_results": 0}, "positive"),
        ({"fields": ["ProjectTitle"]}, "only applies to --raw"),
        ({"raw": True, "fields": ["Title"]}, "Unknown project field"),
        ({"raw": True, "publications": True}, "can't be combined with --raw"),
        ({"raw": True, "output_file": "x.csv"}, "JSON or JSONL"),
        ({"output_file": "-", "format": "sqlite"}, "stdout"),
        ({"merge": True, "resume": True}, "can't be used together"),
    ],
)
def test_invalid_options(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        options(tmp_path, **kwargs).validate()
