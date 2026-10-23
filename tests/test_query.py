import json
from datetime import date

import pytest

from nihreporter_fetch import ReporterError, resolve_date_range
from nihreporter_fetch.query import (
    AGENCIES,
    MAX_AWARD,
    QuerySpec,
    load_criteria,
    parse_fiscal_years,
    parse_name,
    parse_sort,
)


def test_build_criteria_maps_every_field():
    spec = QuerySpec(
        text="CAR T",
        text_fields=["title", "Abstract"],
        text_operator="OR",
        fiscal_years=["2023-2024", 2026],
        pi_names=["Corey, Lawrence", "Bloom"],
        pi_profile_ids=["1860036"],
        po_names=["Guo"],
        org_names=["FRED HUTCH*"],
        org_names_exact=["FRED HUTCHINSON CANCER CENTER"],
        org_cities=["seattle"],
        org_states=["wa"],
        org_countries=["United States"],
        project_nums=["r01ca*"],
        agencies=["nci", "niaid"],
        activity_codes=["r01", "U01"],
        application_types=["1", 2],
        opportunity_numbers=["par-21-123"],
        cong_dists=["wa-07"],
        spending_categories=[31, "132"],
        match_all_categories=True,
        covid_response=["c4", "reg-cv"],
        min_award=1_000_000,
        multi_pi=True,
        subprojects="exclude",
        newly_added=True,
        date_field="added",
        date_from=date(2024, 1, 1),
        date_to=date(2024, 6, 30),
        criteria={"project_start_date": {"from_date": "2020-01-01"}},
    )
    assert spec.build_criteria() == {
        "advanced_text_search": {"operator": "or", "search_field": "projecttitle,abstracttext", "search_text": "CAR T"},
        "fiscal_years": [2023, 2024, 2026],
        "pi_names": [{"last_name": "Corey", "first_name": "Lawrence"}, {"any_name": "Bloom"}],
        "po_names": [{"any_name": "Guo"}],
        "pi_profile_ids": [1860036],
        "org_names": ["FRED HUTCH*"],
        "org_names_exact_match": ["FRED HUTCHINSON CANCER CENTER"],
        "org_cities": ["SEATTLE"],
        # The API matches states case-sensitively.
        "org_states": ["WA"],
        "org_countries": ["UNITED STATES"],
        "project_nums": ["R01CA*"],
        "agencies": ["NCI", "NIAID"],
        "activity_codes": ["R01", "U01"],
        "award_types": ["1", "2"],
        "opportunity_numbers": ["PAR-21-123"],
        "cong_dists": ["WA-07"],
        "covid_response": ["C4", "Reg-CV"],
        "spending_categories": {"values": [31, 132], "match_all": True},
        "award_amount_range": {"min_amount": 1_000_000, "max_amount": MAX_AWARD},
        "multi_pi_only": True,
        "exclude_subprojects": True,
        "newly_added_projects_only": True,
        # The end date covers the whole day (records carry the time they were added).
        "date_added": {"from_date": "2024-01-01", "to_date": "2024-06-30T23:59:59"},
        "project_start_date": {"from_date": "2020-01-01"},
    }


def test_defaults():
    spec = QuerySpec(text="glioblastoma", date_to=date(2024, 12, 31))
    assert spec.build_criteria() == {
        "advanced_text_search": {
            "operator": "and",
            "search_field": "projecttitle,abstracttext,terms",
            "search_text": "glioblastoma",
        },
        "award_notice_date": {"to_date": "2024-12-31T23:59:59"},
    }
    assert QuerySpec().is_empty() and QuerySpec(text="  ").is_empty()
    assert QuerySpec(active=True).build_criteria() == {"include_active_projects": True}
    assert QuerySpec(subprojects="ONLY").build_criteria() == {"sub_project_only": True}
    assert QuerySpec(max_award=500).build_criteria() == {"award_amount_range": {"min_amount": 0, "max_amount": 500}}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"agencies": ["NCII"]}, "Unknown agency"),
        ({"text_fields": ["body"]}, "Unknown text field"),
        ({"text_operator": "xor"}, "Unknown text operator"),
        ({"activity_codes": ["R1"]}, "Activity codes"),
        ({"application_types": ["0"]}, "Unknown application type"),
        ({"covid_response": ["C9"]}, "Unknown COVID"),
        ({"subprojects": "some"}, "Unknown subprojects"),
        ({"fiscal_years": ["2024-2020"]}, "ascending"),
        ({"fiscal_years": ["FY2024"]}, "Expected a fiscal year"),
        ({"fiscal_years": [1970]}, "between 1985"),
        ({"active": True, "fiscal_years": [2024]}, "active can't be combined"),
        ({"min_award": 10, "max_award": 5}, "greater than"),
        ({"min_award": -1}, "between 0 and"),
        ({"max_award": 2**31}, "between 0 and 2,147,483,647"),
        ({"date_field": "posted"}, "date_field"),
        ({"date_from": date(2024, 2, 1), "date_to": date(2024, 1, 1)}, "after its end"),
        ({"criteria": {"fiscal_year": [2024]}}, "Unknown criteria: fiscal_year"),
    ],
)
def test_invalid_specs_are_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        QuerySpec(**kwargs)


def test_criteria_clashing_with_options_are_refused():
    spec = QuerySpec(fiscal_years=[2024], criteria={"fiscal_years": [2023]})
    with pytest.raises(ValueError, match="both as options and as raw criteria: fiscal_years"):
        spec.build_criteria()


def test_agency_list_has_no_duplicates():
    assert len(AGENCIES) == len(set(AGENCIES))
    assert QuerySpec(agencies=["allcdc", "NIH"]).agencies == ["ALLCDC", "NIH"]


def test_parse_fiscal_years():
    assert parse_fiscal_years(["2020-2022", "2021", 2024, " 2025..2025 "]) == [2020, 2021, 2022, 2024, 2025]
    assert parse_fiscal_years([]) == []


def test_parse_name():
    assert parse_name("  Corey,  Lawrence  ") == {"last_name": "Corey", "first_name": "Lawrence"}
    # contact_pi_name style, with a middle initial
    assert parse_name("PRIESS, JAMES R") == {"last_name": "PRIESS", "first_name": "JAMES"}
    assert parse_name("Cor*,") == {"last_name": "Cor*"}
    assert parse_name("Trevor  Bedford") == {"any_name": "Trevor Bedford"}


def test_parse_sort():
    assert parse_sort("award_amount:DESC") == ("award_amount", "desc")
    assert parse_sort(" fiscal_year ") == ("fiscal_year", "asc")
    # The API answers unsortable fields with a server error, so they're refused up front.
    with pytest.raises(ValueError, match="Can't sort on 'project_title'"):
        parse_sort("project_title")
    with pytest.raises(ValueError, match="asc or desc"):
        parse_sort("appl_id:up")


def test_filters_are_json_serializable():
    spec = QuerySpec(org_names=["X"], date_from=date(2024, 1, 1))
    filters = spec.filters()
    assert filters["date_from"] == "2024-01-01" and filters["org_names"] == ["X"]
    json.dumps(filters)


def test_load_criteria(tmp_path):
    assert load_criteria('{"org_names": ["X*"]}') == ({"org_names": ["X*"]}, {})
    # A whole 0.1-style request body
    body = tmp_path / "body.json"
    body.write_text('{"criteria": {"fiscal_years": [2023]}, "include_fields": ["ApplId"], "limit": 10}')
    assert load_criteria(f"@{body}") == ({"fiscal_years": [2023]}, {"include_fields": ["ApplId"], "limit": 10})
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_criteria("{oops")
    with pytest.raises(ValueError, match="JSON object"):
        load_criteria("[1, 2]")


def test_resolve_date_range():
    today = date(2024, 3, 31)
    assert resolve_date_range(30, today=today) == (date(2024, 3, 1), today)
    assert resolve_date_range(10, date_to=date(2024, 1, 11)) == (date(2024, 1, 1), date(2024, 1, 11))
    assert resolve_date_range(None, date(2024, 1, 1)) == (date(2024, 1, 1), None)
    assert resolve_date_range() == (None, None)
    with pytest.raises(ReporterError):
        resolve_date_range(None, date(2024, 2, 1), date(2024, 1, 1))
