"""
Search NIH RePORTER and export structured records of NIH-funded research projects.

Library use::

    from nihreporter_fetch import Options, QuerySpec, run

    result = run(Options(spec=QuerySpec(org_names=["FRED HUTCH*"], fiscal_years=[2025]), output_file="fh.sqlite"))

or, for records in memory::

    from nihreporter_fetch import ReporterClient

    projects = ReporterClient().search_projects(pi_names=["Corey, Lawrence"], fiscal_years=[2024, 2025])
"""

import logging

from ._meta import SCHEMA_VERSION, ReporterError, __version__
from .client import (
    PAGE_SIZE_LIMIT,
    SEARCH_LIMIT,
    ReporterClient,
    load_raw_projects,
    make_session,
    normalize_ids,
    normalize_pmids,
)
from .outputs import open_sink, print_projects, resolve_output, save_results
from .parse import Project, parse_project, record_id
from .pipeline import Options, RunResult, count_matches, resolve_search, run
from .query import QuerySpec, resolve_date_range

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "PAGE_SIZE_LIMIT",
    "SCHEMA_VERSION",
    "SEARCH_LIMIT",
    "Options",
    "Project",
    "QuerySpec",
    "ReporterClient",
    "ReporterError",
    "RunResult",
    "__version__",
    "count_matches",
    "load_raw_projects",
    "make_session",
    "normalize_ids",
    "normalize_pmids",
    "open_sink",
    "parse_project",
    "print_projects",
    "record_id",
    "resolve_date_range",
    "resolve_output",
    "resolve_search",
    "run",
    "save_results",
]
