"""Command-line interface."""

from __future__ import annotations

import argparse
import configparser
import csv
import logging
import os
import re
import sqlite3
import sys
from datetime import date
from typing import Any

from ._meta import ReporterError, __version__
from .outputs import OUTPUT_FORMATS
from .pipeline import EXIT_ERROR, Options, count_matches, run
from .query import (
    COVID_RESPONSE,
    DATE_FIELDS,
    SORT_FIELDS,
    SUBPROJECTS,
    TEXT_FIELDS,
    TEXT_OPERATORS,
    QuerySpec,
    load_criteria,
)

log = logging.getLogger("nihreporter_fetch")

SEARCH_LIST_KEYS = (
    "fiscal_years",
    "pi_names",
    "pi_profile_ids",
    "po_names",
    "org_names",
    "org_names_exact",
    "org_cities",
    "org_states",
    "org_countries",
    "project_nums",
    "agencies",
    "activity_codes",
    "application_types",
    "opportunity_numbers",
    "cong_dists",
    "spending_categories",
    "covid_response",
)
DEFAULTS: dict[str, Any] = {
    "text": None,
    "text_fields": [],
    "text_operator": "and",
    **{key: [] for key in SEARCH_LIST_KEYS},
    "match_all_categories": False,
    "min_award": None,
    "max_award": None,
    "active": False,
    "multi_pi": False,
    "subprojects": "include",
    "newly_added": False,
    "criteria": None,
    "date_field": None,
    "days": None,
    "date_from": None,
    "date_to": None,
    "max_results": 1000,
    "sort": None,
    "appl_ids": [],
    "pmids": [],
    "from_raw": None,
    "raw": False,
    "fields": [],
    "publications": False,
    "output_file": None,
    "format": None,
    "merge": False,
    "resume": False,
    "summary": None,
    "since_last_run": None,
}

LIST_KEYS = ("text_fields", *SEARCH_LIST_KEYS, "appl_ids", "pmids", "fields")
INT_KEYS = ("days", "min_award", "max_award")
DATE_KEYS = ("date_from", "date_to")
BOOL_KEYS = ("match_all_categories", "active", "multi_pi", "newly_added", "raw", "publications", "merge", "resume")
STR_KEYS = tuple(k for k in DEFAULTS if k not in (*LIST_KEYS, *INT_KEYS, *DATE_KEYS, *BOOL_KEYS))
CHOICES = {
    "format": OUTPUT_FORMATS,
    "date_field": tuple(DATE_FIELDS),
    "text_operator": TEXT_OPERATORS,
    "subprojects": SUBPROJECTS,
    "text_fields": tuple(TEXT_FIELDS),
}
# Parts of a 0.1-style request body (--criteria-json/--criteria-file) that paging now handles.
IGNORED_BODY_KEYS = ("limit", "offset", "use_relevance", "is_initial_search", "search_id")


def parse_list(value: str) -> list[str]:
    """
    Parse a config list: comma-separated and/or one item per line. Quote items that contain
    commas, e.g. ``"Corey, Lawrence", "Gilbert, Peter"``.
    """
    items: list[str] = []
    for line in value.splitlines():
        for row in csv.reader([line], skipinitialspace=True):
            items.extend(item.strip() for item in row if item.strip())
    return items


def _choice(value: str, key: str) -> str:
    value = value.strip().lower()
    if value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}, got {value!r}")
    return value


def _max_results(value: str) -> int | str:
    """A positive number of projects, or "all"."""
    if value.strip().lower() == "all":
        return "all"
    number = int(value)
    if number <= 0:
        raise ValueError(f"must be a positive integer or 'all', got {value}")
    return number


def load_config(config_file: str) -> dict[str, Any]:
    """
    Load settings from the [search] section of an INI file. Only keys present in the file are
    returned, so callers can layer CLI > config > defaults.
    """
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_file)
    if "search" not in config:
        return {}
    section = config["search"]

    settings: dict[str, Any] = {}
    for key in section:
        if key not in DEFAULTS:
            log.warning("Ignoring unknown setting %r in %s", key, config_file)
    for key in LIST_KEYS:
        if key in section:
            settings[key] = parse_list(section[key])
    for key in INT_KEYS:
        if section.get(key, "").strip():
            settings[key] = section.getint(key)
    for key in BOOL_KEYS:
        if section.get(key, "").strip():
            settings[key] = section.getboolean(key)
    for key in DATE_KEYS:
        if section.get(key, "").strip():
            settings[key] = date.fromisoformat(section[key].strip())
    for key in STR_KEYS:
        if section.get(key, "").strip():
            settings[key] = section[key].strip()
    for key in CHOICES:
        if isinstance(settings.get(key), str):
            settings[key] = _choice(settings[key], key)
    if "text_fields" in settings:
        settings["text_fields"] = [_choice(f, "text_fields") for f in settings["text_fields"]]
    if "max_results" in settings:
        settings["max_results"] = _max_results(settings["max_results"])
    return settings


def read_ids(items: list[str], name: str) -> list[str]:
    """
    Collect numeric IDs from a mix of IDs, files and '-' (stdin). Files hold IDs separated by
    whitespace or commas; lines starting with # are ignored.
    """
    tokens: list[str] = []
    for item in items:
        if item.strip().isdigit():
            tokens.append(item.strip())
            continue
        if item == "-":
            content = sys.stdin.read()
        else:
            with open(item, encoding="utf-8") as f:
                content = f.read()
        tokens.extend(
            t
            for line in content.splitlines()
            if not line.lstrip().startswith("#")
            for t in re.split(r"[\s,]+", line)
            if t
        )
    if bad := [t for t in tokens if not t.isdigit()]:
        raise ValueError(f"Invalid {name}(s): {', '.join(bad[:5])} (expected whole numbers)")
    return tokens


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return number


def _amount(value: str) -> int:
    number = int(value.replace(",", "").replace("_", ""))
    if number < 0:
        raise argparse.ArgumentTypeError(f"must not be negative, got {value}")
    return number


def _max_results_arg(value: str) -> int | str:
    try:
        return _max_results(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a date as YYYY-MM-DD, got {value!r}") from None


def _flag(group, *names: str, dest: str, const: Any = True, help: str) -> None:
    """A flag that stays None when not given, so config file values can apply."""
    group.add_argument(*names, dest=dest, action="store_const", const=const, default=None, help=help)


def _list(group, *names: str, dest: str, metavar: str, help: str, **kwargs: Any) -> None:
    """A list option: ``--opt A B`` and ``--opt A --opt B`` both collect [A, B]."""
    group.add_argument(*names, dest=dest, metavar=metavar, nargs="+", action="extend", help=help, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nihreporter-fetch",
        description="Search NIH RePORTER and export structured project records as JSON, JSONL, CSV or SQLite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "CAR T cell" --fiscal-year 2024 --agency NCI
  %(prog)s --org "FRED HUTCH*" --days 730 --date-field award_notice -o fred_hutch.sqlite
  %(prog)s --pi "Corey, Lawrence" --publications -o corey.json
  %(prog)s --activity-code R01 R21 --org-state WA --fiscal-year 2023-2025 --max-results all -o wa.csv
  %(prog)s --agency NIAID --covid-response C4 C5 C6 --count
  %(prog)s --appl-ids 11124893 11014863 --output - | jq '.projects[].project_title'
  %(prog)s --pmids 33298401 -o funded_by.json
  %(prog)s --org "FRED HUTCH*" --since-last-run fh.state.json -o fred_hutch.sqlite
  %(prog)s --config config.ini

Names, organizations and project numbers accept * wildcards. Lists are ORed; options are ANDed.
Exit codes: 0 success, 1 error, 2 partial results (some batches failed; see search_info.failed_batches).
        """,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("text", nargs="?", help="Text to search for in project titles, abstracts and terms")

    search = parser.add_argument_group("search fields (OR within a field, AND across fields)")
    _list(
        search,
        "--text-field",
        dest="text_fields",
        metavar="FIELD",
        choices=tuple(TEXT_FIELDS),
        help=f"Where TEXT is searched: {', '.join(TEXT_FIELDS)} (default: all)",
    )
    search.add_argument(
        "--text-operator",
        choices=TEXT_OPERATORS,
        help='How TEXT\'s words combine: and (default), or, advanced (AND/OR/NOT, "phrases")',
    )
    _list(search, "--pi", dest="pi_names", metavar="NAME", help='Principal investigators: "Last, First" or any name')
    _list(search, "--pi-profile-id", dest="pi_profile_ids", metavar="ID", type=int, help="PI profile IDs")
    _list(search, "--po", dest="po_names", metavar="NAME", help="Program officers, like --pi")
    _list(search, "--org", dest="org_names", metavar="NAME", help='Organizations, partial names (e.g. "FRED HUTCH*")')
    _list(search, "--org-exact", dest="org_names_exact", metavar="NAME", help="Organizations, exact names")
    _list(search, "--org-city", dest="org_cities", metavar="CITY", help="Organization cities")
    _list(search, "--org-state", dest="org_states", metavar="ST", help="Organization states (two-letter codes)")
    _list(search, "--org-country", dest="org_countries", metavar="COUNTRY", help='Organization countries ("CANADA")')
    _list(search, "--project-num", dest="project_nums", metavar="NUM", help="Project numbers, e.g. R01CA* or 5U54CA*")

    filters = parser.add_argument_group("filters (OR within a filter, AND across filters; case-insensitive)")
    _list(filters, "--fiscal-year", dest="fiscal_years", metavar="YEAR", help="Fiscal years or ranges: 2024 2020-2022")
    _list(
        filters,
        "--agency",
        dest="agencies",
        metavar="IC",
        help="Administering institute, center or agency: NCI, NIAID, ... (NIH or ALLCDC for all)",
    )
    _list(filters, "--activity-code", dest="activity_codes", metavar="CODE", help="Activity codes: R01, K99, U54, ...")
    _list(
        filters,
        "--application-type",
        dest="application_types",
        metavar="TYPE",
        help="Application types: 1 new, 2 renewal, 3 supplement, 5 continuation, ... (1-9)",
    )
    _list(
        filters,
        "--opportunity-number",
        dest="opportunity_numbers",
        metavar="NUM",
        help="Funding opportunity numbers, e.g. PAR-21-123",
    )
    _list(filters, "--cong-dist", dest="cong_dists", metavar="DIST", help="Congressional districts, e.g. WA-07")
    _list(filters, "--spending-category", dest="spending_categories", metavar="ID", type=int, help="RCDC category IDs")
    _flag(filters, "--match-all-categories", dest="match_all_categories", help="Require every --spending-category")
    _list(
        filters,
        "--covid-response",
        dest="covid_response",
        metavar="CODE",
        help=f"COVID-19 funding: {', '.join(COVID_RESPONSE)}",
    )
    filters.add_argument("--min-award", type=_amount, metavar="USD", help="Minimum award amount")
    filters.add_argument("--max-award", type=_amount, metavar="USD", help="Maximum award amount")
    _flag(filters, "--active", dest="active", help="Currently active projects (can't be combined with --fiscal-year)")
    _flag(filters, "--multi-pi", dest="multi_pi", help="Projects with more than one PI")
    subprojects = filters.add_mutually_exclusive_group()
    _flag(subprojects, "--no-subprojects", dest="subprojects", const="exclude", help="Leave out subprojects")
    _flag(subprojects, "--subprojects-only", dest="subprojects", const="only", help="Only subprojects")
    _flag(filters, "--newly-added", dest="newly_added", help="Projects added in RePORTER's latest weekly update")
    filters.add_argument(
        "--criteria",
        metavar="JSON|@FILE",
        help="Extra API criteria as JSON (or @file), merged with the options, e.g. "
        '\'{"project_start_date": {"from_date": "2020-01-01"}}\'',
    )
    filters.add_argument("--criteria-json", dest="criteria_json", help=argparse.SUPPRESS)
    filters.add_argument("--criteria-file", dest="criteria_file", help=argparse.SUPPRESS)

    dates = parser.add_argument_group("dates")
    dates.add_argument(
        "--date-field",
        choices=DATE_FIELDS,
        help="Which date the range applies to (default: award_notice; added with --since-last-run)",
    )
    when = dates.add_mutually_exclusive_group()
    when.add_argument("--days", type=_positive_int, metavar="N", help="The N days up to --date-to (default today)")
    when.add_argument("--date-from", type=_iso_date, metavar="DATE", help="Earliest date (YYYY-MM-DD)")
    dates.add_argument("--date-to", type=_iso_date, metavar="DATE", help="Latest date (YYYY-MM-DD)")

    fetching = parser.add_argument_group("results")
    fetching.add_argument(
        "--max-results",
        "--max-records",
        dest="max_results",
        type=_max_results_arg,
        metavar="N",
        help="Maximum projects to fetch, or 'all' (default: 1000)",
    )
    fetching.add_argument(
        "--sort",
        metavar="FIELD[:asc|desc]",
        help=f"Sort by {', '.join(SORT_FIELDS[:6])}, ... (default: appl_id)",
    )
    fetching.add_argument("--count", action="store_true", help="Print the number of matching projects and exit")

    inputs = parser.add_argument_group("input (instead of a search)")
    _list(
        inputs,
        "--appl-ids",
        dest="appl_ids",
        metavar="ID|FILE",
        help="Fetch these projects: application IDs, files listing them, or '-' for stdin",
    )
    _list(
        inputs,
        "--pmids",
        dest="pmids",
        metavar="ID|FILE",
        help="Fetch the projects these publications (PubMed IDs, files, '-') are linked to",
    )
    inputs.add_argument(
        "--from-raw", metavar="FILE", help="Re-read raw projects saved with --raw (or nihreporter-fetch 0.1 JSON)"
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "-o",
        "--output",
        dest="output_file",
        metavar="FILE",
        help="Output file, or '-' for stdout (default: projects.<format>); "
        "the extension (.json, .jsonl, .csv, .sqlite/.db) selects the format",
    )
    output.add_argument("--format", choices=OUTPUT_FORMATS, help="Output format (default: json)")
    _flag(
        output,
        "--publications",
        dest="publications",
        help="Add the PubMed IDs linked to each project (one more request per 100 projects)",
    )
    _flag(output, "--raw", dest="raw", help="Write the API's project objects unchanged (JSON/JSONL only)")
    _list(output, "--fields", dest="fields", metavar="FIELD", help="With --raw: only these API fields")
    _flag(output, "--merge", dest="merge", help="Update an existing output, replacing records with the same ID")
    _flag(output, "--resume", dest="resume", help="Append to an existing output, skipping IDs already in it")
    output.add_argument("--summary", metavar="FILE", help="Write the run summary (search_info) as JSON ('-' = stdout)")

    other = parser.add_argument_group("incremental runs and configuration")
    other.add_argument(
        "--since-last-run",
        metavar="STATE",
        help="Fetch projects added since the date stored in STATE and update it after a complete run "
        "(defaults --date-field to added)",
    )
    other.add_argument("--config", metavar="FILE", help="Configuration file (INI format)")
    other.add_argument("--create-config", metavar="FILENAME", help="Create a sample configuration file and exit")
    verbosity = other.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="Only print warnings and errors")
    verbosity.add_argument("-v", "--verbose", action="store_true", help="Print debug output")
    # nihreporter-fetch 0.1 chose the endpoint; projects is the only one now.
    parser.add_argument("--endpoint", choices=("projects", "publications"), help=argparse.SUPPRESS)
    return parser


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments. Unset options are None so config values can apply."""
    args = build_parser().parse_args(argv)
    given = [v for v in (args.criteria, args.criteria_json, args.criteria_file) if v]
    if len(given) > 1:
        build_parser().error("use only one of --criteria, --criteria-json and --criteria-file")
    if args.criteria_json:
        args.criteria = args.criteria_json
    elif args.criteria_file:
        args.criteria = "@" + args.criteria_file
    return args


def resolve_settings(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Merge settings with precedence: command line > config file > defaults."""
    cli = {key: getattr(args, key, None) for key in DEFAULTS}
    settings = dict(DEFAULTS)
    for layer in (config, cli):
        settings.update({k: v for k, v in layer.items() if v is not None})
    # --days on the command line replaces a date_from from the config file, and vice versa.
    if args.days is not None and args.date_from is None:
        settings["date_from"] = None
    if args.date_from is not None:
        settings["days"] = None
    return settings


def _apply_criteria(settings: dict[str, Any]) -> dict[str, Any]:
    """Load ``criteria``; a whole 0.1-style request body may also set the sort and raw fields."""
    if not settings["criteria"]:
        return {}
    criteria, body = load_criteria(settings["criteria"])
    if body.get("sort_field") and not settings["sort"]:
        settings["sort"] = body["sort_field"] + (f":{body['sort_order']}" if body.get("sort_order") else "")
    if body.get("include_fields") and not settings["fields"]:
        if settings["raw"]:
            settings["fields"] = body["include_fields"]
        else:
            log.warning("Ignoring include_fields: normalized records need every field (use --raw to limit fields).")
    if ignored := sorted(set(body) - {"sort_field", "sort_order", "include_fields", *IGNORED_BODY_KEYS}):
        log.warning("Ignoring request settings: %s", ", ".join(ignored))
    return criteria


def build_options(settings: dict[str, Any], quiet: bool = False) -> Options:
    settings = dict(settings)
    criteria = _apply_criteria(settings)
    spec_keys = ["text", "text_fields", "text_operator", *SEARCH_LIST_KEYS, "match_all_categories"]
    spec_keys += ["min_award", "max_award", "active", "multi_pi", "subprojects", "newly_added"]
    spec_keys += ["date_field", "date_from", "date_to"]
    spec = QuerySpec(**{key: settings[key] for key in spec_keys}, criteria=criteria)
    names = "days sort from_raw raw publications output_file format merge resume summary since_last_run".split()
    return Options(
        spec=spec,
        max_results=None if settings["max_results"] == "all" else settings["max_results"],
        appl_ids=read_ids(settings["appl_ids"], "application ID") if settings["appl_ids"] else None,
        pmids=read_ids(settings["pmids"], "PMID") if settings["pmids"] else None,
        fields=settings["fields"] or None,
        display=not quiet,
        **{name: settings[name] for name in names},
    )


SAMPLE_CONFIG = """[search]
# Lists are comma-separated and/or one item per line. Quote items containing commas:
#   pi_names = "Corey, Lawrence", "Gilbert, Peter"
# Values are ORed within a list and ANDed across settings. Names accept * wildcards.

# Text searched in titles, abstracts and terms (the positional argument)
#text = CAR T cell therapy
# Where to search (title, abstract, terms) and how words combine (and, or, advanced)
#text_fields = title, abstract
#text_operator = and

# People and organizations
#pi_names = "Corey, Lawrence"
#pi_profile_ids = 1860036
#po_names = Smith
org_names = FRED HUTCH*
#org_names_exact = FRED HUTCHINSON CANCER CENTER
#org_cities = SEATTLE
#org_states = WA
#org_countries = UNITED STATES
#project_nums = R01CA*

# Filters (case-insensitive)
fiscal_years = 2024-2026
#agencies = NCI, NIAID
#activity_codes = R01, U01
# 1 new, 2 competing renewal, 3 supplement, 4 extension, 5 non-competing continuation, ...
#application_types = 1, 2
#opportunity_numbers = PAR-21-123
#cong_dists = WA-07
# RCDC spending category IDs, and whether projects need all of them
#spending_categories = 31, 132
#match_all_categories = no
#covid_response = C4, C5, C6
#min_award = 1000000
#max_award = 5000000
# Currently active projects (instead of fiscal_years)
#active = no
#multi_pi = no
# include, exclude or only
#subprojects = include
#newly_added = no
# Any other API criteria, as JSON
#criteria = {"project_start_date": {"from_date": "2020-01-01"}}

# Date range: days before date_to (default today), or date_from..date_to (YYYY-MM-DD)
# date_field: award_notice, project_start, project_end, added
#date_field = award_notice
#days = 365
#date_from = 2024-01-01
#date_to = 2024-12-31

# Maximum number of projects, or "all"
max_results = all
#sort = award_amount:desc

# Fetch specific projects instead of searching (IDs and/or files listing them)
#appl_ids = 11124893, 11014863
# Or the projects that publications are linked to
#pmids = 33298401
# Or convert projects saved earlier with raw = yes (or nihreporter-fetch 0.1 JSON), without the network
#from_raw = raw_projects.jsonl

# Output file; the extension (.json, .jsonl, .csv, .sqlite) selects the format unless format is set
output_file = projects.json
#format = jsonl
# Add the PubMed IDs linked to each project
#publications = yes
# Write the API's project objects unchanged (JSON/JSONL only), optionally limited to some fields
#raw = no
#fields = ApplId, ProjectTitle, AwardAmount
#merge = no
#resume = no
#summary = run_summary.json

# Incremental runs: fetch projects added since the date stored in this file. Write to SQLite (or
# use merge) so re-fetched projects replace their old records.
#since_last_run = projects_state.json
"""


def create_sample_config(filename: str) -> bool:
    """Create a sample configuration file. Refuses to overwrite an existing file."""
    try:
        with open(filename, "x", encoding="utf-8") as f:
            f.write(SAMPLE_CONFIG)
    except OSError as e:
        log.error("Error creating config file: %s", e)
        return False
    log.info("Sample configuration file created: %s", filename)
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the command line. Returns a process exit code."""
    args = parse_arguments(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    if args.endpoint == "publications":
        log.error(
            "Error: --endpoint publications was replaced by --publications (add linked PubMed IDs to projects) "
            "and --pmids (find the projects publications are linked to)."
        )
        return EXIT_ERROR
    if args.create_config:
        return 0 if create_sample_config(args.create_config) else EXIT_ERROR

    config: dict[str, Any] = {}
    if args.config:
        if not os.path.exists(args.config):
            log.error("Error: Configuration file '%s' not found.", args.config)
            return EXIT_ERROR
        try:
            config = load_config(args.config)
        except (configparser.Error, ValueError) as e:
            log.error("Error reading configuration file '%s': %s", args.config, e)
            return EXIT_ERROR

    try:
        settings = resolve_settings(args, config)
        options = build_options(settings, quiet=args.quiet)
        if args.count:
            print(count_matches(options))
            return 0
        result = run(options)
    except (ValueError, ReporterError, OSError, sqlite3.Error) as e:
        log.error("Error: %s", e)
        return EXIT_ERROR

    info = result.search_info
    log.info("Done. Retrieved %d projects; %d in %s.", info["retrieved"], info["total_projects"], result.output)
    return result.exit_code


def main_cli() -> None:
    sys.exit(main())
