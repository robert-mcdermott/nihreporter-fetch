"""Output sinks (JSON, JSONL, CSV, SQLite), the human-readable listing and the run summary."""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

from ._meta import SCHEMA_VERSION, utc_now
from .parse import Project, parse_project, record_id

log = logging.getLogger(__name__)

OUTPUT_FORMATS = ("json", "jsonl", "csv", "sqlite")
# Formats that can hold raw API project objects (--raw).
RAW_FORMATS = ("json", "jsonl")
EXTENSIONS = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".csv": "csv",
    ".sqlite": "sqlite",
    ".db": "sqlite",
}
STDOUT = "-"

CSV_FIELDS = [
    "appl_id",
    "project_num",
    "core_project_num",
    "subproject_id",
    "fiscal_year",
    "project_title",
    "application_type",
    "activity_code",
    "funding_mechanism",
    "agency_code",
    "admin_ic",
    "admin_ic_name",
    "funding_ics",
    "award_amount",
    "direct_cost",
    "indirect_cost",
    "award_notice_date",
    "budget_start",
    "budget_end",
    "project_start_date",
    "project_end_date",
    "date_added",
    "is_active",
    "is_new",
    "arra_funded",
    "covid_response",
    "opportunity_number",
    "cfda_code",
    "contact_pi_name",
    "principal_investigators",
    "pi_profile_ids",
    "program_officers",
    "org_name",
    "org_city",
    "org_state",
    "org_country",
    "org_zipcode",
    "org_type",
    "org_dept_type",
    "org_uei",
    "cong_dist",
    "latitude",
    "longitude",
    "study_section_code",
    "study_section",
    "spending_categories",
    "publication_count",
    "publications",
    "url",
    "abstract_text",
]


def resolve_output(filename: str | None = None, fmt: str | None = None) -> tuple[str, str]:
    """Return (filename, format). The format comes from ``fmt``, else the file extension, else JSON."""
    ext_fmt = EXTENSIONS.get(Path(filename).suffix.lower()) if filename and filename != STDOUT else None
    fmt = fmt or ext_fmt or "json"
    if fmt not in OUTPUT_FORMATS:
        raise ValueError(f"Unknown output format: {fmt}")
    return filename or f"projects.{fmt}", fmt


def _join(values: Iterable[Any]) -> str:
    return "; ".join(str(v) for v in values if v not in (None, ""))


def _pi_names(project: Project) -> str:
    return _join(
        p["full_name"] + (" (contact)" if p["is_contact_pi"] and len(project["principal_investigators"]) > 1 else "")
        for p in project["principal_investigators"]
        if p["full_name"]
    )


def _count(values: list[Any] | None) -> int | None:
    return None if values is None else len(values)


def csv_row(project: Project) -> dict[str, Any]:
    admin = project.get("admin_ic") or {}
    org = project.get("organization") or {}
    section = project.get("study_section") or {}
    row = {key: project.get(key) for key in CSV_FIELDS}
    row.update(
        admin_ic=admin.get("abbreviation"),
        admin_ic_name=admin.get("name"),
        funding_ics=_join(dict.fromkeys(f["abbreviation"] for f in project["ic_fundings"])),
        covid_response=_join(project["covid_response"]),
        principal_investigators=_pi_names(project),
        pi_profile_ids=_join(p["profile_id"] for p in project["principal_investigators"]),
        program_officers=_join(p["full_name"] for p in project["program_officers"]),
        org_name=org.get("name"),
        org_city=org.get("city"),
        org_state=org.get("state"),
        org_country=org.get("country"),
        org_zipcode=org.get("zipcode"),
        org_type=org.get("type"),
        org_dept_type=org.get("dept_type"),
        org_uei=org.get("uei"),
        cong_dist=org.get("cong_dist"),
        latitude=org.get("latitude"),
        longitude=org.get("longitude"),
        study_section_code=section.get("code"),
        study_section=section.get("name"),
        spending_categories=_join(c["name"] or c["id"] for c in project["spending_categories"]),
        publication_count=_count(project["publications"]),
        publications=_join(project["publications"] or []),
    )
    return {k: "" if v is None else v for k, v in row.items()}


def _atomic_write(path: str, write) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        write(f)
    os.replace(tmp, path)


class Sink:
    """
    Receives batches of records as they are fetched. ``existing_ids`` lists records already in
    the output that ``resume`` mode skips. ``close`` finalizes the output and returns the
    number of records it contains.
    """

    streaming = False

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        self.path = path
        self.merge = merge
        self.resume = resume
        self.existing_ids: set[int] = set()

    def write(self, batch: list[Project]) -> None:
        raise NotImplementedError

    def close(self, search_info: dict[str, Any]) -> int:
        raise NotImplementedError


class JsonSink(Sink):
    """A single JSON document. Held in memory and written atomically on close."""

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        super().__init__(path, merge, resume)
        self.projects: dict[int, Project] = {}
        if (merge or resume) and path != STDOUT and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict) or "projects" not in doc:
                raise ValueError(
                    f"{path} isn't nihreporter-fetch JSON output (no 'projects' list); can't merge or resume."
                )
            self.projects = {record_id(p): p for p in doc["projects"]}
            if resume:
                self.existing_ids = set(self.projects)

    def write(self, batch: list[Project]) -> None:
        self.projects.update((record_id(p), p) for p in batch)

    def close(self, search_info: dict[str, Any]) -> int:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "search_info": {**search_info, "total_projects": len(self.projects)},
            "projects": list(self.projects.values()),
        }
        if self.path == STDOUT:
            json.dump(doc, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            _atomic_write(self.path, lambda f: json.dump(doc, f, indent=2, ensure_ascii=False))
        return len(self.projects)


class _LineSink(Sink):
    """
    Base for line-oriented formats (JSONL, CSV). Batches are written and flushed as they arrive;
    ``resume`` appends to an existing file. ``merge`` rewrites the file at close, replacing
    existing records with the same application ID.
    """

    streaming = True

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        super().__init__(path, merge, resume)
        self._file: IO[str] | None = None
        self._count = 0
        self._previous: dict[int, Any] = {}
        exists = path != STDOUT and os.path.exists(path) and os.path.getsize(path) > 0
        if exists and resume:
            self._drop_partial_line()
        if exists and (merge or resume):
            self._previous = self._read_existing()
            if resume:
                self.existing_ids = set(self._previous)
                self._count = len(self._previous)
        self._append = resume and exists

    def _drop_partial_line(self) -> None:
        """Remove a trailing partial line left by an interrupted run, so appended records stay valid."""
        with open(self.path, "rb+") as f:
            data = f.read()
            end = data.rfind(b"\n") + 1
            if end < len(data):
                log.warning("Removing an incomplete last line from %s (likely from an interrupted run)", self.path)
                f.truncate(end)

    def _read_existing(self) -> dict[int, Any]:
        raise NotImplementedError

    def _write_header(self, f: IO[str]) -> None:
        return None

    def _write_records(self, f: IO[str], batch: list[Project]) -> None:
        raise NotImplementedError

    def _write_previous(self, f: IO[str], previous: Iterable[Any]) -> None:
        raise NotImplementedError

    def _open(self) -> IO[str]:
        if self._file is None:
            if self.path == STDOUT:
                self._file = sys.stdout
            else:
                target = f"{self.path}.tmp" if self.merge else self.path
                self._file = open(target, "a" if self._append else "w", encoding="utf-8", newline="")
            if not self._append:
                self._write_header(self._file)
        return self._file

    def write(self, batch: list[Project]) -> None:
        f = self._open()
        self._write_records(f, batch)
        f.flush()
        self._count += len(batch)
        if self.merge:
            for project in batch:
                self._previous.pop(record_id(project), None)

    def close(self, search_info: dict[str, Any]) -> int:
        f = self._open()
        if self.merge:
            self._write_previous(f, self._previous.values())
            self._count += len(self._previous)
        if f is not sys.stdout:
            f.close()
            if self.merge:
                os.replace(f"{self.path}.tmp", self.path)
        return self._count


class JsonlSink(_LineSink):
    """One record per line."""

    def _read_existing(self) -> dict[int, Any]:
        records = {}
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Ignoring an incomplete line in %s (likely from an interrupted run)", self.path)
                    continue
                records[record_id(record)] = record
        return records

    def _write_records(self, f: IO[str], batch: list[Project]) -> None:
        f.writelines(json.dumps(p, ensure_ascii=False) + "\n" for p in batch)

    def _write_previous(self, f: IO[str], previous: Iterable[Any]) -> None:
        self._write_records(f, list(previous))


class CsvSink(_LineSink):
    """One flattened row per project; list fields are joined with "; "."""

    def _read_existing(self) -> dict[int, Any]:
        with open(self.path, encoding="utf-8", newline="") as f:
            return {int(row["appl_id"]): row for row in csv.DictReader(f) if (row.get("appl_id") or "").isdigit()}

    def _writer(self, f: IO[str]) -> csv.DictWriter:
        return csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")

    def _write_header(self, f: IO[str]) -> None:
        self._writer(f).writeheader()

    def _write_records(self, f: IO[str], batch: list[Project]) -> None:
        self._writer(f).writerows(map(csv_row, batch))

    def _write_previous(self, f: IO[str], previous: Iterable[Any]) -> None:
        self._writer(f).writerows({k: row.get(k, "") for k in CSV_FIELDS} for row in previous)


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    appl_id INTEGER PRIMARY KEY, project_num TEXT, core_project_num TEXT, subproject_id TEXT, fiscal_year INTEGER,
    project_title TEXT, application_type TEXT, activity_code TEXT, ic_code TEXT, serial_num TEXT, support_year TEXT,
    suffix_code TEXT, funding_mechanism TEXT, mechanism_code_dc TEXT, cfda_code TEXT, opportunity_number TEXT,
    agency_code TEXT, admin_ic TEXT, admin_ic_name TEXT,
    award_amount INTEGER, direct_cost INTEGER, indirect_cost INTEGER,
    award_notice_date TEXT, budget_start TEXT, budget_end TEXT, project_start_date TEXT, project_end_date TEXT,
    date_added TEXT, is_active INTEGER, is_new INTEGER, arra_funded INTEGER, covid_response TEXT, contact_pi_name TEXT,
    org_name TEXT, org_city TEXT, org_state TEXT, org_country TEXT, org_zipcode TEXT, org_type TEXT, org_dept_type TEXT,
    org_uei TEXT, org_duns TEXT, org_ipf_code TEXT, cong_dist TEXT, latitude REAL, longitude REAL,
    study_section_code TEXT, study_section TEXT, abstract_text TEXT, phr_text TEXT, publication_count INTEGER, url TEXT,
    record_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS principal_investigators (
    appl_id INTEGER NOT NULL, profile_id INTEGER, full_name TEXT, first_name TEXT, middle_name TEXT, last_name TEXT,
    title TEXT, is_contact_pi INTEGER
);
CREATE TABLE IF NOT EXISTS program_officers (
    appl_id INTEGER NOT NULL, full_name TEXT, first_name TEXT, middle_name TEXT, last_name TEXT
);
CREATE TABLE IF NOT EXISTS ic_fundings (
    appl_id INTEGER NOT NULL, fiscal_year INTEGER, code TEXT, abbreviation TEXT, name TEXT,
    total_cost INTEGER, direct_cost INTEGER, indirect_cost INTEGER
);
CREATE TABLE IF NOT EXISTS spending_categories (appl_id INTEGER NOT NULL, category_id INTEGER, name TEXT);
CREATE TABLE IF NOT EXISTS terms (appl_id INTEGER NOT NULL, term TEXT);
CREATE TABLE IF NOT EXISTS publications (
    core_project_num TEXT NOT NULL, pmid INTEGER NOT NULL, PRIMARY KEY (core_project_num, pmid)
);
CREATE TABLE IF NOT EXISTS runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, finished_at TEXT, search_info TEXT);
CREATE INDEX IF NOT EXISTS idx_projects_core_project_num ON projects(core_project_num);
CREATE INDEX IF NOT EXISTS idx_projects_fiscal_year ON projects(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_projects_org_name ON projects(org_name);
CREATE INDEX IF NOT EXISTS idx_principal_investigators_appl_id ON principal_investigators(appl_id);
CREATE INDEX IF NOT EXISTS idx_principal_investigators_profile_id ON principal_investigators(profile_id);
CREATE INDEX IF NOT EXISTS idx_program_officers_appl_id ON program_officers(appl_id);
CREATE INDEX IF NOT EXISTS idx_ic_fundings_appl_id ON ic_fundings(appl_id);
CREATE INDEX IF NOT EXISTS idx_spending_categories_appl_id ON spending_categories(appl_id);
CREATE INDEX IF NOT EXISTS idx_spending_categories_name ON spending_categories(name);
CREATE INDEX IF NOT EXISTS idx_terms_appl_id ON terms(appl_id);
CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term);
CREATE INDEX IF NOT EXISTS idx_publications_pmid ON publications(pmid);
"""
SQLITE_CHILD_TABLES = ("principal_investigators", "program_officers", "ic_fundings", "spending_categories", "terms")
SQLITE_PROJECT_COLUMNS = [
    "appl_id",
    "project_num",
    "core_project_num",
    "subproject_id",
    "fiscal_year",
    "project_title",
    "application_type",
    "activity_code",
    "ic_code",
    "serial_num",
    "support_year",
    "suffix_code",
    "funding_mechanism",
    "mechanism_code_dc",
    "cfda_code",
    "opportunity_number",
    "agency_code",
    "award_amount",
    "direct_cost",
    "indirect_cost",
    "award_notice_date",
    "budget_start",
    "budget_end",
    "project_start_date",
    "project_end_date",
    "date_added",
    "is_active",
    "is_new",
    "arra_funded",
    "contact_pi_name",
    "abstract_text",
    "phr_text",
    "url",
]
# Columns taken from nested parts of the record: column -> (record key, nested key).
SQLITE_NESTED_COLUMNS = {
    "admin_ic": ("admin_ic", "abbreviation"),
    "admin_ic_name": ("admin_ic", "name"),
    "org_name": ("organization", "name"),
    "org_city": ("organization", "city"),
    "org_state": ("organization", "state"),
    "org_country": ("organization", "country"),
    "org_zipcode": ("organization", "zipcode"),
    "org_type": ("organization", "type"),
    "org_dept_type": ("organization", "dept_type"),
    "org_uei": ("organization", "uei"),
    "org_duns": ("organization", "duns"),
    "org_ipf_code": ("organization", "ipf_code"),
    "cong_dist": ("organization", "cong_dist"),
    "latitude": ("organization", "latitude"),
    "longitude": ("organization", "longitude"),
    "study_section_code": ("study_section", "code"),
    "study_section": ("study_section", "name"),
}


def _child_rows(project: Project) -> dict[str, list[tuple]]:
    """Rows for each child table, without the leading appl_id."""
    return {
        "principal_investigators": [
            (p["profile_id"], p["full_name"], p["first_name"], p["middle_name"], p["last_name"])
            + (p["title"], p["is_contact_pi"])
            for p in project["principal_investigators"]
        ],
        "program_officers": [
            (p["full_name"], p["first_name"], p["middle_name"], p["last_name"]) for p in project["program_officers"]
        ],
        "ic_fundings": [
            (f["fiscal_year"], f["code"], f["abbreviation"], f["name"], f["total_cost"], f["direct_cost"])
            + (f["indirect_cost"],)
            for f in project["ic_fundings"]
        ],
        "spending_categories": [(c["id"], c["name"]) for c in project["spending_categories"]],
        "terms": [(t,) for t in project["pref_terms"]],
    }


class SqliteSink(Sink):
    """
    A normalized SQLite database. Records are always upserted by application ID, so repeated
    runs accumulate into the same database. Each batch is committed as it arrives.
    """

    streaming = True

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        super().__init__(path, merge, resume)
        self.db = sqlite3.connect(path)
        self.db.executescript(SQLITE_SCHEMA)
        if resume:
            self.existing_ids = {row[0] for row in self.db.execute("SELECT appl_id FROM projects")}

    def write(self, batch: list[Project]) -> None:
        now = utc_now()
        # A record repeated within the batch would otherwise get its child rows twice; the last one wins.
        batch = list({p["appl_id"]: p for p in batch}.values())
        ids = [(p["appl_id"],) for p in batch]
        columns = [*SQLITE_PROJECT_COLUMNS, *SQLITE_NESTED_COLUMNS, "covid_response", "publication_count"]
        columns += ["record_json", "updated_at"]
        with self.db:
            for table in SQLITE_CHILD_TABLES:
                self.db.executemany(f"DELETE FROM {table} WHERE appl_id = ?", ids)
            self.db.executemany(
                f"INSERT OR REPLACE INTO projects ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                [self._project_row(p, now) for p in batch],
            )
            rows: dict[str, list[tuple]] = {table: [] for table in SQLITE_CHILD_TABLES}
            for project in batch:
                for table, table_rows in _child_rows(project).items():
                    rows[table].extend((project["appl_id"], *row) for row in table_rows)
            for table, table_rows in rows.items():
                if table_rows:
                    placeholders = ", ".join("?" * len(table_rows[0]))
                    self.db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", table_rows)
            # Publications belong to core projects. Records fetched without --publications leave them alone.
            linked = {p["core_project_num"]: p["publications"] for p in batch if p["publications"] is not None}
            linked.pop(None, None)
            self.db.executemany("DELETE FROM publications WHERE core_project_num = ?", [(c,) for c in linked])
            self.db.executemany(
                "INSERT OR IGNORE INTO publications VALUES (?, ?)",
                [(core, pmid) for core, pmids in linked.items() for pmid in pmids],
            )

    @staticmethod
    def _project_row(project: Project, now: str) -> tuple:
        nested = [(project.get(key) or {}).get(sub) for key, sub in SQLITE_NESTED_COLUMNS.values()]
        return (
            *(project.get(c) for c in SQLITE_PROJECT_COLUMNS),
            *nested,
            _join(project["covid_response"]) or None,
            _count(project["publications"]),
            json.dumps(project, ensure_ascii=False),
            now,
        )

    def close(self, search_info: dict[str, Any]) -> int:
        with self.db:
            total = self.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            self.db.execute(
                "INSERT INTO runs (finished_at, search_info) VALUES (?, ?)",
                (utc_now(), json.dumps({**search_info, "total_projects": total}, ensure_ascii=False)),
            )
        self.db.close()
        return total


SINKS: dict[str, type[Sink]] = {"json": JsonSink, "jsonl": JsonlSink, "csv": CsvSink, "sqlite": SqliteSink}


def open_sink(path: str, fmt: str, merge: bool = False, resume: bool = False) -> Sink:
    if path == STDOUT and (fmt == "sqlite" or merge or resume):
        raise ValueError("Writing to stdout ('-') can't be combined with sqlite, --merge or --resume.")
    if merge and resume:
        raise ValueError("--merge and --resume can't be used together.")
    return SINKS[fmt](path, merge=merge, resume=resume)


def save_results(projects: list[Project], search_info: dict[str, Any], filename=None, fmt=None, merge=False) -> int:
    """Write a complete list of records in one go. Returns the number of records in the output."""
    path, fmt = resolve_output(filename, fmt)
    sink = open_sink(path, fmt, merge=merge)
    sink.write(projects)
    return sink.close(search_info)


def write_summary(path: str, summary: dict[str, Any]) -> None:
    """Write the run summary (search_info) as JSON; ``-`` means stdout."""
    if path == STDOUT:
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _atomic_write(path, lambda f: json.dump(summary, f, indent=2, ensure_ascii=False))


def _clip(values: list[str], n: int = 4) -> str:
    return ", ".join(values[:n]) + (f" (+{len(values) - n} more)" if len(values) > n else "")


def _money(amount: Any) -> str | None:
    return f"${amount:,.0f}" if isinstance(amount, (int, float)) else None


def print_projects(projects: list[Project], start: int = 1, out: IO[str] | None = None) -> None:
    """Print a human-readable listing of records (normalized or raw)."""
    out = out or sys.stdout
    for i, project in enumerate(projects, start):
        if "admin_ic" not in project:
            try:
                project = parse_project(project)
            except (KeyError, TypeError, AttributeError, ValueError):
                print(f"\n{i}. Application {record_id(project)}\n" + "-" * 40, file=out)
                continue
        admin = (project.get("admin_ic") or {}).get("abbreviation")
        fy = f"FY{project['fiscal_year']}" if project.get("fiscal_year") else None
        summary = [project["project_num"], fy, admin, project["activity_code"], _money(project["award_amount"])]
        lines = [f"\n{i}. {project['project_title'] or project['project_num'] or project['appl_id']}"]
        lines.append("   " + " | ".join(str(s) for s in summary if s))
        pis = [p["full_name"] for p in project["principal_investigators"] if p["full_name"]]
        if pis:
            lines.append(f"   PI{'s' if len(pis) > 1 else ''}: {_clip(pis)}")
        if org := project.get("organization"):
            place = ", ".join(p for p in (org["city"], org["state"] or org["country"]) if p)
            lines.append(f"   Organization: {org['name']}" + (f" ({place})" if place else ""))
        dates = [
            f"{label} {project[key]}"
            for label, key in (
                ("start", "project_start_date"),
                ("end", "project_end_date"),
                ("notice", "award_notice_date"),
            )
            if project.get(key)
        ]
        if dates:
            lines.append(f"   Dates: {', '.join(dates)}")
        if project.get("subproject_id"):
            lines.append(f"   Subproject {project['subproject_id']} of {project['core_project_num']}")
        if project["publications"] is not None:
            lines.append(f"   Publications: {len(project['publications'])}")
        lines.append(f"   URL: {project['url']}")
        lines.append("-" * 40)
        print("\n".join(lines), file=out)
