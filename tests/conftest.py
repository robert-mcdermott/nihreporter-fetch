import copy
import json
import os
from pathlib import Path

import pytest
import requests

import nihreporter_fetch.client

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "projects_sample.json"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NIHREPORTER_FETCH_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live API test; set NIHREPORTER_FETCH_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch, request):
    if "live" not in request.keywords:
        monkeypatch.setattr(nihreporter_fetch.client.time, "sleep", lambda seconds: None)


class FakeResponse:
    def __init__(self, json_data=None, status=200, text=None):
        self._json = json_data
        self.status_code = status
        self.text = text if text is not None else (json.dumps(json_data) if json_data is not None else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json is None:
            raise requests.JSONDecodeError("Expecting value", self.text, 0)
        return self._json


class FakeSession:
    """Routes POST requests to handlers keyed by URL substring (first match wins) and records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.headers = {}

    def post(self, url, json=None, **kwargs):
        body = copy.deepcopy(json or {})
        self.calls.append((url, body))
        for key, handler in self.routes.items():
            if key in url:
                return handler(url, body)
        raise AssertionError(f"Unexpected request: {url}")

    def calls_to(self, fragment):
        return [body for url, body in self.calls if fragment in url]

    def searches(self):
        """Project searches, without the data-timestamp lookup every run starts with."""
        return [b for b in self.calls_to("projects/search") if b.get("sort_field") != "date_added" or b["criteria"]]


def sample():
    return copy.deepcopy(json.loads(SAMPLE.read_text(encoding="utf-8")))


def sample_projects():
    """The real API project objects in the fixture (fresh copies)."""
    return sample()["projects"]


def synthetic_project(n, fiscal_year=2024, agency="NCI", **extra):
    """A minimal NIH project with application ID 1000 + n."""
    return {
        "appl_id": 1000 + n,
        "project_num": f"5R01CA{n:06d}-01",
        "core_project_num": f"R01CA{n:06d}",
        "fiscal_year": fiscal_year,
        "project_title": f"Project {n}",
        "agency_code": "NIH",
        "agency_ic_admin": {"code": "CA", "abbreviation": agency, "name": agency},
        "date_added": "2024-01-01T00:00:00",
        **extra,
    }


def _pascal(key):
    return "".join(part.capitalize() for part in key.split("_"))


def projects_handler(projects=None, fail_offsets=(), limit=15_000, fail_criteria=None):
    """
    Serve /projects/search like the API: filter by appl_ids, fiscal_years, agencies (by
    administering IC or agency code) and date_added; sort; page with offset/limit under the API's limits; apply
    include_fields. Requests at an offset in ``fail_offsets`` (or whose criteria contain
    ``fail_criteria``) answer 500.
    """
    projects = sample_projects() if projects is None else projects

    def handler(url, body):
        c = body.get("criteria") or {}
        if fail_criteria and fail_criteria.items() <= c.items():
            return FakeResponse({"message": "An unexpected error occurred"}, status=500)
        pool = projects
        if "appl_ids" in c:
            pool = [p for p in pool if p["appl_id"] in c["appl_ids"]]
        if "fiscal_years" in c:
            pool = [p for p in pool if p.get("fiscal_year") in c["fiscal_years"]]
        if "agencies" in c:
            pool = [
                p
                for p in pool
                if {(p.get("agency_ic_admin") or {}).get("abbreviation"), p.get("agency_code")} & set(c["agencies"])
            ]
        if "date_added" in c:
            low, high = c["date_added"].get("from_date", ""), c["date_added"].get("to_date", "9999")
            pool = [p for p in pool if low <= p["date_added"] <= high]
        field = body.get("sort_field") or "appl_id"
        pool = sorted(pool, key=lambda p: p.get(field) or 0, reverse=body.get("sort_order") == "desc")
        offset, size = body.get("offset", 0), body.get("limit", 50)
        if size > 500:
            return FakeResponse({"message": "System doesn't support limit value greater than 500."}, status=400)
        if offset >= limit:
            return FakeResponse({"message": f"Requested limit exceeded. Maximum offset is {limit - 1:,}."}, status=400)
        if offset in fail_offsets:
            return FakeResponse({"message": "An unexpected error occurred"}, status=500)
        results = pool[offset : min(offset + size, limit)]
        if fields := body.get("include_fields"):
            results = [{k: v for k, v in p.items() if _pascal(k) in fields} for p in results]
        return FakeResponse({"meta": {"total": len(pool), "offset": offset, "limit": size}, "results": results})

    return handler


def publications_handler(links=None, limit=10_000):
    """Serve /publications/search: links filtered by core_project_nums or pmids, paged."""
    links = sample()["publication_links"] if links is None else links

    def handler(url, body):
        c = body.get("criteria") or {}
        pool = links
        if "core_project_nums" in c:
            pool = [link for link in pool if link["coreproject"] in c["core_project_nums"]]
        if "pmids" in c:
            pool = [link for link in pool if link["pmid"] in c["pmids"]]
        offset, size = body.get("offset", 0), body.get("limit", 50)
        results = pool[offset : min(offset + size, limit)]
        return FakeResponse({"meta": {"total": len(pool), "offset": offset}, "results": results})

    return handler


def fake_api(links=None, **kwargs):
    return FakeSession(
        {"projects/search": projects_handler(**kwargs), "publications/search": publications_handler(links)}
    )


def by_id(projects):
    return {p["appl_id"]: p for p in projects}


@pytest.fixture
def raw_projects():
    return sample_projects()


@pytest.fixture
def records(raw_projects):
    from nihreporter_fetch import parse_project

    return {r["appl_id"]: r for r in map(parse_project, raw_projects)}
