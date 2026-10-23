# NIH RePORTER Fetch

A CLI and Python library that searches [NIH RePORTER](https://reporter.nih.gov) (API v2) for NIH-funded research projects, and exports structured, schema-versioned project records as JSON, JSONL, CSV or SQLite, ready for downstream analysis. Each record includes the project number, title and abstract, fiscal year, activity code and application type, award amounts and funding institutes, project and budget dates, principal investigators and program officers, the organization with its location and identifiers, study section, RCDC spending categories and terms, and, on request, linked PubMed IDs.

A sister tool to [pubmed-fetch](https://github.com/robert-mcdermott/pubmed-fetch) and [trials-fetch](https://github.com/robert-mcdermott/trials-fetch), with the same options, output conventions and agent skill.

## Requirements
- Python 3.10+ (development uses 3.13, see .python-version)
- uv package manager

## Install
```bash
uv sync
```
This installs the `nihreporter-fetch` command. Run it with `uv run nihreporter-fetch ...` (or `uv run -m nihreporter_fetch ...`). To install it globally, run `uv tool install .`; to run it without installing, use `uvx --from git+https://github.com/robert-mcdermott/nihreporter-fetch nihreporter-fetch ...`.

To use it as a library from another project: `uv add git+https://github.com/robert-mcdermott/nihreporter-fetch` (see [Library use](#library-use)).

## Quick start
```bash
# NCI-funded CAR T projects in fiscal year 2025
uv run nihreporter-fetch "CAR T" --fiscal-year 2025 --agency NCI -o car_t.json

# How many projects match, without fetching them
uv run nihreporter-fetch --org "FRED HUTCH*" --fiscal-year 2025 --count

# Fred Hutch awards with a notice date in the last two years, into SQLite
uv run nihreporter-fetch --org "FRED HUTCH*" --days 730 --max-results all -o fred_hutch.sqlite

# A PI's projects, with the PubMed IDs of their publications
uv run nihreporter-fetch --pi "Corey, Lawrence" --fiscal-year 2020-2025 --publications -o corey.json

# Every R01 and R21 in Washington state for three fiscal years, as CSV
uv run nihreporter-fetch --activity-code R01 R21 --org-state WA --fiscal-year 2023-2025 --max-results all -o wa.csv

# Every NIH project of fiscal year 2025 (over 80,000; split automatically past RePORTER's 15,000 limit)
uv run nihreporter-fetch --fiscal-year 2025 --max-results all -o fy2025.sqlite

# Which grants funded these papers?
uv run nihreporter-fetch --pmids 33298401 34320281 -o funding.json

# Specific projects by application ID, piped to another tool
uv run nihreporter-fetch --appl-ids 11124893 11014863 -o - -q | jq '.projects[] | {project_num, project_title, award_amount}'

# Weekly monitoring into a SQLite database (after the first run, only projects added since the last run)
uv run nihreporter-fetch --org "FRED HUTCH*" --max-results all --since-last-run fh.state.json -o fred_hutch.sqlite
```

## Searching
Several values for one option are ORed (`--agency NCI NIAID`); different options are ANDed. Names, organizations and project numbers accept `*` wildcards. Put the positional search text first, before options that take several values. The exact API criteria sent are recorded in `search_info.criteria`.

| Option | Searches |
| --- | --- |
| `TEXT` (positional) | project titles, abstracts and terms (`--text-field title abstract terms` to choose) |
| `--text-operator` | how the words of `TEXT` combine: `and` (all, the default), `or` (any), or `advanced`, a boolean expression such as `'"CAR T" AND (glioblastoma OR glioma)'` |
| `--pi NAME` | principal investigators: `"Last, First"` (middle names and initials after the first name are ignored) or any part of a name, e.g. `Bedford` or `"Trevor Bedford"` |
| `--pi-profile-id ID` | PIs by their RePORTER profile ID (`principal_investigators[].profile_id`), which avoids name ambiguity |
| `--po NAME` | program officers, like `--pi` |
| `--org NAME` | organizations, by partial name: `"FRED HUTCH*"` matches FRED HUTCHINSON CANCER CENTER and its former names |
| `--org-exact NAME` | organizations, by exact name |
| `--org-city`, `--org-state`, `--org-country` | where the organization is: `SEATTLE`, `WA` (two-letter code), `CANADA` |
| `--project-num NUM` | project numbers: `R01CA*` (any R01 from NCI), `5U54CA*`, `R01AG088223` |

## Filters
Values are case-insensitive.

| Option | Values |
| --- | --- |
| `--fiscal-year` | years and ranges: `2025`, `2020-2024` |
| `--agency` | the administering institute, center or agency: NIH's `NCI`, `NIAID`, `NHLBI`, `NIGMS`, `NIDDK`, `NINDS`, `NIMH`, `NICHD`, `NIA`, `NIDA`, `NIEHS`, `NEI`, `NIAMS`, `NIAAA`, `NIDCD`, `NIDCR`, `NINR`, `NHGRI`, `NIBIB`, `NIMHD`, `NCCIH`, `NCATS`, `NLM`, `FIC`, `OD`, `CLC` (Clinical Center), and former `NCRR`, `CSR`; CDC's `NCBDDD`, `NCCDPHP`, `NCEH`, `NCEZID`, `NCHHSTP`, `NCHS`, `NCIPC`, `NCIRD`, `NIOSH`, `CGH`, `ATSDR`, and former `CID`, `NCHM`, `ODCDC`, `PHPPO`; `AHRQ`, `FDA`, `VA`, `SAMHSA`; HRSA's former divisions `DADHP`, `DDA`, `DM,BHP`, `DN`; `OMHHE`; or `NIH`, `ALLCDC`, `HRSA` for a whole agency |
| `--activity-code` | `R01`, `R21`, `K99`, `U54`, `P30`, `T32`, `F31`, `R44`, ... |
| `--application-type` | `1` new, `2` competing renewal, `3` supplement, `4` extension, `5` non-competing continuation, `6`-`9` changes of organization, grantee or institute |
| `--opportunity-number` | the funding opportunity, e.g. `PAR-21-123`, `PA-20-185` |
| `--cong-dist` | the organization's congressional district, e.g. `WA-07` |
| `--spending-category ID` | RCDC spending category IDs (`spending_categories[].id` in the output, e.g. 132 Cancer); `--match-all-categories` requires all of them |
| `--covid-response` | COVID-19 funding: `Reg-CV` (regular appropriations), `CV`, `C3`, `C4`, `C5`, `C6` (supplemental appropriations acts) |
| `--min-award`, `--max-award` | award amount (total cost for the fiscal year), in dollars |
| `--active` | currently active projects (instead of `--fiscal-year`: RePORTER would return the projects of those years *plus* every active project) |
| `--multi-pi` | projects with more than one PI |
| `--no-subprojects` / `--subprojects-only` | leave out, or keep only, the components of center and program grants (P01, P30, U54, ...) |
| `--newly-added` | projects added in RePORTER's latest weekly update |
| `--criteria JSON` | any other [API criteria](https://api.reporter.nih.gov), as JSON or `@file`, e.g. `'{"project_start_date": {"from_date": "2020-01-01"}}'`. Names are checked, since RePORTER silently ignores unknown criteria. |

**Dates.** `--date-from` and `--date-to` (YYYY-MM-DD; either can be left open) or `--days N` (the N days up to `--date-to`, default today) filter on one date, chosen with `--date-field`:
- `award_notice` (default): when the award was issued
- `project_start`, `project_end`: the project period (all years of the grant)
- `added`: when the record was added to RePORTER (best for monitoring; the default with `--since-last-run`)

Use `--criteria` for a second date range, e.g. projects ending after 2026 with awards issued in 2025.

**How many.** `--max-results` (default 1000) caps the number of projects fetched; `--max-results all` removes the cap. When more match, a warning says so and `search_info.truncated` is true. `--count` prints the number of matches and exits. `--sort FIELD[:asc|desc]` orders the results by `appl_id` (the default), `fiscal_year`, `award_amount`, `direct_cost_amt`, `indirect_cost_amt`, `award_notice_date`, `date_added`, `project_start_date`, `project_end_date`, `budget_start`, `budget_end`, `core_project_num`, `activity_code`, `agency_code`, `org_name`, `contact_pi_name`, `cong_dist` or `funding_mechanism`, e.g. `--sort award_amount:desc --max-results 20` for the 20 largest awards.

**More than 15,000 matches.** RePORTER serves at most 15,000 records of a search. Larger searches are split automatically: by fiscal year, then (for a year still over the limit) by administering institute or agency, with CDC and HRSA also fetched whole to catch centers whose codes RePORTER doesn't publish. Records found twice are kept once. `search_info.subsearches` says how many searches were run. With `--sort`, results are then sorted within each part rather than overall. Splitting a search with no fiscal years first finds the range of years, and counting each year and institute costs one request each, so very large searches start with a minute of counting. The parts cover all but a handful of projects (all of FY2023's 85,133; 76,385 of FY2025's 76,387; a few hundred in some older years), which carry codes no search reaches. When a split leaves projects out, a warning says how many and `search_info.truncated` is true.

## Inputs other than a search
- `--appl-ids ID|FILE ...`: fetch specific projects by application ID. Give IDs, files listing them (whitespace/comma separated, `#` comments allowed), or `-` for stdin. IDs that don't exist are listed in `search_info.missing_ids`.
- `--pmids ID|FILE ...`: fetch the projects that publications are linked to (one record per linked core project, the year RePORTER links it to), with their `publications`. PMIDs without a linked project are listed in `search_info.missing_ids`.
- `--from-raw FILE`: convert raw projects saved earlier with `--raw` (or the JSON written by nihreporter-fetch 0.1) without contacting RePORTER, e.g. `--from-raw fred_hutch_last2y.json -o fred_hutch.sqlite`.

## Output
`-o/--output FILE` (or `-` for stdout); the format comes from `--format` or the extension:

| Format | Extension | Notes |
| --- | --- | --- |
| `json` | `.json` | One document: `schema_version`, `search_info`, `projects`. Written atomically at the end. |
| `jsonl` | `.jsonl`, `.ndjson` | One project per line, written as pages arrive. |
| `csv` | `.csv` | One flattened row per project; lists joined with `; `. Written as pages arrive. |
| `sqlite` | `.sqlite`, `.db` | Normalized tables (below). Records are upserted by application ID, so repeated runs accumulate. |

- `--publications` adds `publications`, the PubMed IDs linked to each project's core project (all its fiscal years; for subprojects, the parent grant's). It costs one request per 100 core projects, and links of core projects already looked up are reused within a run.
- `--raw` writes the API's own project objects unchanged (JSON or JSONL). `--fields` limits raw output to some API fields, in either spelling (`--fields ProjectTitle award_amount`).
- `--merge` updates an existing file, replacing records with the same application ID (json, jsonl, csv).
- `--resume` continues an interrupted run, skipping application IDs already in the output and appending the rest (jsonl, csv, sqlite, json). It still downloads them; it only saves rewriting them.
- `--summary FILE` writes the run's `search_info` as JSON (`-` for stdout), which is handy for orchestration tools.

Progress and diagnostics go to stderr (`-q` for warnings and errors only, `-v` for debug output). The human-readable listing goes to stdout unless `-q` is given or the data itself is written to stdout.

### Exit codes
| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Error (bad options, invalid criteria, search failed, nothing could be fetched) |
| 2 | Partial results: a page or batch failed after retries (details in `search_info.failed_batches`; `--since-last-run` state is not advanced, so the next run retries), or some publications couldn't be looked up (`search_info.failed_publications`; those projects have `publications: null`). Output is written with `search_info.complete = false`. |

### Record format (schema version 1)
The JSON Schema is in [`src/nihreporter_fetch/schema/output.schema.json`](src/nihreporter_fetch/schema/output.schema.json) (project records are `$defs/project`) and is available in Python via `nihreporter_fetch.schema.load_schema()`. Missing values are `null` and missing lists are empty, never placeholder strings. Dates are `YYYY-MM-DD`.

A record is one project (or subproject) in one fiscal year: a five-year R01 has five records, with different application IDs and the same `core_project_num`. Each record includes:
- Identification: `appl_id` (application ID, unique per record), `project_num` (e.g. `5R01AG088223-02`), `core_project_num` (`R01AG088223`), `subproject_id`, `fiscal_year`, `url` (the RePORTER page)
- Classification, from the project number: `application_type`, `activity_code`, `ic_code`, `serial_num`, `support_year`, `suffix_code` (e.g. `S1` for a supplement); and `funding_mechanism`, `mechanism_code_dc`, `cfda_code`, `opportunity_number`
- Agency: `agency_code` (`NIH`, `ALLCDC`, `AHRQ`, `FDA`, `VA`, and in older years `HRSA` and `SAMSHA`, RePORTER's spelling), `admin_ic` (`code`, `abbreviation`, `name` of the administering institute), `ic_fundings` (per funding institute: `fiscal_year`, `code`, `abbreviation`, `name`, `total_cost`, `direct_cost`, `indirect_cost`)
- Money: `award_amount` (total cost for the fiscal year; `null` for VA intramural projects), `direct_cost`, `indirect_cost`
- Dates: `award_notice_date`, `budget_start`, `budget_end`, `project_start_date`, `project_end_date`, `date_added` (a timestamp: when the record was added to RePORTER)
- Flags: `is_active`, `is_new`, `arra_funded`, `covid_response` (codes, see `--covid-response`)
- People: `contact_pi_name`, `principal_investigators` (`profile_id`, `full_name`, `first_name`, `middle_name`, `last_name`, `title`, `is_contact_pi`), `program_officers` (`full_name`, `first_name`, `middle_name`, `last_name`)
- `organization`: `name`, `city`, `state`, `country`, `zipcode`, `cong_dist`, `dept_type`, `type` (e.g. Domestic Higher Education), `type_code`, `uei`, `duns`, `ipf_code`, `fips`, `external_org_id`, `latitude`, `longitude`
- `study_section` (the review group): `code`, `flex`, `sra_designator_code`, `sra_flex_code`, `group_code`, `name`
- Topic: `spending_categories` (RCDC categories: `id`, `name`), `pref_terms` (RePORTER's preferred terms), `terms` (all terms, with synonyms)
- Text: `project_title`, `abstract_text`, `phr_text` (public health relevance statement)
- `publications`: sorted PMIDs linked to the core project, or `null` unless `--publications` (or `--pmids`) was used

`search_info` records provenance and completeness: `tool` (name, version), `generated_on` (UTC), `api`, `data_timestamp` (when the newest project was added to RePORTER, which updates weekly), `source` (`search`, `ids`, `pmids` or `raw`), `criteria` (the API criteria), `filters`, `sort`, `raw`, `total_found`, `max_results`, `expected`, `retrieved`, `truncated`, `subsearches`, `complete`, `failed_batches`, `failed_publications`, `missing_ids`, `skipped_records`, `skipped_existing`, `with_publications`, and `total_projects`.

### SQLite tables
| Table | One row per | Columns |
| --- | --- | --- |
| `projects` | project record (primary key `appl_id`) | `project_num`, `core_project_num`, `subproject_id`, `fiscal_year`, `project_title`, `application_type`, `activity_code`, `ic_code`, `serial_num`, `support_year`, `suffix_code`, `funding_mechanism`, `mechanism_code_dc`, `cfda_code`, `opportunity_number`, `agency_code`, `admin_ic` (abbreviation), `admin_ic_name`, `award_amount`, `direct_cost`, `indirect_cost`, the dates, `is_active`, `is_new`, `arra_funded`, `covid_response` (joined), `contact_pi_name`, `org_name`, `org_city`, `org_state`, `org_country`, `org_zipcode`, `org_type`, `org_dept_type`, `org_uei`, `org_duns`, `org_ipf_code`, `cong_dist`, `latitude`, `longitude`, `study_section_code`, `study_section`, `abstract_text`, `phr_text`, `publication_count`, `url`, `record_json` (the full record), `updated_at` |
| `principal_investigators` | PI | `appl_id`, `profile_id`, `full_name`, `first_name`, `middle_name`, `last_name`, `title`, `is_contact_pi` |
| `program_officers` | program officer | `appl_id`, `full_name`, `first_name`, `middle_name`, `last_name` |
| `ic_fundings` | funding institute | `appl_id`, `fiscal_year`, `code`, `abbreviation`, `name`, `total_cost`, `direct_cost`, `indirect_cost` |
| `spending_categories` | RCDC category | `appl_id`, `category_id`, `name` |
| `terms` | preferred term | `appl_id`, `term` |
| `publications` | publication of a core project | `core_project_num`, `pmid` (filled by runs with `--publications`) |
| `runs` | run of nihreporter-fetch | `finished_at`, `search_info` (JSON) |

Child tables have one row per item, so use `COUNT(DISTINCT appl_id)` when counting projects. Funding questions usually want sums over `projects` (one row per award and year); `ic_fundings` splits co-funded awards by institute.

### Example: build and query a funding database
Collect every Fred Hutchinson project RePORTER has (fiscal years 1985-2026), with linked publications. In October 2026 this took under 7 minutes for 14,311 projects and 46,282 publication links, and made a 285 MB database:
```bash
uv run nihreporter-fetch --org "FRED HUTCH*" --max-results all --publications -o fred_hutch.sqlite
```

Then query it with the `sqlite3` command-line tool (preinstalled on macOS and most Linux systems), e.g. `sqlite3 -header -column fred_hutch.sqlite "<query>"`:

```sql
-- Funding by fiscal year (subproject amounts are part of their parent award, so leave them out)
SELECT fiscal_year, COUNT(*) AS awards, ROUND(SUM(award_amount) / 1e6, 1) AS million_usd
FROM projects WHERE subproject_id IS NULL GROUP BY fiscal_year ORDER BY fiscal_year DESC LIMIT 10;

-- Funding by institute, for one year (co-funded awards split by institute)
SELECT abbreviation, ROUND(SUM(total_cost) / 1e6, 1) AS million_usd
FROM ic_fundings f JOIN projects p USING (appl_id)
WHERE p.fiscal_year = 2025 AND p.subproject_id IS NULL GROUP BY abbreviation ORDER BY million_usd DESC;

-- Activity codes in one year
SELECT activity_code, COUNT(*) AS awards, ROUND(SUM(award_amount) / 1e6, 1) AS million_usd
FROM projects WHERE fiscal_year = 2025 AND subproject_id IS NULL
GROUP BY activity_code ORDER BY million_usd DESC LIMIT 15;

-- PIs with the most funding over the last three fiscal years (each PI of a multi-PI award gets its full amount)
SELECT pi.full_name, COUNT(DISTINCT p.core_project_num) AS grants, ROUND(SUM(p.award_amount) / 1e6, 1) AS million_usd
FROM projects p JOIN principal_investigators pi USING (appl_id)
WHERE p.fiscal_year >= 2023 AND p.subproject_id IS NULL
GROUP BY pi.profile_id ORDER BY million_usd DESC LIMIT 10;

-- New awards (type 1) and competing renewals (type 2) per year
SELECT fiscal_year, SUM(application_type = '1') AS new, SUM(application_type = '2') AS renewals
FROM projects WHERE subproject_id IS NULL GROUP BY fiscal_year ORDER BY fiscal_year DESC LIMIT 10;

-- RCDC spending categories, by projects in the latest year
SELECT name, COUNT(DISTINCT appl_id) AS projects
FROM spending_categories JOIN projects USING (appl_id)
WHERE fiscal_year = 2025 GROUP BY name ORDER BY projects DESC LIMIT 15;

-- Projects mentioning a topic
SELECT DISTINCT p.project_num, p.project_title
FROM projects p JOIN terms t USING (appl_id)
WHERE t.term = 'Immunotherapy' AND p.fiscal_year = 2025;

-- Grants ending in the next year (renewal pipeline)
SELECT core_project_num, contact_pi_name, project_end_date, project_title
FROM projects WHERE is_active AND subproject_id IS NULL AND project_end_date < date('now', '+1 year')
GROUP BY core_project_num ORDER BY project_end_date;

-- Most productive grants, by linked publications
SELECT core_project_num, COUNT(*) AS publications,
       (SELECT project_title FROM projects p WHERE p.core_project_num = pub.core_project_num
        AND p.subproject_id IS NULL ORDER BY fiscal_year DESC LIMIT 1) AS title
FROM publications pub GROUP BY core_project_num ORDER BY publications DESC LIMIT 10;

-- Study sections reviewing the most new awards
SELECT study_section, COUNT(*) AS awards
FROM projects WHERE application_type = '1' AND study_section IS NOT NULL
GROUP BY study_section ORDER BY awards DESC LIMIT 10;

-- Program officers of a year's awards, from record_json
SELECT json_extract(po.value, '$.full_name') AS program_officer, COUNT(*) AS awards
FROM projects, json_each(record_json, '$.program_officers') AS po
WHERE fiscal_year = 2025 GROUP BY program_officer ORDER BY awards DESC LIMIT 10;

-- History of the runs that built the database
SELECT run_id, finished_at,
       json_extract(search_info, '$.total_found') AS found,
       json_extract(search_info, '$.total_projects') AS in_database,
       json_extract(search_info, '$.data_timestamp') AS data_as_of
FROM runs;
```
Fields that aren't columns can be read from `record_json` with SQLite's JSON functions, as in the program officers query.

The same database loads straight into pandas (here run ad hoc with `uv run --with pandas`, without adding it to the project):
```bash
uv run --with pandas python - <<'EOF'
import sqlite3
import pandas as pd

db = sqlite3.connect("fred_hutch.sqlite")
awards = pd.read_sql(
    "SELECT fiscal_year, admin_ic, award_amount FROM projects WHERE fiscal_year >= 2021 AND subproject_id IS NULL", db
)
print(awards.pivot_table(index="admin_ic", columns="fiscal_year", values="award_amount", aggfunc="sum").round(-3).fillna(0))
EOF
```

To keep the database current, run the search on a schedule with `--since-last-run` (see [Incremental runs](#incremental-runs)).

### Linking to PubMed
`publications` holds the PubMed IDs of a project's publications. They can be fetched with [pubmed-fetch](https://github.com/robert-mcdermott/pubmed-fetch), e.g. for abstracts and citation metrics:
```bash
uv run nihreporter-fetch --project-num R01AI165821 --publications -o - -q \
  | jq -r '.projects[0].publications[]' \
  | pubmed-fetch --pmids - --icite --output bedford-papers.jsonl
```
In the other direction, `--pmids` finds the grants that funded papers, e.g. a list of PMIDs from a pubmed-fetch search.

## Incremental runs
`--since-last-run STATE` fetches only projects added to RePORTER (`added`, unless `--date-field` says otherwise) since the end date stored in the STATE file, then updates the file after a complete run. RePORTER adds projects in weekly updates, including the new fiscal year's record of continuing grants. The first run has no date limit (unless you give `--days`), so it collects everything that currently matches as a baseline.

- **Write to SQLite, or use `--merge`,** so the one-day overlap between runs (which ensures nothing added late on the last day is missed) is de-duplicated. Don't use `--resume` here: it skips records already in the output.
- **Use `--max-results all`** (or a cap above the number of matches). The state is not advanced when a page failed or when not every match was retrieved, since that would skip projects, so a capped search would fetch the same window on every run.
- RePORTER has no "last modified" date, so incremental runs don't pick up later corrections to records already fetched (rare for past fiscal years). Rebuild the database from scratch occasionally, or re-fetch recent fiscal years with `--fiscal-year`, to refresh them.
- Use `--publications` on every run if the database uses them; records fetched without it keep the publication links already stored. Publications that couldn't be looked up (`failed_publications`) don't hold the state back.

`--since-last-run` can't be combined with `--date-from`; to limit projects by another date as well, use `--criteria`, e.g. `'{"project_end_date": {"from_date": "2026-01-01"}}'`.

## Using a config file (INI)
All options can go in a `[search]` section; see [sample_config.ini](sample_config.ini), or generate one with `uv run nihreporter-fetch --create-config my_config.ini` (it won't overwrite an existing file). Lists are comma-separated and/or one per line; quote values that contain commas (such as `"Last, First"` names):
```ini
[search]
org_names = FRED HUTCH*
pi_names = "Corey, Lawrence", "Gilbert, Peter"
fiscal_years = 2022-2025
activity_codes =
    R01
    U01
max_results = all
publications = yes
output_file = fh.sqlite
```
Run it with `uv run nihreporter-fetch --config my_config.ini`. Unknown keys are reported, so typos don't go unnoticed. Command-line options override the config file, e.g. `--config my_config.ini --agency NCI`.

## Library use
```python
from nihreporter_fetch import Options, QuerySpec, ReporterClient, run

client = ReporterClient()

# Records in memory
projects = client.search_projects(org_names=["FRED HUTCH*"], fiscal_years=[2025], activity_codes=["R01"])
for project in projects:
    print(project["project_num"], project["award_amount"], project["project_title"])

# Streamed page by page, for large searches (split automatically past 15,000 matches)
spec = QuerySpec(agencies=["NCI"], fiscal_years=["2020-2024"], activity_codes=["R01"])
print(client.count(spec), "matches")
for page in client.iter_search(spec, max_results=None, sort="award_amount:desc"):
    client.attach_publications(page)  # optional: adds each project's linked PMIDs
    ...
print(client.search_info["complete"])

# Specific projects, one project, the projects behind some papers, or raw links
projects = client.fetch_projects([11124893, 11014863])
project = client.get_project(11124893)  # None if it doesn't exist
funders = [p for page in client.iter_pmids([33298401]) for p in page]
links = client.publication_links(core_project_nums=["R01AI165821"])  # [{"pmid", "core_project_num", "appl_id"}]

# The full pipeline, same as the CLI: writes the output file and returns the run summary
result = run(
    Options(spec=QuerySpec(org_names=["FRED HUTCH*"]), max_results=None, output_file="fh.sqlite", display=False)
)
print(result.exit_code, result.search_info["total_projects"])
```
`QuerySpec` validates and normalizes its values (raising `ValueError`), so `agencies=["nci"]`, `fiscal_years=["2020-2024"]` and `org_states=["wa"]` work. Errors from the API raise `ReporterError` with the API's explanation. `parse_project()` converts a raw API project into a record, and `nihreporter_fetch.schema.project_schema()` returns the JSON Schema for one record. The package doesn't configure logging; it logs to the `nihreporter_fetch` logger.

## Using nihreporter-fetch from AI coding agents (agent skill)
[`skills/nihreporter-fetch/`](skills/nihreporter-fetch/) is an agent skill in the open [Agent Skills](https://agentskills.io) format, which Claude Code, OpenAI Codex, Gemini CLI, GitHub Copilot, Cursor, Devin and other coding agents support. It teaches an agent to use this tool well:
- run it, with no install needed
- check the match count before collecting
- turn requests into the right search fields and filters (and avoid RePORTER's silent pitfalls)
- pick an output format and confirm the results are complete
- read results with `jq`/`sqlite3` instead of loading whole files

`SKILL.md` holds the workflow and worked examples; `reference.md` holds the record fields, SQLite tables and example queries, and is loaded only when needed.

**Requirements:** the agent must be able to run shell commands, with [uv](https://docs.astral.sh/uv/) installed and network access to api.reporter.nih.gov. The skill runs the tool with `uvx --from git+https://github.com/robert-mcdermott/nihreporter-fetch nihreporter-fetch` when it isn't installed. `jq` and `sqlite3` are used for reading results.

### Install with a skills installer
Either of these copies the skill into the right folder for the agents you choose, at project or personal scope:
```bash
# skills CLI (Node.js 18+); supports Claude Code, Codex, Cursor, Copilot, Gemini CLI and more
npx skills add robert-mcdermott/nihreporter-fetch

# GitHub CLI (skill support is in preview); see `gh skill install --help` for --agent and --scope
gh skill install robert-mcdermott/nihreporter-fetch nihreporter-fetch
```

### Install manually
Copy (or symlink) the `skills/nihreporter-fetch` folder into a skills folder your agent reads:

| Agent | Project (commit it to share with a team) | Personal (all your projects) |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| OpenAI Codex | `.agents/skills/` | `~/.agents/skills/` |
| Gemini CLI | `.agents/skills/` or `.gemini/skills/` | `~/.agents/skills/` or `~/.gemini/skills/` |
| GitHub Copilot | `.github/skills/`, `.agents/skills/` or `.claude/skills/` | `~/.agents/skills/` or `~/.copilot/skills/` |
| Devin | `.agents/skills/` or `.devin/skills/` | `~/.agents/skills/` or `~/.config/devin/skills/` |
| Cursor, Cline, OpenCode, Amp, Warp and others | `.agents/skills/` | see the agent's documentation |

For example:
```bash
git clone https://github.com/robert-mcdermott/nihreporter-fetch.git
mkdir -p ~/.agents/skills ~/.claude/skills
cp -r nihreporter-fetch/skills/nihreporter-fetch ~/.agents/skills/   # Codex, Gemini CLI, Copilot, Devin, Cursor, ...
cp -r nihreporter-fetch/skills/nihreporter-fetch ~/.claude/skills/   # Claude Code
```
To pick up updates with `git pull`, symlink instead of copying. Restart the agent, or reload its skills, after installing.

### Use it
Ask in plain language, and the agent loads the skill when the request matches its description:
> How much NIH funding did Fred Hutch get in each of the last five fiscal years, by institute?

> Find active NCI R01s on CAR T cell therapy and list the PIs and their institutions.

> Which grants funded PMID 33298401, and who are their PIs?

You can also invoke it by name: `/nihreporter-fetch` in Claude Code and Devin, or `$nihreporter-fetch` in Codex. For an agent without skill support, point it at the file instead, e.g. "Read `skills/nihreporter-fetch/SKILL.md` and follow it to find grants on ...".

## Notes
- No API key is needed. NIH asks for no more than one request per second, and for large jobs on weekends or weekdays between 9 PM and 5 AM US Eastern time. nihreporter-fetch paces its requests to one per second and retries rate-limit (429) and server (5xx) errors with backoff. Each request returns up to 500 projects. Don't run several fetches at once: they would exceed NIH's rate.
- RePORTER is updated weekly; `search_info.data_timestamp` says when it last added projects.
- Award amounts are total costs (direct plus indirect) for one fiscal year. Subprojects of center grants carry their own amounts, which are also part of the parent award, so use `--no-subprojects` (or `subproject_id IS NULL`) when summing funding to avoid counting them twice.
- Publication links come from RePORTER and are made at the core project level, so they span all years of a grant, and subprojects share their parent's links. RePORTER can't serve the links of a few core projects (its publication search fails with a server error for them, e.g. U19AI149676 in October 2026). Batches containing one are looked up again in halves, so only that project's `publications` stays `null`; it is listed in `search_info.failed_publications`, and the exit code is 2.
- See [CHANGELOG.md](CHANGELOG.md) for changes between versions, including the 0.2.0 changes from the original script.

## Development
```bash
uv sync --extra dev          # pytest, ruff, mypy, jsonschema
uv run -m pytest -q          # unit tests (offline, use real API responses in tests/fixtures)
NIHREPORTER_FETCH_LIVE=1 uv run -m pytest -q -m live   # contract tests against the real API (a few minutes)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
When CLI options or the output change, update the agent skill in `skills/nihreporter-fetch/` to match.

GitHub Actions runs lint, type checks and tests on Python 3.10-3.13 for every push and pull request (`.github/workflows/ci.yml`), and the live API tests weekly (`.github/workflows/live.yml`).
