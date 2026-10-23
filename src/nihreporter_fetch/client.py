"""NIH RePORTER API v2 client: search projects, fetch them by ID, and look up linked publications."""

from __future__ import annotations

import html
import json
import logging
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ._meta import TOOL_NAME, ReporterError, __version__
from .parse import Project, parse_project, record_id
from .query import ADMIN_AGENCIES, AGENCY_GROUPS, CATCH_ALL_AGENCIES, QuerySpec, parse_sort

log = logging.getLogger(__name__)

API_URL = "https://api.reporter.nih.gov/v2/"
# The API returns at most 500 records per request, serves at most 15,000 records of one search
# (offset + limit) and at most 10,000 links of one publications search.
PAGE_SIZE_LIMIT = 500
SEARCH_LIMIT = 15_000
PUBLICATION_LIMIT = 10_000
USER_AGENT = f"{TOOL_NAME}/{__version__} (+https://github.com/robert-mcdermott/nihreporter-fetch)"
# Project fields as the API names them in include_fields (--raw --fields). It ignores unknown names.
RAW_FIELDS = (
    "ApplId",
    "SubprojectId",
    "FiscalYear",
    "ProjectNum",
    "ProjectSerialNum",
    "Organization",
    "AwardType",
    "ActivityCode",
    "AwardAmount",
    "IsActive",
    "ProjectNumSplit",
    "PrincipalInvestigators",
    "ContactPiName",
    "ProgramOfficers",
    "AgencyIcAdmin",
    "AgencyIcFundings",
    "CongDist",
    "SpendingCategories",
    "SpendingCategoriesDesc",
    "ProjectStartDate",
    "ProjectEndDate",
    "OrganizationType",
    "GeoLatLon",
    "OpportunityNumber",
    "FullStudySection",
    "AwardNoticeDate",
    "IsNew",
    "MechanismCodeDc",
    "CoreProjectNum",
    "Terms",
    "PrefTerms",
    "AbstractText",
    "ProjectTitle",
    "PhrText",
    "AgencyCode",
    "CovidResponse",
    "ArraFunded",
    "BudgetStart",
    "BudgetEnd",
    "CfdaCode",
    "FundingMechanism",
    "DirectCostAmt",
    "IndirectCostAmt",
    "ProjectDetailUrl",
    "DateAdded",
)
Link = dict[str, Any]


def make_session(pool_size: int = 4, retries: int = 4) -> requests.Session:
    """A requests session that retries transient failures (429/5xx) with backoff."""
    retry = Retry(
        total=retries,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        # RePORTER searches are POST requests but read-only, so they are safe to retry.
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=pool_size))
    return session


def _positive_ints(ids: Iterable[int | str], name: str) -> list[int]:
    result = []
    bad = []
    for value in ids:
        text = str(value).strip()
        if not text:
            continue
        if text.isdigit() and int(text) > 0:
            result.append(int(text))
        else:
            bad.append(text)
    if bad:
        raise ValueError(f"Invalid {name}(s): {', '.join(bad[:5])} (expected positive whole numbers)")
    return list(dict.fromkeys(result))


def normalize_ids(ids: Iterable[int | str]) -> list[int]:
    """De-duplicate application IDs, keeping their order. Raises ValueError for malformed IDs."""
    return _positive_ints(ids, "application ID")


def normalize_pmids(pmids: Iterable[int | str]) -> list[int]:
    """De-duplicate PubMed IDs, keeping their order. Raises ValueError for malformed IDs."""
    return _positive_ints(pmids, "PMID")


def raw_field_name(name: str) -> str:
    """The API's name for a project field, from either form: ``project_title`` or ``ProjectTitle``."""
    key = name.strip().replace("_", "").lower()
    for field in RAW_FIELDS:
        if field.lower() == key:
            return field
    raise ValueError(f"Unknown project field {name!r}; use one of {', '.join(RAW_FIELDS)}")


def _error_message(response: requests.Response) -> str:
    """The API's explanation of a rejected request, which comes as {"message": ...} or a list."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("message"):
        message = str(data["message"])
    elif isinstance(data, list) and data:
        message = "; ".join(map(str, data))
    else:
        message = response.text.strip() or f"HTTP {response.status_code}"
    return html.unescape(message)


def _chunks(values: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class ReporterClient:
    """
    Search NIH RePORTER and fetch project records. Batches are yielded as lists of records:
    normalized dicts (see ``parse_project``) or, with ``raw=True``, the API's own project objects.
    Details of the run (criteria, counts, failed batches) are collected in ``self.search_info``.
    """

    page_size = PAGE_SIZE_LIMIT
    search_limit = SEARCH_LIMIT
    publication_limit = PUBLICATION_LIMIT
    id_batch_size = PAGE_SIZE_LIMIT
    # Core project numbers per publications request; batches over the link limit are halved.
    publication_batch_size = 100
    page_attempts = 2
    # NIH asks for no more than one request per second.
    min_interval = 1.0

    def __init__(self, session: requests.Session | None = None, timeout: float = 120, base_url: str = API_URL):
        self.base_url = base_url
        self.timeout = timeout
        self.session = session or make_session()
        # For narrowing down failures, where retrying a request that fails every time only costs time.
        self.quick_session = session or make_session(retries=0)
        self.search_info: dict[str, Any] = {}
        self._last_request = 0.0
        # Publications by core project number, kept for the life of the client.
        self._publications: dict[str, list[int]] = {}

    def _post(self, path: str, body: dict[str, Any], quick: bool = False) -> Any:
        """Make a rate-limited POST request and return the decoded JSON."""
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        session = self.quick_session if quick else self.session
        try:
            response = session.post(f"{self.base_url}{path}", json=body, timeout=self.timeout)
        finally:
            self._last_request = time.monotonic()
        if response.status_code in (400, 404, 422):
            # The API explains bad requests, e.g. "Requested limit exceeded. Maximum offset is 14,999."
            raise ReporterError(_error_message(response))
        response.raise_for_status()
        return response.json()

    def _post_page(self, path: str, body: dict[str, Any], quick: bool = False) -> dict[str, Any]:
        """
        ``_post`` for one page of results, retried once for connections dropped mid-download.
        ``quick`` makes a single attempt, without the session's retries.
        """
        attempts = 1 if quick else self.page_attempts
        for attempt in range(1, attempts + 1):
            try:
                data = self._post(path, body, quick)
                if not isinstance(data, dict):
                    raise ValueError(f"Unexpected response: {str(data)[:200]}")
                return data
            except (requests.RequestException, ValueError) as e:
                if attempt == attempts:
                    raise
                log.debug("Request failed (%s); retrying.", e)
                time.sleep(5)
        raise AssertionError("unreachable")

    def _search(self, criteria: dict[str, Any], offset: int, limit: int, **extra: Any) -> dict[str, Any]:
        return self._post_page("projects/search", {"criteria": criteria, "offset": offset, "limit": limit, **extra})

    def _count(self, criteria: dict[str, Any]) -> int:
        data = self._search(criteria, 0, 1, include_fields=["ApplId"])
        return int((data.get("meta") or {}).get("total") or 0)

    # -- metadata --------------------------------------------------------------------------

    def latest_update(self) -> str | None:
        """When the newest project was added to RePORTER (an ISO timestamp), as a data version."""
        data = self._search(
            {}, 0, 1, include_fields=["ApplId", "DateAdded"], sort_field="date_added", sort_order="desc"
        )
        results = data.get("results") or []
        return results[0].get("date_added") if results else None

    def count(self, spec: QuerySpec) -> int:
        """The number of projects matching a search."""
        criteria = spec.build_criteria()
        if not criteria:
            raise ReporterError("No search criteria provided.")
        return self._count(criteria)

    def get_project(self, appl_id: int | str, raw: bool = False) -> Project | None:
        """One project by application ID, or None if it doesn't exist."""
        [appl_id] = normalize_ids([appl_id])
        results = self._search({"appl_ids": [appl_id]}, 0, 1).get("results") or []
        if not results:
            return None
        return results[0] if raw else parse_project(results[0])

    # -- searching -------------------------------------------------------------------------

    @staticmethod
    def _include_fields(fields: Iterable[str] | None) -> dict[str, Any]:
        if not fields:
            return {}
        # The application ID is needed to identify records (for --merge, --resume and listings).
        return {"include_fields": list(dict.fromkeys(["ApplId", *map(raw_field_name, fields)]))}

    def _convert(self, projects: list[Project], raw: bool) -> list[Project]:
        if raw:
            return projects
        records = []
        for project in projects:
            try:
                records.append(parse_project(project))
            except (KeyError, TypeError, AttributeError, ValueError) as e:
                self.search_info["skipped_records"] += 1
                log.warning("Skipping a project that could not be parsed: %r", e)
        return records

    def iter_search(
        self,
        spec: QuerySpec,
        max_results: int | None = 1000,
        sort: str | None = None,
        raw: bool = False,
        fields: Iterable[str] | None = None,
    ) -> Iterator[list[Project]]:
        """
        Search and yield batches (pages) of records; ``max_results=None`` fetches every match.
        Searches matching more than the 15,000 records RePORTER serves per search are split by
        fiscal year, then by agency and institute. Raises ReporterError if the search itself fails.
        A later page that fails is recorded in ``search_info["failed_batches"]``.
        """
        criteria = spec.build_criteria()
        if not criteria:
            raise ReporterError("No search criteria provided.")
        # Sorting by a unique field keeps pages stable, so paging neither repeats nor skips records.
        sort_field, sort_order = parse_sort(sort) if sort else ("appl_id", "asc")
        extra = {"sort_field": sort_field, "sort_order": sort_order, **self._include_fields(fields)}
        log.info("Search: %s", json.dumps(criteria))
        self._init_info(
            source="search",
            criteria=criteria,
            filters=spec.filters(),
            sort=sort,
            raw=raw,
            max_results=max_results,
            total_found=None,
            expected=None,
            truncated=False,
            subsearches=1,
        )

        first_limit = min(self.page_size, max_results or self.page_size)
        try:
            first = self._search(criteria, 0, first_limit, **extra)
        except (ReporterError, requests.RequestException, ValueError) as e:
            raise ReporterError(f"Error searching NIH RePORTER: {e}") from e
        total = int((first.get("meta") or {}).get("total") or 0)
        wanted = total if max_results is None else min(total, max_results)
        log.info("Found %d projects", total)

        slices, unreachable = [(criteria, total)], 0
        if wanted > self.search_limit:
            try:
                slices, unreachable = self._split(criteria, total)
            except (ReporterError, requests.RequestException, ValueError) as e:
                raise ReporterError(f"Error splitting a search over {self.search_limit:,} projects: {e}") from e
            if sort and max_results is not None:
                log.warning("The search was split, so results are sorted within each part, not overall.")
        # Parts may overlap (an agency and its institutes); records seen twice are skipped below.
        over_limit = sum(max(0, n - self.search_limit) for _, n in slices)
        expected = min(wanted, max(0, total - unreachable - over_limit))
        self.search_info.update(
            total_found=total, expected=expected, truncated=expected < total, subsearches=len(slices)
        )
        if expected < wanted:
            log.warning(
                "Only %d of %d matching projects can be retrieved: RePORTER serves at most %d per search, and "
                "splitting the search by fiscal year and agency couldn't reach the rest. Narrow the search "
                "(e.g. by --agency or --activity-code) to get them.",
                expected,
                wanted,
                self.search_limit,
            )
        elif expected < total:
            log.warning(
                "Only %d of %d matching projects will be retrieved (raise --max-results, use --max-results all, "
                "or narrow the search).",
                expected,
                total,
            )

        seen: set[int] = set()
        for number, (part, count) in enumerate(slices, 1):
            if self.search_info["retrieved"] >= expected:
                break
            reuse = first if part is criteria else None
            yield from self._iter_part(part, min(count, self.search_limit), expected, extra, raw, seen, reuse, number)
        log.info("Total projects retrieved: %d", self.search_info["retrieved"])

    def _iter_part(
        self,
        criteria: dict[str, Any],
        limit: int,
        expected: int,
        extra: dict[str, Any],
        raw: bool,
        seen: set[int],
        first: dict[str, Any] | None,
        number: int,
    ) -> Iterator[list[Project]]:
        """Page through one (sub)search, up to ``limit`` records, skipping records already seen."""
        offset = 0
        while offset < limit and self.search_info["retrieved"] < expected:
            size = min(self.page_size, limit - offset, expected - self.search_info["retrieved"])
            if first is not None:
                data, size, first = first, len(first.get("results") or []), None
            else:
                try:
                    data = self._search(criteria, offset, size, **extra)
                except (ReporterError, requests.RequestException, ValueError) as e:
                    where: dict[str, Any] = {"offset": offset, "after": self.search_info["retrieved"]}
                    if self.search_info["subsearches"] > 1:
                        where = {"subsearch": number, "criteria": criteria, **where}
                    self._record_failure(where, e)
                    return
            projects = data.get("results") or []
            offset += size
            new = [p for p in projects if record_id(p) not in seen][: expected - self.search_info["retrieved"]]
            seen.update(map(record_id, new))
            log.info("Fetched %d projects (offset %d)", len(projects), offset - size)
            records = self._convert(new, raw)
            self.search_info["retrieved"] += len(records)
            if records:
                yield records
            if len(projects) < size:
                break

    def _split(self, criteria: dict[str, Any], total: int) -> tuple[list[tuple[dict[str, Any], int]], int]:
        """
        Partition a search into sub-searches of at most ``search_limit`` matches each: by fiscal
        year, then by agency and institute. Parts that can't be split further are kept as they are.
        Returns the parts and the number of matches no part covers.
        """
        log.info(
            "%d projects match, more than the %d RePORTER serves per search; splitting it.", total, self.search_limit
        )
        parts: list[tuple[dict[str, Any], int]] = []
        unreachable = 0
        pending = [(criteria, total)]
        while pending:
            part, count = pending.pop(0)
            split = self._children(part) if count > self.search_limit else None
            if not split:
                parts.append((part, count))
                continue
            key, values = split
            counted = [({**part, key: value}, self._count({**part, key: value})) for value in values]
            # Fiscal years don't overlap, but agencies can (an agency and its institutes), so count their union.
            union = {**part, key: [v for value in values for v in value]}
            covered = self._count(union) if key == "agencies" else sum(n for _, n in counted)
            if covered < count:
                log.warning("Splitting %s left %d projects unaccounted for.", json.dumps(part), count - covered)
                unreachable += count - covered
            pending[:0] = [(child, n) for child, n in counted if n]
        log.info("Split into %d searches", len(parts))
        return parts, unreachable

    def _children(self, criteria: dict[str, Any]) -> tuple[str, list[list[Any]]] | None:
        """How to split a search: the criterion to vary and its values, or None if it can't be split."""
        # Fiscal years partition projects, except that include_active_projects adds other years.
        years = criteria.get("fiscal_years") or []
        if len(years) != 1 and not criteria.get("include_active_projects"):
            return "fiscal_years", [[year] for year in years or self._fiscal_year_range(criteria)]
        # Institutes, plus the small agencies as a whole, which also catch centers with unpublished codes.
        agencies = criteria.get("agencies") or [*ADMIN_AGENCIES, *CATCH_ALL_AGENCIES]
        if len(agencies) == 1:
            agencies = list(AGENCY_GROUPS.get(agencies[0], ()))
        if len(agencies) > 1:
            return "agencies", [[agency] for agency in agencies]
        return None

    def _fiscal_year_range(self, criteria: dict[str, Any]) -> list[int]:
        years = []
        for order in ("asc", "desc"):
            data = self._search(
                criteria, 0, 1, include_fields=["ApplId", "FiscalYear"], sort_field="fiscal_year", sort_order=order
            )
            years.append(int((data.get("results") or [{}])[0]["fiscal_year"]))
        return list(range(years[0], years[1] + 1))

    def iter_ids(
        self, ids: Iterable[int | str], raw: bool = False, fields: Iterable[str] | None = None
    ) -> Iterator[list[Project]]:
        """
        Fetch specific projects by application ID in batches. IDs that don't exist are listed in
        ``search_info["missing_ids"]``.
        """
        ids = normalize_ids(ids)
        self._init_info(source="ids", raw=raw, total_found=len(ids), expected=len(ids), truncated=False)
        self.search_info["missing_ids"] = []
        log.info("Fetching %d projects by application ID", len(ids))
        yield from self._fetch_ids(ids, raw, fields, self.search_info["missing_ids"])
        if missing := self.search_info["missing_ids"]:
            log.warning("%d application ID(s) not found: %s", len(missing), ", ".join(map(str, missing[:10])))
        log.info("Total projects retrieved: %d", self.search_info["retrieved"])

    def _fetch_ids(
        self, ids: list[int], raw: bool, fields: Iterable[str] | None, missing: list[int]
    ) -> Iterator[list[Project]]:
        extra = {"sort_field": "appl_id", "sort_order": "asc", **self._include_fields(fields)}
        for chunk in _chunks(ids, self.id_batch_size):
            try:
                data = self._search({"appl_ids": chunk}, 0, len(chunk), **extra)
            except (ReporterError, requests.RequestException, ValueError) as e:
                self._record_failure({"ids": [chunk[0], chunk[-1]], "count": len(chunk)}, e)
                continue
            projects = data.get("results") or []
            found = {record_id(p) for p in projects}
            missing.extend(i for i in chunk if i not in found)
            records = self._convert(projects, raw)
            self.search_info["retrieved"] += len(records)
            if records:
                yield records

    def iter_pmids(
        self, pmids: Iterable[int | str], raw: bool = False, fields: Iterable[str] | None = None
    ) -> Iterator[list[Project]]:
        """
        Fetch the projects that publications (PMIDs) are linked to, as RePORTER links them: one
        record per core project. PMIDs without a linked project are listed in
        ``search_info["missing_ids"]``.
        """
        pmids = normalize_pmids(pmids)
        self._init_info(source="pmids", raw=raw, pmids=len(pmids), truncated=False, missing_ids=[])
        log.info("Looking up the projects linked to %d PMIDs", len(pmids))
        try:
            links = self.publication_links(pmids=pmids)
        except (ReporterError, requests.RequestException, ValueError) as e:
            raise ReporterError(f"Error looking up publications in NIH RePORTER: {e}") from e
        linked = {link["pmid"] for link in links}
        self.search_info["missing_ids"] = [p for p in pmids if p not in linked]
        if missing := self.search_info["missing_ids"]:
            log.warning("%d PMID(s) not linked to any project: %s", len(missing), ", ".join(map(str, missing[:10])))
        ids = list(dict.fromkeys(link["appl_id"] for link in links if link["appl_id"]))
        self.search_info.update(total_found=len(ids), expected=len(ids))
        not_found: list[int] = []
        yield from self._fetch_ids(ids, raw, fields, not_found)
        if not_found:
            log.warning("%d linked project(s) not found: %s", len(not_found), ", ".join(map(str, not_found[:10])))
        log.info("Total projects retrieved: %d", self.search_info["retrieved"])

    def iter_raw(self, path: str | Path, raw: bool = False) -> Iterator[list[Project]]:
        """
        Read raw API project objects saved earlier and yield them (normalized unless ``raw``)
        without contacting RePORTER. Accepts the JSON written by nihreporter-fetch 0.1
        (``{"meta", "results"}``), JSON documents written with ``--raw`` (``projects``), lists and JSONL.
        """
        self._init_info(source="raw", raw_file=str(path), raw=raw, total_found=None, expected=None, truncated=False)
        projects = load_raw_projects(path)
        for batch in _chunks(projects, self.page_size):
            records = self._convert(batch, raw)
            self.search_info["retrieved"] += len(records)
            if records:
                yield records

    def search_projects(self, spec: QuerySpec | None = None, max_results: int | None = 1000, **kwargs) -> list[Project]:
        """
        Convenience wrapper: run a search and return all records as a list. Keyword arguments
        other than ``sort``, ``raw`` and ``fields`` build the QuerySpec, e.g.
        ``search_projects(org_names=["FRED HUTCH*"], fiscal_years=[2024], activity_codes=["R01"])``.
        """
        options = {k: kwargs.pop(k) for k in ("sort", "raw", "fields") if k in kwargs}
        spec = spec or QuerySpec(**kwargs)
        return [p for batch in self.iter_search(spec, max_results, **options) for p in batch]

    def fetch_projects(self, ids: Iterable[int | str], raw: bool = False) -> list[Project]:
        """Convenience wrapper: fetch projects by application ID and return them as a list."""
        return [p for batch in self.iter_ids(ids, raw=raw) for p in batch]

    # -- publications ----------------------------------------------------------------------

    def publication_links(
        self, core_project_nums: Iterable[str] | None = None, pmids: Iterable[int | str] | None = None
    ) -> list[Link]:
        """
        Links between publications and projects, as ``{"pmid", "core_project_num", "appl_id"}``
        dicts. RePORTER links publications to core projects (all fiscal years of a grant).
        """
        if (core_project_nums is None) == (pmids is None):
            raise ValueError("Give either core_project_nums or pmids.")
        values: list[Any]
        if pmids is not None:
            key, values, size = "pmids", normalize_pmids(pmids), PAGE_SIZE_LIMIT
        else:
            key, size = "core_project_nums", self.publication_batch_size
            values = list(dict.fromkeys(n.strip().upper() for n in core_project_nums or [] if n.strip()))
        return [link for chunk in _chunks(values, size) for link in self._links(key, chunk)]

    def _links(self, key: str, values: list[Any], quick: bool = False) -> list[Link]:
        """All links for one batch of values, halving the batch when it exceeds the link limit."""
        first = self._post_page(
            "publications/search", {"criteria": {key: values}, "offset": 0, "limit": self.page_size}, quick
        )
        total = int((first.get("meta") or {}).get("total") or 0)
        if total > self.publication_limit and len(values) > 1:
            half = len(values) // 2
            return self._links(key, values[:half], quick) + self._links(key, values[half:], quick)
        if total > self.publication_limit:
            log.warning(
                "%s %s has %d publication links; only %d can be retrieved.",
                key,
                values[0],
                total,
                self.publication_limit,
            )
        results = list(first.get("results") or [])
        offset = len(results)
        while results and offset < min(total, self.publication_limit):
            page = self._post_page(
                "publications/search", {"criteria": {key: values}, "offset": offset, "limit": self.page_size}, quick
            )
            batch = page.get("results") or []
            if not batch:
                break
            results.extend(batch)
            offset += len(batch)
        return [
            {"pmid": r.get("pmid"), "core_project_num": r.get("coreproject"), "appl_id": r.get("applid")}
            for r in results
            if r.get("pmid")
        ]

    def attach_publications(self, records: list[Project]) -> None:
        """
        Set each normalized record's ``publications`` to the sorted PMIDs linked to its core
        project. Core projects whose lookup fails are listed in ``search_info["failed_publications"]``
        and keep ``publications`` as None.
        """
        cores = list(dict.fromkeys(r["core_project_num"] for r in records if r.get("core_project_num")))
        wanted = [c for c in cores if c not in self._publications]
        for chunk in _chunks(wanted, self.publication_batch_size):
            failed: list[str] = []
            try:
                links = self._links("core_project_nums", chunk)
            except (ReporterError, requests.RequestException, ValueError) as e:
                links, failed = self._narrow_down(chunk, e)
            found: dict[str, set[int]] = {core: set() for core in chunk if core not in failed}
            for link in links:
                found.setdefault(link["core_project_num"], set()).add(link["pmid"])
            self._publications.update((core, sorted(pmids)) for core, pmids in found.items())
        for record in records:
            core = record.get("core_project_num")
            if not core:
                record["publications"] = []
            elif core in self._publications:
                record["publications"] = list(self._publications[core])

    def _narrow_down(self, cores: list[str], error: Exception) -> tuple[list[Link], list[str]]:
        """
        Look up a failed batch again in halves. RePORTER fails a whole batch with a server error
        when one of its core projects is broken, so this limits the failure to that project.
        Returns the links found and the core projects that failed.
        """
        if len(cores) == 1:
            self.search_info.setdefault("failed_publications", []).append(
                {"core_project_num": cores[0], "error": str(error)}
            )
            log.warning("Could not look up the publications of %s: %s", cores[0], error)
            return [], cores
        log.info("Publication lookup for %d core projects failed (%s); narrowing it down.", len(cores), error)
        links: list[Link] = []
        failed: list[str] = []
        half = len(cores) // 2
        for part in (cores[:half], cores[half:]):
            try:
                links += self._links("core_project_nums", part, quick=True)
            except (ReporterError, requests.RequestException, ValueError) as e:
                part_links, part_failed = self._narrow_down(part, e)
                links += part_links
                failed += part_failed
        return links, failed

    # -- bookkeeping -----------------------------------------------------------------------

    def _init_info(self, **info: Any) -> None:
        self.search_info = {
            **info,
            "retrieved": 0,
            "skipped_records": 0,
            "failed_batches": [],
            "failed_publications": [],
        }

    def _record_failure(self, where: dict[str, Any], error: Exception) -> None:
        self.search_info.setdefault("failed_batches", []).append({**where, "error": str(error)})
        log.warning("Batch %s failed: %s", where, error)


def load_raw_projects(path: str | Path) -> list[Project]:
    """Load raw API project objects from a JSON or JSONL file (see ``ReporterClient.iter_raw``)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as e:
            raise ReporterError(f"{path} is neither JSON nor JSONL: {e}") from e
    if isinstance(data, dict):
        key = next((k for k in ("projects", "results") if k in data), None)
        data = data[key] if key else [data] if "appl_id" in data else None
    if not isinstance(data, list) or not all(isinstance(p, dict) and "appl_id" in p for p in data):
        raise ReporterError(f"No projects found in {path}")
    if data and "admin_ic" in data[0]:
        raise ReporterError(
            f"{path} contains normalized records, not raw API projects; "
            "only output written with --raw (or by nihreporter-fetch 0.1) can be re-read."
        )
    return data
