"""
Contract tests against the real NIH RePORTER API, to catch upstream changes in criteria, fields
or limits. RePORTER ignores unknown criteria and returns no matches for unknown values instead of
failing, so these tests check that every filter actually narrows a search. Skipped unless
NIHREPORTER_FETCH_LIVE=1; CI runs them weekly. They take a few minutes (one request per second).
"""

from datetime import date

import jsonschema
import pytest

from nihreporter_fetch import QuerySpec, ReporterClient, ReporterError
from nihreporter_fetch.client import RAW_FIELDS
from nihreporter_fetch.query import (
    ADMIN_AGENCIES,
    AGENCIES,
    APPLICATION_TYPES,
    CATCH_ALL_AGENCIES,
    COVID_RESPONSE,
    SORT_FIELDS,
)
from nihreporter_fetch.schema import project_schema

pytestmark = pytest.mark.live

FRED_HUTCH = QuerySpec(org_names=["FRED HUTCH*"], fiscal_years=[2025])


@pytest.fixture(scope="module")
def client():
    return ReporterClient()


@pytest.fixture(scope="module")
def validator():
    return jsonschema.Draft202012Validator(project_schema())


def test_search_records_match_schema(client, validator):
    spec = QuerySpec(org_names=["FRED HUTCH*"], fiscal_years=[2025], activity_codes=["R01"])
    projects = [p for batch in client.iter_search(spec, max_results=25) for p in batch]
    assert len(projects) == 25 and client.search_info["total_found"] > 25
    client.attach_publications(projects)
    for project in projects:
        validator.validate(project)
        assert project["activity_code"] == "R01" and project["fiscal_year"] == 2025
        assert "FRED HUTCH" in project["organization"]["name"]
    assert any(p["publications"] for p in projects)


def test_paging_and_count_agree(client):
    spec = QuerySpec(org_names=["FRED HUTCH*"], fiscal_years=[2025], activity_codes=["R01"])
    client.page_size = 20
    try:
        projects = [p for batch in client.iter_search(spec, max_results=None) for p in batch]
    finally:
        client.page_size = ReporterClient.page_size
    assert len(projects) == client.count(spec) == client.search_info["total_found"]
    assert len({p["appl_id"] for p in projects}) == len(projects)


def test_every_filter_is_accepted(client):
    """Each filter must narrow the search: 0 means a bad value, all means an ignored criterion."""
    base = {"fiscal_years": [2024]}
    total = client.count(QuerySpec(**base))
    for kwargs in [
        {"text": "glioblastoma"},
        {"text": "glioblastoma", "text_fields": ["title"]},
        {"text": "glioblastoma immunotherapy", "text_operator": "or"},
        {"text": '"CAR T" AND glioblastoma', "text_operator": "advanced"},
        {"pi_names": ["Corey, Lawrence"]},
        {"pi_names": ["Bedford"]},
        {"pi_profile_ids": [1860036]},
        {"po_names": ["Guo"]},
        {"org_names": ["FRED HUTCH*"]},
        {"org_names_exact": ["FRED HUTCHINSON CANCER CENTER"]},
        {"org_cities": ["seattle"]},
        {"org_states": ["wa"]},
        {"org_countries": ["canada"]},
        {"project_nums": ["R01CA*"]},
        {"agencies": ["NCI"]},
        {"agencies": ["ALLCDC"]},
        {"activity_codes": ["r01"]},
        {"application_types": ["1"]},
        {"opportunity_numbers": ["PA-20-185"]},
        {"cong_dists": ["WA-07"]},
        {"spending_categories": [31, 132]},
        {"spending_categories": [31, 132], "match_all_categories": True},
        {"covid_response": ["Reg-CV"]},
        {"min_award": 1_000_000},
        {"max_award": 100_000},
        {"min_award": 100_000, "max_award": 200_000},
        {"multi_pi": True},
        {"subprojects": "exclude"},
        {"subprojects": "only"},
        {"date_field": "award_notice", "date_from": date(2024, 6, 1)},
        {"date_field": "project_start", "date_from": date(2024, 1, 1)},
        {"date_field": "project_end", "date_to": date(2025, 1, 1)},
        {"date_field": "added", "date_from": date(2024, 6, 1)},
        {"criteria": {"project_start_date": {"from_date": "2024-01-01"}}},
    ]:
        count = client.count(QuerySpec(**base, **kwargs))
        assert 0 < count < total, kwargs
    everything = client.count(QuerySpec(criteria={"fiscal_years": list(range(1985, date.today().year + 2))}))
    for kwargs in [{"active": True}, {"newly_added": True}]:
        assert 0 < client.count(QuerySpec(**kwargs)) < everything, kwargs


def test_every_code_has_projects(client):
    for agency in AGENCIES:
        assert client.count(QuerySpec(agencies=[agency])) > 0, agency
    for code in COVID_RESPONSE:
        assert client.count(QuerySpec(covid_response=[code])) > 0, code
    for application_type in APPLICATION_TYPES:
        assert client.count(QuerySpec(application_types=[application_type])) > 0, application_type


def test_agencies_cover_recent_fiscal_years(client):
    """
    Large searches are split by institute and agency, which only works if together they cover the
    projects. A few carry codes no search reaches (2 of 76,387 in FY2025); a new institute would be many more.
    """
    for year in (2023, 2025):
        total = client.count(QuerySpec(fiscal_years=[year]))
        covered = client.count(QuerySpec(fiscal_years=[year], agencies=[*ADMIN_AGENCIES, *CATCH_ALL_AGENCIES]))
        assert total - covered <= total // 10_000, (year, total, covered)


def test_split_search_retrieves_everything(client):
    spec = QuerySpec(org_names=["FRED HUTCH*"], fiscal_years=["2023-2025"])
    client.search_limit = 300
    try:
        projects = [p for batch in client.iter_search(spec, max_results=None) for p in batch]
    finally:
        client.search_limit = ReporterClient.search_limit
    info = client.search_info
    assert info["subsearches"] > 3 and not info["failed_batches"] and not info["truncated"]
    assert len(projects) == len({p["appl_id"] for p in projects}) == info["total_found"] == client.count(spec)


def test_end_dates_include_the_whole_day(client):
    latest = date.fromisoformat(client.latest_update()[:10])
    assert client.count(QuerySpec(date_field="added", date_from=latest, date_to=latest)) > 0


def test_sort_fields_are_accepted(client):
    for field in SORT_FIELDS:
        projects = client.search_projects(FRED_HUTCH, max_results=2, sort=f"{field}:desc")
        assert len(projects) == 2, field


def test_raw_field_names(client):
    [project] = client.search_projects(FRED_HUTCH, max_results=1, raw=True, fields=RAW_FIELDS)
    expected = {"".join("_" + c.lower() if c.isupper() else c for c in f).lstrip("_") for f in RAW_FIELDS}
    assert set(project) == expected
    [project] = client.search_projects(FRED_HUTCH, max_results=1, raw=True, fields=["ProjectTitle"])
    assert set(project) == {"appl_id", "project_title"}


def test_ids_pmids_and_publication_links(client, validator):
    projects = client.fetch_projects([11124893, 11014863, 99999999])
    assert sorted(p["appl_id"] for p in projects) == [11014863, 11124893]
    assert client.search_info["missing_ids"] == [99999999]
    projects = [p for batch in client.iter_pmids([33298401, 1]) for p in batch]
    assert len(projects) >= 3 and client.search_info["missing_ids"] == [1]
    for project in projects:
        validator.validate(project)
    links = client.publication_links(core_project_nums=["R01AI165821"])
    assert len(links) > 10 and all(link["core_project_num"] == "R01AI165821" for link in links)


def test_errors_are_explained(client):
    with pytest.raises(ReporterError, match="Maximum offset is 14,999"):
        client._search({"fiscal_years": [2023]}, 15_000, 10)
    assert client.get_project(99999999) is None
