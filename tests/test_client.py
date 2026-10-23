import json
import logging

import pytest
import requests
from conftest import (
    FakeResponse,
    FakeSession,
    by_id,
    fake_api,
    projects_handler,
    publications_handler,
    sample,
    synthetic_project,
)

from nihreporter_fetch import QuerySpec, ReporterClient, ReporterError, normalize_ids, normalize_pmids
from nihreporter_fetch.client import USER_AGENT, load_raw_projects, make_session, raw_field_name
from nihreporter_fetch.query import ADMIN_AGENCIES, CATCH_ALL_AGENCIES, NIH_ICS

SPEC = QuerySpec(org_names=["FRED HUTCH*"])


def client_for(session, page_size=None, search_limit=None):
    client = ReporterClient(session=session)
    if page_size:
        client.page_size = page_size
    if search_limit:
        client.search_limit = search_limit
    return client


def ids(batches):
    return [p["appl_id"] for batch in batches for p in batch]


def test_search_pages_through_results():
    session = fake_api(projects=[synthetic_project(i) for i in range(5)])
    client = client_for(session, page_size=2)
    batches = list(client.iter_search(SPEC, max_results=None))
    assert [len(b) for b in batches] == [2, 2, 1]
    calls = session.searches()
    assert [(c["offset"], c["limit"]) for c in calls] == [(0, 2), (2, 2), (4, 1)]
    # Sorting by application ID by default keeps pages stable.
    assert (calls[0]["sort_field"], calls[0]["sort_order"]) == ("appl_id", "asc")
    assert calls[0]["criteria"] == {"org_names": ["FRED HUTCH*"]} and "include_fields" not in calls[0]
    info = client.search_info
    assert (info["total_found"], info["expected"], info["retrieved"], info["truncated"]) == (5, 5, 5, False)
    assert (info["subsearches"], info["source"], info["criteria"]) == (1, "search", {"org_names": ["FRED HUTCH*"]})


def test_max_results_limits_pages_and_marks_truncation(caplog):
    session = fake_api(projects=[synthetic_project(i) for i in range(5)])
    client = client_for(session, page_size=2)
    projects = [p for b in client.iter_search(SPEC, max_results=3) for p in b]
    assert len(projects) == 3
    # The last page asks for only what's still needed.
    assert [c["limit"] for c in session.searches()] == [2, 1]
    assert (client.search_info["expected"], client.search_info["truncated"]) == (3, True)
    assert "Only 3 of 5 matching projects will be retrieved" in caplog.text


def test_sort_is_passed_and_validated():
    session = fake_api(projects=[synthetic_project(i) for i in range(3)])
    projects = client_for(session).search_projects(SPEC, sort="appl_id:desc")
    assert [p["appl_id"] for p in projects] == [1002, 1001, 1000]
    assert session.searches()[0]["sort_order"] == "desc"
    with pytest.raises(ValueError, match="Can't sort"):
        list(client_for(session).iter_search(SPEC, sort="project_title"))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"message": "Requested limit exceeded. Maximum offset is 14,999."}, "Maximum offset is 14,999"),
        (["Could not convert string to integer: x. Path &#39;criteria.fiscal_years[0]&#39;"], "'criteria.fiscal_years"),
    ],
)
def test_search_error_explains_the_problem(body, message):
    client = client_for(FakeSession({"projects/search": lambda url, b: FakeResponse(body, status=400)}))
    with pytest.raises(ReporterError, match=message):
        list(client.iter_search(SPEC))


def test_failed_later_page_is_recorded():
    session = fake_api(projects=[synthetic_project(i) for i in range(5)], fail_offsets=(2,))
    client = client_for(session, page_size=2)
    assert len(client.search_projects(SPEC)) == 2
    [failure] = client.search_info["failed_batches"]
    assert (failure["offset"], failure["after"]) == (2, 2) and "subsearch" not in failure


def test_page_retried_after_dropped_connection():
    handler = projects_handler(projects=[synthetic_project(0)])
    attempts = []

    def flaky(url, body):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("connection reset")
        return handler(url, body)

    client = client_for(FakeSession({"projects/search": flaky}))
    assert len(client.search_projects(SPEC)) == 1 and len(attempts) == 2


def test_raw_mode_returns_api_objects_and_limits_fields(raw_projects):
    session = fake_api()
    client = client_for(session)
    projects = client.search_projects(SPEC, raw=True, fields=["project_title", "AwardAmount"])
    assert session.searches()[0]["include_fields"] == ["ApplId", "ProjectTitle", "AwardAmount"]
    first = by_id(raw_projects)[100336]
    assert projects[0] == {k: first[k] for k in ("appl_id", "project_title", "award_amount")}
    assert all(set(p) == {"appl_id", "project_title", "award_amount"} for p in projects)
    assert client.search_projects(SPEC, raw=True) == sorted(raw_projects, key=lambda p: p["appl_id"])


def test_search_projects_builds_spec_from_keywords():
    session = fake_api()
    client_for(session).search_projects(org_names=["X*"], fiscal_years=["2024"], agencies=["nci"], sort="fiscal_year")
    body = session.searches()[0]
    assert body["criteria"] == {"fiscal_years": [2024], "org_names": ["X*"], "agencies": ["NCI"]}
    assert body["sort_field"] == "fiscal_year"


def test_empty_search_is_refused():
    with pytest.raises(ReporterError, match="No search criteria"):
        list(client_for(fake_api()).iter_search(QuerySpec()))
    with pytest.raises(ReporterError, match="No search criteria"):
        client_for(fake_api()).count(QuerySpec())


def test_unparseable_projects_are_skipped(caplog):
    session = fake_api(projects=[synthetic_project(0), synthetic_project(1, principal_investigators=[None])])
    client = client_for(session)
    assert len(client.search_projects(SPEC)) == 1
    assert client.search_info["skipped_records"] == 1 and "could not be parsed" in caplog.text


def mixed_years():
    return (
        [synthetic_project(i, 2023, "NCI") for i in range(3)]
        + [synthetic_project(i, 2023, "NIAID") for i in range(3, 6)]
        + [synthetic_project(i, 2024, "NCI") for i in range(6, 8)]
    )


def test_large_searches_are_split_by_fiscal_year_then_agency():
    session = fake_api(projects=mixed_years(), limit=4)
    client = client_for(session, page_size=2, search_limit=4)
    projects = [p for b in client.iter_search(SPEC, max_results=None) for p in b]
    assert sorted(p["appl_id"] for p in projects) == list(range(1000, 1008))
    info = client.search_info
    assert (info["total_found"], info["expected"], info["retrieved"], info["truncated"]) == (8, 8, 8, False)
    assert info["subsearches"] == 3 and not info["failed_batches"]
    searched = [c["criteria"] for c in session.searches() if c["limit"] > 1]
    assert {"org_names": ["FRED HUTCH*"], "fiscal_years": [2023], "agencies": ["NCI"]} in searched
    assert {"org_names": ["FRED HUTCH*"], "fiscal_years": [2023], "agencies": ["NIAID"]} in searched
    assert {"org_names": ["FRED HUTCH*"], "fiscal_years": [2024]} in searched
    # The fiscal years were found by sorting on them.
    assert [c["sort_order"] for c in session.searches() if c.get("sort_field") == "fiscal_year"] == ["asc", "desc"]
    # Each institute and small agency was counted once, with a one-record search, and then their union.
    counts = [c for c in session.searches() if c.get("include_fields") == ["ApplId"] and "agencies" in c["criteria"]]
    split = [*ADMIN_AGENCIES, *CATCH_ALL_AGENCIES]
    assert [c["criteria"]["agencies"] for c in counts] == [[a] for a in split] + [split]
    assert {c["limit"] for c in counts} == {1}


def test_split_uses_given_fiscal_years():
    session = fake_api(projects=mixed_years(), limit=4)
    client = client_for(session, search_limit=4)
    projects = client.search_projects(QuerySpec(fiscal_years=["2023-2024"], agencies=["NCI", "NIAID"]), None)
    assert len(projects) == 8
    assert not [c for c in session.searches() if c.get("sort_field") == "fiscal_year"]


def test_unsplittable_search_is_truncated(caplog):
    session = fake_api(projects=[synthetic_project(i, 2023, "NCI") for i in range(6)], limit=4)
    client = client_for(session, search_limit=4)
    projects = client.search_projects(QuerySpec(fiscal_years=[2023], agencies=["NCI"]), max_results=None)
    assert len(projects) == 4
    assert (client.search_info["expected"], client.search_info["truncated"]) == (4, True)
    assert "Only 4 of 6 matching projects can be retrieved" in caplog.text


def test_split_reports_projects_it_cannot_reach(caplog):
    projects = mixed_years() + [synthetic_project(9, 2023, "UNLISTED")]
    client = client_for(fake_api(projects=projects, limit=4), search_limit=4)
    assert len(client.search_projects(SPEC, max_results=None)) == 8
    assert "left 1 projects unaccounted for" in caplog.text
    assert (client.search_info["total_found"], client.search_info["expected"]) == (9, 8)
    assert client.search_info["truncated"]


def test_failed_subsearch_is_recorded_and_others_continue():
    session = fake_api(projects=mixed_years(), limit=4, fail_criteria={"agencies": ["NIAID"]})
    client = client_for(session, search_limit=4)
    count = client._count

    def count_without_failing(criteria):
        # Only fetching the NIAID part fails, not counting it.
        return 3 if criteria.get("agencies") == ["NIAID"] else count(criteria)

    client._count = count_without_failing
    projects = client.search_projects(SPEC, max_results=None)
    assert len(projects) == 5
    [failure] = client.search_info["failed_batches"]
    assert failure["criteria"]["agencies"] == ["NIAID"] and failure["subsearch"] == 2


def test_split_children():
    client = client_for(fake_api())
    # Within a fiscal year: by institute, plus the small agencies as a whole.
    assert client._children({"fiscal_years": [2023]}) == (
        "agencies",
        [[a] for a in (*ADMIN_AGENCIES, *CATCH_ALL_AGENCIES)],
    )
    assert client._children({"fiscal_years": [2023], "agencies": ["NIH", "VA"]}) == ("agencies", [["NIH"], ["VA"]])
    assert client._children({"fiscal_years": [2023], "agencies": ["NIH"]}) == ("agencies", [[ic] for ic in NIH_ICS])
    assert client._children({"fiscal_years": [2023, 2024]}) == ("fiscal_years", [[2023], [2024]])
    # include_active_projects adds projects from other years, so years can't partition it.
    key, values = client._children({"include_active_projects": True})
    assert key == "agencies" and len(values) == len(ADMIN_AGENCIES) + len(CATCH_ALL_AGENCIES)
    assert client._children({"fiscal_years": [2023], "agencies": ["NCI"]}) is None


def test_fetch_by_ids_reports_missing(caplog):
    session = fake_api()
    client = client_for(session)
    client.id_batch_size = 2
    projects = client.fetch_projects(["11124893", 11014863, 99999999, 11124893])
    assert sorted(p["appl_id"] for p in projects) == [11014863, 11124893]
    assert [c["criteria"]["appl_ids"] for c in session.searches()] == [[11124893, 11014863], [99999999]]
    assert client.search_info["missing_ids"] == [99999999]
    assert (client.search_info["source"], client.search_info["total_found"]) == ("ids", 3)
    assert "1 application ID(s) not found" in caplog.text


def test_failed_id_batch_is_skipped():
    handler = projects_handler()

    def flaky(url, body):
        return FakeResponse(status=503) if 11124893 in body["criteria"]["appl_ids"] else handler(url, body)

    client = client_for(FakeSession({"projects/search": flaky}))
    client.id_batch_size = 1
    assert [p["appl_id"] for p in client.fetch_projects([11124893, 11014863])] == [11014863]
    assert client.search_info["failed_batches"][0]["ids"] == [11124893, 11124893]
    assert client.search_info["missing_ids"] == []


def linked_projects():
    return [
        synthetic_project(0, appl_id=11325011, core_project_num="T32DA007292"),
        synthetic_project(1, appl_id=11417127, core_project_num="P30AI094189"),
        synthetic_project(2, appl_id=10347282, core_project_num="K01DA046234"),
    ]


def test_fetch_projects_linked_to_pmids(caplog):
    session = fake_api(projects=linked_projects())
    client = client_for(session)
    projects = [p for b in client.iter_pmids([33298401, "1"]) for p in b]
    assert sorted(p["core_project_num"] for p in projects) == ["K01DA046234", "P30AI094189", "T32DA007292"]
    info = client.search_info
    assert (info["source"], info["pmids"], info["total_found"], info["missing_ids"]) == ("pmids", 2, 3, [1])
    assert session.calls_to("publications/search")[0]["criteria"] == {"pmids": [33298401, 1]}
    assert "1 PMID(s) not linked" in caplog.text


def test_publication_links_page_and_split(caplog):
    session = fake_api()
    client = client_for(session, page_size=5)
    links = client.publication_links(core_project_nums=["r01md013769 "])
    assert len(links) == 18 and set(links[0]) == {"pmid", "core_project_num", "appl_id"}
    assert [c["offset"] for c in session.calls_to("publications/search")] == [0, 5, 10, 15]
    # Batches with more links than the API serves are halved; a single project is capped.
    client.publication_limit = 10
    links = client.publication_links(core_project_nums=["R01MD013769", "T32DA007292"])
    assert len(links) == 11 and "has 18 publication links; only 10" in caplog.text
    with pytest.raises(ValueError, match="either"):
        client.publication_links()


def test_attach_publications(records):
    session = fake_api()
    client = client_for(session)
    client._init_info()
    batch = [records[10303004], records[11124893], {**records[11014863], "core_project_num": None}]
    client.attach_publications(batch)
    expected = sorted(link["pmid"] for link in sample()["publication_links"] if link["coreproject"] == "R01MD013769")
    assert batch[0]["publications"] == expected and len(expected) == 18
    assert batch[1]["publications"] == [] and batch[2]["publications"] == []
    # Core projects already looked up aren't requested again.
    calls = len(session.calls_to("publications/search"))
    client.attach_publications([dict(records[10303004], publications=None)])
    assert len(session.calls_to("publications/search")) == calls


def test_failed_publication_lookup_leaves_publications_unknown(records):
    session = fake_api()
    session.routes["publications/search"] = lambda url, body: FakeResponse(status=503)
    client = client_for(session)
    client._init_info()
    record = dict(records[10303004])
    client.attach_publications([record])
    assert record["publications"] is None
    assert [f["core_project_num"] for f in client.search_info["failed_publications"]] == ["R01MD013769"]
    assert client.search_info["failed_batches"] == []


def test_one_broken_core_project_does_not_fail_its_batch(records, caplog):
    """RePORTER answers a server error for any batch containing some core projects (e.g. U19AI149676)."""
    caplog.set_level(logging.INFO)
    session = fake_api()
    links = publications_handler()

    def broken(url, body):
        cores = body["criteria"]["core_project_nums"]
        return FakeResponse(status=500) if "U19AI149676" in cores else links(url, body)

    session.routes["publications/search"] = broken
    client = client_for(session)
    client._init_info()
    batch = [dict(records[r]) for r in (10303004, 11124893, 11014863, 11253759)]
    batch.insert(2, {**records[11052511], "core_project_num": "U19AI149676"})
    client.attach_publications(batch)
    assert [len(r["publications"]) if r["publications"] is not None else None for r in batch] == [18, 0, None, 0, 0]
    [failure] = client.search_info["failed_publications"]
    assert failure == {"core_project_num": "U19AI149676", "error": "500 error"}
    assert "narrowing it down" in caplog.text


def test_get_project_count_and_latest_update(raw_projects):
    session = fake_api()
    client = client_for(session)
    assert client.get_project("11124893")["project_num"] == "5R01AG088223-02"
    assert client.get_project(11124893, raw=True) == by_id(raw_projects)[11124893]
    assert client.get_project(99999999) is None
    assert client.count(SPEC) == len(raw_projects)
    assert session.calls_to("projects/search")[-1]["limit"] == 1
    assert client.latest_update() == max(p["date_added"] for p in raw_projects)


def test_requests_are_rate_limited(monkeypatch):
    sleeps = []
    monkeypatch.setattr("nihreporter_fetch.client.time.sleep", sleeps.append)
    client = client_for(fake_api())
    client.count(SPEC)
    client.count(SPEC)
    assert sleeps and 0 < sleeps[0] <= client.min_interval


@pytest.mark.parametrize("layout", ["v01", "document", "list", "jsonl"])
def test_iter_raw_reads_saved_projects(tmp_path, raw_projects, layout):
    path = tmp_path / "raw.json"
    if layout == "v01":  # nihreporter-fetch 0.1 output
        path.write_text(json.dumps({"meta": {"total": 16}, "results": raw_projects}))
    elif layout == "document":  # --raw JSON output
        path.write_text(json.dumps({"schema_version": 1, "search_info": {}, "projects": raw_projects}))
    elif layout == "list":
        path.write_text(json.dumps(raw_projects))
    else:
        path.write_text("".join(json.dumps(p) + "\n" for p in raw_projects))
    client = ReporterClient(session=FakeSession({}))
    projects = [p for b in client.iter_raw(path) for p in b]
    assert [p["appl_id"] for p in projects] == [p["appl_id"] for p in raw_projects]
    assert (client.search_info["source"], client.search_info["retrieved"]) == ("raw", 16)


def test_load_raw_projects_rejects_normalized_records_and_garbage(tmp_path, records):
    path = tmp_path / "normalized.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records.values()))
    with pytest.raises(ReporterError, match="normalized records"):
        load_raw_projects(path)
    path.write_text("not json\n")
    with pytest.raises(ReporterError, match="neither JSON nor JSONL"):
        load_raw_projects(path)
    path.write_text('{"something": "else"}')
    with pytest.raises(ReporterError, match="No projects"):
        load_raw_projects(path)


def test_ids_and_field_names():
    assert normalize_ids([" 42", 42, "7", ""]) == [42, 7]
    assert normalize_pmids(["33298401"]) == [33298401]
    with pytest.raises(ValueError, match="R01"):
        normalize_ids(["R01"])
    with pytest.raises(ValueError, match="PMID"):
        normalize_pmids(["-5"])
    assert raw_field_name("geo_lat_lon") == "GeoLatLon" and raw_field_name("PHRTEXT") == "PhrText"
    with pytest.raises(ValueError, match="Unknown project field"):
        raw_field_name("title")


def test_session_identifies_the_tool():
    assert make_session().headers["User-Agent"] == USER_AGENT
