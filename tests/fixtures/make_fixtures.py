"""
Regenerate projects_sample.json from the live API: uv run python tests/fixtures/make_fixtures.py

The projects cover edge cases: an R01 with a program officer (11124893), a center subproject
(11014863), a COVID-19 supplement with three PIs (10303004), an ARRA-funded RC2 from 2010
(8444852), a Canadian organization (11234869), a multi-PI R01 (11253759), a CDC-funded SBIR
(11361757), VA projects without award amounts (11076998, and 100336 whose project number is
just "2025"), a CDC center project with an M002 suffix (11624218), FDA and AHRQ projects
(11457555, 11292435), a P01 component without abstract or terms (11257477), a 1990 project
(588365), funding from two institutes (11052511) and five PIs (10915693). Publication links
are included for three of the core projects and one PMID.
"""

import json
from pathlib import Path

from nihreporter_fetch.client import ReporterClient

IDS = [
    11124893,
    11014863,
    10303004,
    8444852,
    11234869,
    11253759,
    11361757,
    11076998,
    100336,
    11624218,
    11457555,
    11292435,
    11257477,
    588365,
    11052511,
    10915693,
]
LINKED_CORES = ["R01AG088223", "R01AI192148", "R01MD013769"]
LINKED_PMIDS = [33298401]


def main() -> None:
    client = ReporterClient()
    page = client._search({"appl_ids": IDS}, 0, len(IDS))
    projects = sorted(page["results"], key=lambda p: IDS.index(p["appl_id"]))
    links = []
    for criteria in ({"core_project_nums": LINKED_CORES}, {"pmids": LINKED_PMIDS}):
        links.extend(client._post_page("publications/search", {"criteria": criteria, "limit": 500})["results"])
    target = Path(__file__).with_name("projects_sample.json")
    doc = {"projects": projects, "publication_links": links}
    target.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(projects)} projects and {len(links)} publication links to {target}")


if __name__ == "__main__":
    main()
