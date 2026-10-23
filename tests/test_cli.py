import io
import json
from datetime import date

import pytest
from conftest import fake_api

from nihreporter_fetch import cli
from nihreporter_fetch.cli import build_options, load_config, parse_arguments, parse_list, read_ids, resolve_settings


def test_parse_arguments():
    args = parse_arguments(
        ["CAR T", "--pi", "Corey, Lawrence", "--pi", "Bloom", "--agency", "NCI", "NIAID", "--fiscal-year", "2023-2025"]
        + ["--activity-code", "R01", "--activity-code", "U01", "--min-award", "1,000,000", "--no-subprojects"]
        + ["--max-results", "all", "--text-field", "title", "abstract", "-o", "out.csv", "--publications"]
    )
    assert args.text == "CAR T"
    # Repeating a list option adds to it.
    assert args.pi_names == ["Corey, Lawrence", "Bloom"] and args.activity_codes == ["R01", "U01"]
    assert (args.agencies, args.fiscal_years, args.text_fields) == (
        ["NCI", "NIAID"],
        ["2023-2025"],
        ["title", "abstract"],
    )
    assert (args.min_award, args.subprojects, args.max_results, args.output_file, args.publications) == (
        1_000_000,
        "exclude",
        "all",
        "out.csv",
        True,
    )
    assert (args.merge, args.raw, args.active, args.multi_pi) == (None, None, None, None)


def test_version_01_options_still_work(tmp_path):
    args = parse_arguments(
        ["--criteria-json", '{"criteria": {"fiscal_years": [2023]}}', "--max-records", "10", "--verbose"]
    )
    assert (args.criteria, args.max_results, args.verbose) == ('{"criteria": {"fiscal_years": [2023]}}', 10, True)
    body = tmp_path / "body.json"
    body.write_text("{}")
    assert parse_arguments(["--criteria-file", str(body)]).criteria == f"@{body}"
    with pytest.raises(SystemExit):
        parse_arguments(["--criteria-json", "{}", "--criteria", "{}"])


@pytest.mark.parametrize(
    "bad",
    [
        ["--days", "0"],
        ["--date-from", "01/02/2024"],
        ["--days", "5", "--date-from", "2024-01-01"],
        ["--format", "xlsx"],
        ["--date-field", "posted"],
        ["--max-results", "lots"],
        ["--no-subprojects", "--subprojects-only"],
        ["--text-field", "body"],
        ["--text-operator", "xor"],
        ["--min-award", "-5"],
        ["--pi-profile-id", "abc"],
        ["-q", "-v"],
    ],
)
def test_parse_arguments_rejects_invalid_values(bad):
    with pytest.raises(SystemExit):
        parse_arguments(["x", *bad])


def test_parse_list_supports_quotes_and_lines():
    assert parse_list('"Corey, Lawrence", Bloom') == ["Corey, Lawrence", "Bloom"]
    assert parse_list("\n  A\n  B, C\n") == ["A", "B", "C"]


def test_load_config(tmp_path, caplog):
    config_file = tmp_path / "c.ini"
    config_file.write_text(
        "[search]\n"
        "text = CAR T\n"
        'pi_names = "Corey, Lawrence", Bloom\n'
        "fiscal_years =\n  2023\n  2025\n"
        "agencies = NCI, NIAID\n"
        "days = 60\nmax_results = all\nmin_award = 1000\n"
        "date_from = 2024-01-01\ndate_to = 2024-12-31\nformat = CSV\ndate_field = ADDED\n"
        "text_fields = Title\nsubprojects = exclude\npublications = yes\nraw = no\nmulti_pi = true\n"
        'criteria = {"project_start_date": {"from_date": "2020-01-01"}}\ntypo_key = 1\n'
    )
    config = load_config(str(config_file))
    assert config == {
        "text": "CAR T",
        "pi_names": ["Corey, Lawrence", "Bloom"],
        "fiscal_years": ["2023", "2025"],
        "agencies": ["NCI", "NIAID"],
        "text_fields": ["title"],
        "days": 60,
        "min_award": 1000,
        "publications": True,
        "raw": False,
        "multi_pi": True,
        "date_from": date(2024, 1, 1),
        "date_to": date(2024, 12, 31),
        "subprojects": "exclude",
        "criteria": '{"project_start_date": {"from_date": "2020-01-01"}}',
        "date_field": "added",
        "max_results": "all",
        "format": "csv",
    }
    assert "Ignoring unknown setting 'typo_key'" in caplog.text

    config_file.write_text("[search]\nformat = xlsx\n")
    with pytest.raises(ValueError, match="format"):
        load_config(str(config_file))


def test_settings_precedence():
    config = {"org_names": ["A*"], "days": 60, "max_results": 5, "multi_pi": True}
    settings = resolve_settings(parse_arguments(["--days", "30", "--subprojects-only"]), config)
    assert (settings["days"], settings["max_results"], settings["org_names"]) == (30, 5, ["A*"])
    assert (settings["multi_pi"], settings["subprojects"]) == (True, "only")
    assert settings["output_file"] is None and settings["text_operator"] == "and"


def test_cli_dates_replace_config_dates():
    config = {"date_from": date(2024, 1, 1), "date_to": date(2024, 12, 31), "days": 30}
    settings = resolve_settings(parse_arguments(["--days", "7"]), config)
    assert (settings["date_from"], settings["date_to"], settings["days"]) == (None, date(2024, 12, 31), 7)
    settings = resolve_settings(parse_arguments(["--date-from", "2025-01-01"]), config)
    assert (settings["date_from"], settings["days"]) == (date(2025, 1, 1), None)


def test_build_options(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("# my list\n11124893, 11014863\n")
    args = parse_arguments(["--appl-ids", "8444852", str(ids), "--output", "x.csv", "--merge", "--max-results", "all"])
    options = build_options(resolve_settings(args, {}), quiet=True)
    assert options.appl_ids == ["8444852", "11124893", "11014863"]
    assert (options.mode, options.output_file, options.merge, options.display) == ("ids", "x.csv", True, False)
    assert options.max_results is None


def test_build_options_from_a_01_request_body(caplog):
    body = json.dumps(
        {
            "criteria": {"org_names": ["FRED HUTCH*"]},
            "include_fields": ["ApplId"],
            "limit": 500,
            "sort_field": "fiscal_year",
            "sort_order": "desc",
            "bogus": 1,
        }
    )
    options = build_options(resolve_settings(parse_arguments(["--criteria-json", body]), {}))
    assert options.spec.build_criteria() == {"org_names": ["FRED HUTCH*"]}
    assert (options.sort, options.fields) == ("fiscal_year:desc", None)
    assert "Ignoring include_fields" in caplog.text and "Ignoring request settings: bogus" in caplog.text
    options = build_options(resolve_settings(parse_arguments(["--criteria-json", body, "--raw"]), {}))
    assert options.fields == ["ApplId"]


def test_build_options_validates_values():
    with pytest.raises(ValueError, match="Unknown agency"):
        build_options(resolve_settings(parse_arguments(["x", "--agency", "NCII"]), {}))
    with pytest.raises(ValueError, match="Unknown criteria"):
        build_options(resolve_settings(parse_arguments(["--criteria", '{"fiscal_year": [2024]}']), {}))


def test_read_ids_from_stdin_and_rejects_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("33298401\n19415119\n"))
    assert read_ids(["-"], "PMID") == ["33298401", "19415119"]
    bad = tmp_path / "bad.txt"
    bad.write_text("33298401 PMC123")
    with pytest.raises(ValueError, match="Invalid PMID.*PMC123"):
        read_ids([str(bad)], "PMID")


def test_create_sample_config(tmp_path):
    target = tmp_path / "sample.ini"
    assert cli.create_sample_config(str(target))
    assert load_config(str(target))["org_names"] == ["FRED HUTCH*"]
    assert not cli.create_sample_config(str(target))


def test_repo_sample_config_matches_template():
    with open("sample_config.ini", encoding="utf-8") as f:
        assert f.read() == cli.SAMPLE_CONFIG
    config = load_config("sample_config.ini")
    assert config["fiscal_years"] == ["2024-2026"] and config["max_results"] == "all"
    build_options(resolve_settings(parse_arguments([]), config))


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--org", "X", "--appl-ids", "1"],
        ["--org", "X", "--raw", "--output", "x.csv"],
        ["--org", "X", "--output", "-", "--format", "sqlite"],
        ["--org", "X", "--merge", "--resume"],
        ["--org", "X", "--fields", "ProjectTitle"],
        ["--org", "X", "--raw", "--publications"],
        ["--org", "X", "--sort", "project_title"],
        ["--appl-ids", "1", "--since-last-run", "s.json"],
        ["--appl-ids", "1", "--count"],
        ["--org", "X", "--date-from", "2024-01-01", "--since-last-run", "s.json"],
        ["--active", "--fiscal-year", "2024"],
        ["--appl-ids", "missing-file.txt"],
        ["--config", "missing.ini"],
        ["--endpoint", "publications", "--org", "X"],
    ],
)
def test_main_rejects_invalid_usage(argv):
    assert cli.main([*argv, "-q"]) == 1


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.startswith("nihreporter-fetch ")


def test_count(monkeypatch, capsys):
    monkeypatch.setattr("nihreporter_fetch.client.make_session", lambda **kwargs: fake_api())
    assert cli.main(["--org", "FRED HUTCH*", "--count", "-q"]) == 0
    assert capsys.readouterr().out == "16\n"


def test_main_runs_a_search(tmp_path, monkeypatch):
    monkeypatch.setattr("nihreporter_fetch.client.make_session", lambda **kwargs: fake_api())
    out, summary = tmp_path / "out.jsonl", tmp_path / "summary.json"
    code = cli.main(["glioblastoma", "--agency", "nci", "-q", "-o", str(out), "--summary", str(summary)])
    assert code == 0
    info = json.loads(summary.read_text())
    assert (info["total_projects"], info["complete"], info["source"]) == (4, True, "search")
    assert info["criteria"]["advanced_text_search"]["search_text"] == "glioblastoma"
    assert info["criteria"]["agencies"] == ["NCI"]
    assert len(out.read_text().splitlines()) == 4
