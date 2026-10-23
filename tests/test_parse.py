from nihreporter_fetch import parse_project, record_id
from nihreporter_fetch.parse import opt


def test_regular_project(records):
    p = records[11124893]
    assert (p["project_num"], p["core_project_num"], p["subproject_id"], p["fiscal_year"]) == (
        "5R01AG088223-02",
        "R01AG088223",
        None,
        2025,
    )
    assert p["url"] == "https://reporter.nih.gov/project-details/11124893"
    assert (p["application_type"], p["activity_code"], p["ic_code"], p["serial_num"], p["support_year"]) == (
        "5",
        "R01",
        "AG",
        "088223",
        "02",
    )
    # Empty strings become None, dates lose their T00:00:00, and Y/N flags become booleans.
    assert p["suffix_code"] is None and p["arra_funded"] is False
    assert (p["project_start_date"], p["project_end_date"], p["award_notice_date"]) == (
        "2024-07-01",
        "2028-06-30",
        "2025-07-31",
    )
    assert p["admin_ic"] == {"code": "AG", "abbreviation": "NIA", "name": "National Institute on Aging"}
    assert p["award_amount"] == p["direct_cost"] + p["indirect_cost"] == p["ic_fundings"][0]["total_cost"]
    # Amounts the API sends as floats (498790.0) are integers.
    assert isinstance(p["ic_fundings"][0]["total_cost"], int)
    [pi] = p["principal_investigators"]
    assert pi == {
        "profile_id": 1860036,
        "full_name": "JAMES R PRIESS",
        "first_name": "JAMES",
        "middle_name": "R",
        "last_name": "PRIESS",
        "title": None,
        "is_contact_pi": True,
    }
    assert p["organization"]["name"] == "FRED HUTCHINSON CANCER CENTER"
    assert (p["organization"]["city"], p["organization"]["state"], p["organization"]["cong_dist"]) == (
        "SEATTLE",
        "WA",
        "WA-07",
    )
    assert p["study_section"]["code"] == "CMAD"
    assert p["publications"] is None


def test_names_collapse_whitespace(raw_projects):
    raw = raw_projects[0]
    raw["program_officers"] = [{"first_name": "MAX", "middle_name": "", "last_name": "GUO", "full_name": "MAX  GUO"}]
    raw["contact_pi_name"] = "PRIESS,  JAMES R "
    p = parse_project(raw)
    assert p["program_officers"] == [
        {"full_name": "MAX GUO", "first_name": "MAX", "middle_name": None, "last_name": "GUO"}
    ]
    assert p["contact_pi_name"] == "PRIESS, JAMES R"


def test_terms_and_spending_categories(raw_projects, records):
    raw, p = raw_projects[0], records[11124893]
    assert p["terms"][:3] == ["21+ years old", "3-D", "3-Dimensional"] and len(p["terms"]) > 100
    assert "<" not in "".join(p["terms"])
    assert p["pref_terms"] == raw["pref_terms"].split(";")
    # Category IDs and names come separately; they pair up in order.
    names = raw["spending_categories_desc"].split("; ")
    assert p["spending_categories"] == [
        {"id": i, "name": n} for i, n in zip(raw["spending_categories"], names, strict=True)
    ]
    assert p["spending_categories"][0] == {"id": 31, "name": "Aging"}


def test_mismatched_spending_categories_keep_ids(raw_projects):
    raw = raw_projects[0]
    raw["spending_categories_desc"] = "Aging"
    assert [c["name"] for c in parse_project(raw)["spending_categories"]] == [None] * len(raw["spending_categories"])


def test_subproject(records):
    p = records[11014863]
    assert (p["subproject_id"], p["core_project_num"], p["activity_code"], p["application_type"]) == (
        "7094",
        "P30CA015704",
        "P30",
        "2",
    )


def test_covid_supplement_with_several_pis(records):
    p = records[10303004]
    assert (p["covid_response"], p["application_type"], p["suffix_code"]) == (["C4"], "3", "S1")
    assert len(p["principal_investigators"]) == 3
    assert [pi["last_name"] for pi in p["principal_investigators"] if pi["is_contact_pi"]] == ["Schoenthaler"]


def test_arra_project_from_2010(records):
    p = records[8444852]
    assert (p["arra_funded"], p["fiscal_year"], p["activity_code"]) == (True, 2010, "RC2")
    assert p["spending_categories"] == [] and p["terms"] == []


def test_foreign_organization(records):
    org = records[11234869]["organization"]
    assert (org["country"], org["state"], org["cong_dist"], org["type"]) == ("CANADA", "ON", None, "Unavailable")
    assert (org["latitude"], org["longitude"]) == (43.70011, -79.4163)


def test_va_projects_without_amounts(records):
    va = records[11076998]
    assert (va["agency_code"], va["admin_ic"]["abbreviation"], va["award_amount"], va["direct_cost"]) == (
        "VA",
        "VA",
        None,
        None,
    )
    # A project number that isn't one ("2025") has no parts.
    odd = records[100336]
    assert (odd["project_num"], odd["application_type"], odd["activity_code"], odd["ic_code"]) == (
        "2025",
        None,
        None,
        None,
    )
    assert odd["study_section"] is None


def test_other_agencies(records):
    cdc = records[11624218]
    assert (cdc["agency_code"], cdc["admin_ic"]["abbreviation"], cdc["suffix_code"]) == ("ALLCDC", "NCIPC", "M002")
    sbir = records[11361757]
    assert (sbir["agency_code"], sbir["admin_ic"]["abbreviation"], sbir["funding_mechanism"]) == (
        "ALLCDC",
        "NCEZID",
        "SBIR/STTR",
    )
    assert records[11457555]["agency_code"] == "FDA"
    assert records[11292435]["agency_code"] == "AHRQ"


def test_old_project(records):
    p = records[588365]
    assert (p["fiscal_year"], p["project_start_date"], p["date_added"]) == (1990, "1990-09-30", "2011-03-09T00:00:00")
    assert p["abstract_text"] is None and p["terms"] == []


def test_funding_from_two_institutes(records):
    p = records[11052511]
    assert [f["abbreviation"] for f in p["ic_fundings"]] == ["NIMH", "OD"]
    assert sum(f["total_cost"] for f in p["ic_fundings"]) == p["award_amount"]


def test_minimal_project_parses():
    p = parse_project({"appl_id": "42"})
    assert p["appl_id"] == 42 and p["url"].endswith("/42")
    assert (p["organization"], p["admin_ic"], p["study_section"]) == (None, None, None)
    assert p["principal_investigators"] == p["terms"] == p["covid_response"] == []


def test_record_id_and_opt(raw_projects, records):
    assert record_id(raw_projects[0]) == record_id(records[11124893]) == 11124893
    assert opt("  ") is None and opt(" x ") == "x" and opt(0) == 0
