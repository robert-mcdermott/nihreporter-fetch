"""Build NIH RePORTER project search criteria from a QuerySpec."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from ._meta import ReporterError

# Where text searches look: option value -> the API's search_field name.
TEXT_FIELDS = {"title": "projecttitle", "abstract": "abstracttext", "terms": "terms"}
TEXT_OPERATORS = ("and", "or", "advanced")

# Administering institutes, centers and offices (agency_ic_admin abbreviations), grouped by agency.
NIH_ICS = (
    "CLC",
    "FIC",
    "NCATS",
    "NCCIH",
    "NCI",
    "NEI",
    "NHGRI",
    "NHLBI",
    "NIA",
    "NIAAA",
    "NIAID",
    "NIAMS",
    "NIBIB",
    "NICHD",
    "NIDA",
    "NIDCD",
    "NIDCR",
    "NIDDK",
    "NIEHS",
    "NIGMS",
    "NIMH",
    "NIMHD",
    "NINDS",
    "NINR",
    "NLM",
    "OD",
    # Former centers, for older projects
    "CSR",
    "NCRR",
)
CDC_CENTERS = (
    "ATSDR",
    "CGH",
    "NCBDDD",
    "NCCDPHP",
    "NCEH",
    "NCEZID",
    "NCHHSTP",
    "NCHS",
    "NCIPC",
    "NCIRD",
    "NIOSH",
    # Former centers and offices
    "CID",
    "NCHM",
    "ODCDC",
    "PHPPO",
)
HRSA_DIVISIONS = ("DADHP", "DDA", "DM,BHP", "DN")
OTHER_AGENCIES = ("AHRQ", "FDA", "VA", "SAMHSA", "OMHHE")
ADMIN_AGENCIES = (*NIH_ICS, *CDC_CENTERS, *HRSA_DIVISIONS, *OTHER_AGENCIES)
# Agencies that the agencies filter also accepts as a whole, and their institutes, centers or divisions.
AGENCY_GROUPS = {"NIH": NIH_ICS, "ALLCDC": CDC_CENTERS, "HRSA": HRSA_DIVISIONS}
# Values the agencies filter accepts.
AGENCIES = (*ADMIN_AGENCIES, *AGENCY_GROUPS)
# Agencies small enough to fetch whole within a fiscal year. The client splits searches over the
# 15,000-record limit by institute, adding these to catch centers whose codes RePORTER doesn't publish.
# Together they cover every project of recent years; some older projects carry codes nothing matches
# (the client reports them).
CATCH_ALL_AGENCIES = ("ALLCDC", "HRSA")

# Application types (the first digit of a project number).
APPLICATION_TYPES = {
    "1": "new",
    "2": "competing renewal",
    "3": "supplement",
    "4": "extension",
    "5": "non-competing continuation",
    "6": "change of organization",
    "7": "change of grantee or training institution",
    "8": "change of institute or center (non-competing)",
    "9": "change of institute or center (competing)",
}
# COVID-19 funding sources: regular appropriations, and the four supplemental appropriations acts.
COVID_RESPONSE = ("Reg-CV", "CV", "C3", "C4", "C5", "C6")
SUBPROJECTS = ("include", "exclude", "only")

# Date filters: option value -> API criterion (and record field).
DATE_FIELDS = {
    "award_notice": "award_notice_date",
    "project_start": "project_start_date",
    "project_end": "project_end_date",
    "added": "date_added",
}

# Fields the API can sort on. Others fail with a server error, so they are refused up front.
SORT_FIELDS = (
    "appl_id",
    "fiscal_year",
    "award_amount",
    "direct_cost_amt",
    "indirect_cost_amt",
    "award_notice_date",
    "date_added",
    "project_start_date",
    "project_end_date",
    "budget_start",
    "budget_end",
    "core_project_num",
    "activity_code",
    "agency_code",
    "org_name",
    "contact_pi_name",
    "cong_dist",
    "funding_mechanism",
)

# Every criterion the projects search accepts. The API silently ignores unknown ones (a typo
# would match all 3 million projects), so extra criteria are checked against this list.
CRITERIA_KEYS = frozenset(
    {
        "use_relevance",
        "fiscal_years",
        "include_active_projects",
        "pi_names",
        "po_names",
        "org_names",
        "org_names_exact_match",
        "pi_profile_ids",
        "org_cities",
        "org_states",
        "project_nums",
        "org_countries",
        "appl_ids",
        "project_num_split",
        "agencies",
        "is_agency_admin",
        "is_agency_funding",
        "activity_codes",
        "cooperative_agreement_codes",
        "award_types",
        "dept_types",
        "cong_dists",
        "foa",
        "opportunity_numbers",
        "spending_categories",
        "project_start_date",
        "project_end_date",
        "date_added",
        "organization_type",
        "full_study_sections",
        "award_notice_date",
        "award_amount_range",
        "exclude_subprojects",
        "multi_pi_only",
        "newly_added_projects_only",
        "sub_project_only",
        "funding_mechanism",
        "covid_response",
        "outcomes_only",
        "arra_type",
        "advanced_text_search",
        "publications_search",
    }
)

# The largest award amount the API accepts in a range (a 32-bit integer; larger values are a server
# error). It is also sent with a minimum-only range, which the API would otherwise ignore.
MAX_AWARD = 2**31 - 1
FIRST_FISCAL_YEAR = 1985
_YEARS = re.compile(r"^\s*(\d{4})\s*(?:(?:-|\.\.)\s*(\d{4})\s*)?$")
_ACTIVITY_CODE = re.compile(r"^[A-Z0-9]{3}$")


def normalize_enum(
    values: Iterable[str], allowed: Iterable[str], name: str, aliases: dict[str, str] | None = None
) -> list[str]:
    """Match values case-insensitively against ``allowed`` (returning its spelling). Raises ValueError."""
    canonical = {a.upper(): a for a in allowed}
    result = []
    for value in values:
        key = value.strip().upper()
        key = (aliases or {}).get(key, key)
        if key not in canonical:
            raise ValueError(f"Unknown {name} {value!r}; use one of {', '.join(canonical.values())}")
        result.append(canonical[key])
    return list(dict.fromkeys(result))


def parse_fiscal_years(values: Iterable[int | str]) -> list[int]:
    """Fiscal years from numbers and ranges such as ``2020-2024``, sorted and de-duplicated."""
    years: set[int] = set()
    for value in values:
        if isinstance(value, int):
            first = last = value
        else:
            match = _YEARS.match(str(value))
            if not match:
                raise ValueError(f"Expected a fiscal year (2024) or a range (2020-2024), got {value!r}")
            first = int(match[1])
            last = int(match[2] or match[1])
        if not FIRST_FISCAL_YEAR <= first <= last <= date.today().year + 2:
            raise ValueError(f"Fiscal years must be ascending and between {FIRST_FISCAL_YEAR} and next year: {value!r}")
        years.update(range(first, last + 1))
    return sorted(years)


def parse_name(value: str) -> dict[str, str]:
    """
    A person-name criterion. ``"Last, First"`` matches last and first name (any middle name or
    initial after the first name is ignored); anything else matches any part of a name. ``*`` is
    a wildcard.
    """
    value = " ".join(value.split())
    if "," not in value:
        return {"any_name": value}
    last, _, first = (part.strip() for part in value.partition(","))
    name = {"last_name": last}
    if first:
        name["first_name"] = first.split()[0]
    return name


def parse_sort(value: str) -> tuple[str, str]:
    """``FIELD[:asc|desc]`` -> (field, order). Raises ValueError for fields the API can't sort on."""
    name, _, order = value.strip().partition(":")
    name, order = name.strip().lower(), (order.strip().lower() or "asc")
    if name not in SORT_FIELDS:
        raise ValueError(f"Can't sort on {name!r}; use one of {', '.join(SORT_FIELDS)}")
    if order not in ("asc", "desc"):
        raise ValueError(f"Sort order must be asc or desc, got {order!r}")
    return name, order


def _upper(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v.strip().upper() for v in values if v.strip()))


def _strip(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


@dataclass
class QuerySpec:
    """
    A RePORTER projects search. Values within one list are ORed; different fields are ANDed.
    Names (``pi_names``, ``po_names``, ``org_names``) and ``project_nums`` accept ``*`` wildcards.
    ``criteria`` adds raw API criteria (snake_case) for anything without a field of its own.
    """

    text: str | None = None
    text_fields: list[str] = field(default_factory=lambda: list(TEXT_FIELDS))
    text_operator: str = "and"
    fiscal_years: list[int] = field(default_factory=list)
    pi_names: list[str] = field(default_factory=list)
    pi_profile_ids: list[int] = field(default_factory=list)
    po_names: list[str] = field(default_factory=list)
    org_names: list[str] = field(default_factory=list)
    org_names_exact: list[str] = field(default_factory=list)
    org_cities: list[str] = field(default_factory=list)
    org_states: list[str] = field(default_factory=list)
    org_countries: list[str] = field(default_factory=list)
    project_nums: list[str] = field(default_factory=list)
    agencies: list[str] = field(default_factory=list)
    activity_codes: list[str] = field(default_factory=list)
    application_types: list[str] = field(default_factory=list)
    opportunity_numbers: list[str] = field(default_factory=list)
    cong_dists: list[str] = field(default_factory=list)
    spending_categories: list[int] = field(default_factory=list)
    match_all_categories: bool = False
    covid_response: list[str] = field(default_factory=list)
    min_award: int | None = None
    max_award: int | None = None
    active: bool = False
    multi_pi: bool = False
    subprojects: str = "include"
    newly_added: bool = False
    date_field: str | None = None  # key of DATE_FIELDS; default "award_notice"
    date_from: date | None = None
    date_to: date | None = None
    criteria: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = self.text.strip() if self.text and self.text.strip() else None
        self.text_fields = normalize_enum(self.text_fields or list(TEXT_FIELDS), TEXT_FIELDS, "text field")
        self.text_operator = normalize_enum([self.text_operator], TEXT_OPERATORS, "text operator")[0]
        self.fiscal_years = parse_fiscal_years(self.fiscal_years)
        self.pi_names = _strip(self.pi_names)
        self.pi_profile_ids = list(dict.fromkeys(int(i) for i in self.pi_profile_ids))
        self.po_names = _strip(self.po_names)
        self.org_names = _strip(self.org_names)
        self.org_names_exact = _strip(self.org_names_exact)
        self.org_cities = _upper(self.org_cities)
        # The API matches states case-sensitively ("wa" finds nothing).
        self.org_states = _upper(self.org_states)
        self.org_countries = _upper(self.org_countries)
        self.project_nums = _upper(self.project_nums)
        self.agencies = normalize_enum(self.agencies, AGENCIES, "agency")
        self.activity_codes = _upper(self.activity_codes)
        if bad := [c for c in self.activity_codes if not _ACTIVITY_CODE.match(c)]:
            raise ValueError(f"Activity codes are 3 letters or digits (R01, K99, UG3), got {', '.join(bad)}")
        self.application_types = normalize_enum(
            [str(t) for t in self.application_types], APPLICATION_TYPES, "application type"
        )
        self.opportunity_numbers = _upper(self.opportunity_numbers)
        self.cong_dists = _upper(self.cong_dists)
        self.spending_categories = list(dict.fromkeys(int(c) for c in self.spending_categories))
        self.covid_response = normalize_enum(self.covid_response, COVID_RESPONSE, "COVID response code")
        for name in ("min_award", "max_award"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= MAX_AWARD:
                raise ValueError(f"{name} must be between 0 and {MAX_AWARD:,}, got {value}")
        if self.min_award is not None and self.max_award is not None and self.min_award > self.max_award:
            raise ValueError(f"min_award ({self.min_award}) is greater than max_award ({self.max_award}).")
        self.subprojects = normalize_enum([self.subprojects], SUBPROJECTS, "subprojects setting")[0]
        if self.active and self.fiscal_years:
            # The API returns the union of the two, which is rarely what's wanted.
            raise ValueError(
                "active can't be combined with fiscal_years: RePORTER would return projects in those "
                "fiscal years plus every active project. Use one or the other."
            )
        if self.date_field is not None and self.date_field not in DATE_FIELDS:
            raise ValueError(f"date_field must be one of {', '.join(DATE_FIELDS)}, got {self.date_field!r}")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError(f"Start of the date range ({self.date_from}) is after its end ({self.date_to}).")
        self.criteria = dict(self.criteria or {})
        if unknown := sorted(set(self.criteria) - CRITERIA_KEYS):
            raise ValueError(
                f"Unknown criteria: {', '.join(unknown)}. RePORTER ignores unknown criteria, which would match every "
                "project; see https://api.reporter.nih.gov for valid names (snake_case)."
            )

    def _built(self) -> dict[str, Any]:
        c: dict[str, Any] = {}
        if self.text:
            c["advanced_text_search"] = {
                "operator": self.text_operator,
                "search_field": ",".join(TEXT_FIELDS[f] for f in self.text_fields),
                "search_text": self.text,
            }
        if self.fiscal_years:
            c["fiscal_years"] = self.fiscal_years
        if self.active:
            c["include_active_projects"] = True
        if self.pi_names:
            c["pi_names"] = [parse_name(n) for n in self.pi_names]
        if self.po_names:
            c["po_names"] = [parse_name(n) for n in self.po_names]
        lists = {
            "pi_profile_ids": self.pi_profile_ids,
            "org_names": self.org_names,
            "org_names_exact_match": self.org_names_exact,
            "org_cities": self.org_cities,
            "org_states": self.org_states,
            "org_countries": self.org_countries,
            "project_nums": self.project_nums,
            "agencies": self.agencies,
            "activity_codes": self.activity_codes,
            "award_types": self.application_types,
            "opportunity_numbers": self.opportunity_numbers,
            "cong_dists": self.cong_dists,
            "covid_response": self.covid_response,
        }
        c.update((key, values) for key, values in lists.items() if values)
        if self.spending_categories:
            c["spending_categories"] = {"values": self.spending_categories, "match_all": self.match_all_categories}
        if self.min_award is not None or self.max_award is not None:
            c["award_amount_range"] = {
                "min_amount": self.min_award or 0,
                "max_amount": MAX_AWARD if self.max_award is None else self.max_award,
            }
        if self.multi_pi:
            c["multi_pi_only"] = True
        if self.subprojects == "exclude":
            c["exclude_subprojects"] = True
        elif self.subprojects == "only":
            c["sub_project_only"] = True
        if self.newly_added:
            c["newly_added_projects_only"] = True
        if self.date_from or self.date_to:
            dates = {}
            if self.date_from:
                dates["from_date"] = self.date_from.isoformat()
            if self.date_to:
                # The API compares timestamps, so a bare end date would exclude records added later that day.
                dates["to_date"] = f"{self.date_to.isoformat()}T23:59:59"
            c[DATE_FIELDS[self.date_field or "award_notice"]] = dates
        return c

    def build_criteria(self) -> dict[str, Any]:
        """The ``criteria`` object for /v2/projects/search. Raises ValueError for conflicting criteria."""
        built = self._built()
        if clash := sorted(set(built) & set(self.criteria)):
            raise ValueError(f"Criteria given both as options and as raw criteria: {', '.join(clash)}")
        return {**built, **self.criteria}

    def is_empty(self) -> bool:
        return not self.build_criteria()

    def filters(self) -> dict[str, Any]:
        """The spec as JSON-serializable data (dates as ISO strings)."""
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(self).items()}


def load_criteria(value: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Raw criteria from a JSON string or, with a leading ``@``, a JSON file. Accepts either the
    criteria object itself or a whole request body (``{"criteria": {...}, "sort_field": ...}``,
    as taken by nihreporter-fetch 0.1). Returns (criteria, other request settings).
    """
    text = value.strip()
    source = "criteria"
    if text.startswith("@"):
        source = text[1:]
        with open(source, encoding="utf-8") as f:
            text = f.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {source}: {e}") from None
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {source}")
    if "criteria" in data:
        body = dict(data)
        criteria = body.pop("criteria") or {}
        if not isinstance(criteria, dict):
            raise ValueError(f"'criteria' in {source} must be a JSON object")
        return criteria, body
    return data, {}


def resolve_date_range(
    days: int | None = None, date_from: date | None = None, date_to: date | None = None, today: date | None = None
) -> tuple[date | None, date | None]:
    """
    Return (from, to). Explicit dates win; ``days`` means the ``days`` days up to ``date_to``
    (default today). With neither, the range stays open (None).
    """
    if days is not None and date_from is None:
        date_to = date_to or today or date.today()
        date_from = date_to - timedelta(days=days)
    if date_from and date_to and date_from > date_to:
        raise ReporterError(f"Start of the date range ({date_from}) is after its end ({date_to}).")
    return date_from, date_to
