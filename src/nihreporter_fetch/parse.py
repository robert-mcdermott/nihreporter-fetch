"""Convert NIH RePORTER API project objects into flat, schema-versioned project records."""

from __future__ import annotations

import re
from itertools import zip_longest
from typing import Any

# A normalized project record (see schema/output.schema.json, $defs/project), or a raw API project.
Project = dict[str, Any]

PROJECT_URL = "https://reporter.nih.gov/project-details/{appl_id}"
_TERM = re.compile(r"<([^<>]*)>")


def opt(value: Any) -> Any:
    """``value`` (stripped, if a string), or None if it is missing or blank."""
    if isinstance(value, str):
        value = value.strip()
    return None if value in (None, "") else value


def _name(value: Any) -> str | None:
    """A name with runs of whitespace collapsed ("MAX  GUO" -> "MAX GUO")."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _date(value: Any) -> str | None:
    """The date part of an API timestamp: "2024-07-01T00:00:00" -> "2024-07-01"."""
    value = opt(value)
    return value[:10] if isinstance(value, str) else None


def _amount(value: Any) -> int | float | None:
    """A dollar amount; whole numbers sent as floats (498790.0) become ints."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _flag(value: Any) -> bool | None:
    """A Y/N flag (or a boolean) as a boolean."""
    if isinstance(value, bool) or value is None:
        return value
    return {"Y": True, "N": False}.get(str(value).strip().upper())


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _terms(value: Any) -> list[str]:
    """Terms sent as "<term one><term two>"."""
    return _unique([t.strip() for t in _TERM.findall(value)]) if isinstance(value, str) else []


def _split(value: Any, sep: str = ";") -> list[str]:
    return _unique([t.strip() for t in value.split(sep)]) if isinstance(value, str) else []


def record_id(record: Project) -> int:
    """The application ID of a normalized record or a raw API project (the same field in both)."""
    return int(record["appl_id"])


def _person(p: dict[str, Any], pi: bool) -> dict[str, Any]:
    first, middle, last = _name(p.get("first_name")), _name(p.get("middle_name")), _name(p.get("last_name"))
    person: dict[str, Any] = {
        "full_name": _name(p.get("full_name")) or _name(" ".join(n for n in (first, middle, last) if n)),
        "first_name": first,
        "middle_name": middle,
        "last_name": last,
    }
    if pi:
        person = {"profile_id": p.get("profile_id"), **person, "title": opt(p.get("title"))}
        person["is_contact_pi"] = bool(p.get("is_contact_pi"))
    return person


def _organization(raw: Project) -> dict[str, Any] | None:
    org = raw.get("organization") or {}
    if not opt(org.get("org_name")):
        return None
    org_type = raw.get("organization_type") or {}
    geo = raw.get("geo_lat_lon") or {}
    ueis = [u for u in org.get("org_ueis") or [] if u]
    duns = [d for d in org.get("org_duns") or [] if d]
    return {
        "name": _name(org["org_name"]),
        "city": opt(org.get("org_city")) or opt(org.get("city")),
        "state": opt(org.get("org_state")),
        "country": opt(org.get("org_country")) or opt(org.get("country")),
        "zipcode": opt(org.get("org_zipcode")),
        "cong_dist": opt(raw.get("cong_dist")),
        "dept_type": opt(org.get("dept_type")),
        "type": opt(org_type.get("name")),
        "type_code": opt(org_type.get("code")),
        "uei": opt(org.get("primary_uei")) or (ueis[0] if ueis else None),
        "duns": opt(org.get("primary_duns")) or (duns[0] if duns else None),
        "ipf_code": opt(org.get("org_ipf_code")),
        "fips": opt(org.get("org_fips")),
        "external_org_id": org.get("external_org_id"),
        "latitude": geo.get("lat"),
        "longitude": geo.get("lon"),
    }


def _study_section(raw: Project) -> dict[str, Any] | None:
    section = raw.get("full_study_section") or {}
    if not (opt(section.get("srg_code")) or opt(section.get("name"))):
        return None
    return {
        "code": opt(section.get("srg_code")),
        "flex": opt(section.get("srg_flex")),
        "sra_designator_code": opt(section.get("sra_designator_code")),
        "sra_flex_code": opt(section.get("sra_flex_code")),
        "group_code": opt(section.get("group_code")),
        "name": opt(section.get("name")),
    }


def _spending_categories(raw: Project) -> list[dict[str, Any]]:
    # IDs come as a list and names as one "A; B" string, in the same (name) order.
    ids = [i for i in raw.get("spending_categories") or [] if i is not None]
    names = _split(raw.get("spending_categories_desc"))
    if ids and len(ids) != len(names):
        names = []
    return [{"id": i, "name": n} for i, n in zip_longest(ids, names)]


def parse_project(raw: Project) -> Project:
    """Normalize one project object from the API. Missing values are None (or empty lists)."""
    appl_id = int(raw["appl_id"])
    split = raw.get("project_num_split") or {}
    admin = raw.get("agency_ic_admin") or {}
    return {
        "appl_id": appl_id,
        "project_num": opt(raw.get("project_num")),
        "core_project_num": opt(raw.get("core_project_num")),
        "subproject_id": opt(raw.get("subproject_id")),
        "fiscal_year": raw.get("fiscal_year"),
        "url": opt(raw.get("project_detail_url")) or PROJECT_URL.format(appl_id=appl_id),
        "project_title": opt(raw.get("project_title")),
        "application_type": opt(split.get("appl_type_code")) or opt(raw.get("award_type")),
        "activity_code": opt(raw.get("activity_code")) or opt(split.get("activity_code")),
        "ic_code": opt(split.get("ic_code")),
        "serial_num": opt(split.get("serial_num")),
        "support_year": opt(split.get("support_year")),
        "suffix_code": opt(split.get("suffix_code")),
        "funding_mechanism": opt(raw.get("funding_mechanism")),
        "mechanism_code_dc": opt(raw.get("mechanism_code_dc")),
        "cfda_code": opt(raw.get("cfda_code")),
        "opportunity_number": opt(raw.get("opportunity_number")),
        "agency_code": opt(raw.get("agency_code")),
        "admin_ic": (
            {
                "code": opt(admin.get("code")),
                "abbreviation": opt(admin.get("abbreviation")),
                "name": opt(admin.get("name")),
            }
            if admin
            else None
        ),
        "ic_fundings": [
            {
                "fiscal_year": f.get("fy"),
                "code": opt(f.get("code")),
                "abbreviation": opt(f.get("abbreviation")),
                "name": opt(f.get("name")),
                "total_cost": _amount(f.get("total_cost")),
                "direct_cost": _amount(f.get("direct_cost_ic")),
                "indirect_cost": _amount(f.get("indirect_cost_ic")),
            }
            for f in raw.get("agency_ic_fundings") or []
        ],
        "award_amount": _amount(raw.get("award_amount")),
        "direct_cost": _amount(raw.get("direct_cost_amt")),
        "indirect_cost": _amount(raw.get("indirect_cost_amt")),
        "award_notice_date": _date(raw.get("award_notice_date")),
        "budget_start": _date(raw.get("budget_start")),
        "budget_end": _date(raw.get("budget_end")),
        "project_start_date": _date(raw.get("project_start_date")),
        "project_end_date": _date(raw.get("project_end_date")),
        "date_added": opt(raw.get("date_added")),
        "is_active": raw.get("is_active"),
        "is_new": raw.get("is_new"),
        "arra_funded": _flag(raw.get("arra_funded")),
        "covid_response": [c for c in raw.get("covid_response") or [] if c],
        "contact_pi_name": _name(raw.get("contact_pi_name")),
        "principal_investigators": [_person(p, pi=True) for p in raw.get("principal_investigators") or []],
        "program_officers": [_person(p, pi=False) for p in raw.get("program_officers") or []],
        "organization": _organization(raw),
        "study_section": _study_section(raw),
        "spending_categories": _spending_categories(raw),
        "pref_terms": _split(raw.get("pref_terms")),
        "terms": _terms(raw.get("terms")),
        "abstract_text": opt(raw.get("abstract_text")),
        "phr_text": opt(raw.get("phr_text")),
        "publications": None,
    }
