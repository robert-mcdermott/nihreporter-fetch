# nihreporter-fetch reference

Record fields, `search_info`, SQLite tables and example queries for nihreporter-fetch output (schema version 1). The JSON Schema is `src/nihreporter_fetch/schema/output.schema.json` in the repository.

## Output layout

- **JSON** (`.json`): `{"schema_version": 1, "search_info": {...}, "projects": [...]}`
- **JSONL** (`.jsonl`): one project record per line, no `search_info` (use `--summary FILE` for it)
- **CSV** (`.csv`): one row per project, lists joined with `; `
- **SQLite** (`.sqlite`/`.db`): normalized tables (below); `search_info` of every run is in `runs`

Missing values are `null`, missing lists `[]`. Dates are `YYYY-MM-DD`; `date_added` is a timestamp.

## Project record

A record is one project (or subproject) in one fiscal year. Its key is `appl_id`; all years of a grant share `core_project_num`.

| Field | Meaning |
| --- | --- |
| `appl_id` | Application ID, unique per record |
| `project_num` | Full project number, e.g. `5R01AG088223-02`: application type `5`, activity code `R01`, institute code `AG`, serial number `088223`, support year `02`, optional suffix (`S1` supplement, `A1` amended) |
| `core_project_num` | `R01AG088223`: the grant across all its years |
| `subproject_id` | Set for components of center/program grants (their `core_project_num` is the parent's) |
| `fiscal_year` | Fiscal year of the award (October to September) |
| `url` | The project's RePORTER page |
| `project_title`, `abstract_text`, `phr_text` | Title, abstract, public health relevance statement |
| `application_type` | `1` new, `2` competing renewal, `3` supplement, `4` extension, `5` non-competing continuation, `6`-`9` changes of organization, grantee or institute |
| `activity_code`, `ic_code`, `serial_num`, `support_year`, `suffix_code` | Parts of the project number |
| `funding_mechanism` | e.g. `Non-SBIR/STTR`, `SBIR/STTR` |
| `mechanism_code_dc`, `cfda_code`, `opportunity_number` | Budget mechanism code, CFDA (assistance listing) number, funding opportunity (e.g. `PA-20-185`) |
| `agency_code` | `NIH`, `ALLCDC` (CDC), `AHRQ`, `FDA`, `VA`; in older years also `HRSA` and `SAMSHA` (RePORTER's spelling of SAMHSA) |
| `admin_ic` | Administering institute: `code` (`AG`), `abbreviation` (`NIA`), `name` |
| `ic_fundings[]` | Per funding institute: `fiscal_year`, `code`, `abbreviation`, `name`, `total_cost`, `direct_cost`, `indirect_cost` |
| `award_amount`, `direct_cost`, `indirect_cost` | Total, direct and indirect cost for the fiscal year (USD); `null` for VA projects |
| `award_notice_date` | When the award was issued |
| `budget_start`, `budget_end` | This year's budget period |
| `project_start_date`, `project_end_date` | The whole project period |
| `date_added` | When RePORTER added the record (weekly updates) |
| `is_active`, `is_new`, `arra_funded` | Booleans; `arra_funded` marks 2009 Recovery Act funding |
| `covid_response[]` | COVID-19 funding codes: `Reg-CV`, `CV`, `C3`, `C4`, `C5`, `C6` |
| `contact_pi_name` | `LAST, FIRST M` of the contact PI |
| `principal_investigators[]` | `profile_id`, `full_name`, `first_name`, `middle_name`, `last_name`, `title`, `is_contact_pi` |
| `program_officers[]` | `full_name`, `first_name`, `middle_name`, `last_name` |
| `organization` | `name`, `city`, `state`, `country`, `zipcode`, `cong_dist`, `dept_type`, `type` (e.g. `Domestic Higher Education`), `type_code`, `uei`, `duns`, `ipf_code`, `fips`, `external_org_id`, `latitude`, `longitude` |
| `study_section` | Review group: `code` (e.g. `CMAD`), `flex`, `sra_designator_code`, `sra_flex_code`, `group_code`, `name` |
| `spending_categories[]` | RCDC categories: `id`, `name` |
| `pref_terms[]` | RePORTER's preferred terms (deduplicated concepts) |
| `terms[]` | All terms, including synonyms (long) |
| `publications` | Sorted PMIDs linked to the core project, or `null` unless `--publications`/`--pmids` was used |

## search_info

| Field | Meaning |
| --- | --- |
| `tool`, `schema_version`, `generated_on`, `api` | Provenance |
| `data_timestamp` | When RePORTER last added projects (its data version) |
| `source` | `search`, `ids`, `pmids` or `raw` |
| `criteria`, `filters`, `sort` | The API criteria sent, and the options they came from |
| `total_found`, `max_results`, `expected`, `retrieved` | Matches, cap, records planned, records fetched |
| `truncated` | Fewer records retrieved than matched (`--max-results`, or a part over 15,000 that couldn't be split) |
| `subsearches` | Searches run (more than 1 when a search over 15,000 matches was split) |
| `complete`, `failed_batches` | Whether everything succeeded, and which pages or batches failed |
| `failed_publications` | Core projects whose publications couldn't be looked up (`core_project_num`, `error`); their `publications` is `null` |
| `missing_ids` | Application IDs not found (`--appl-ids`), or PMIDs without a linked project (`--pmids`) |
| `pmids` | Number of PMIDs looked up (`--pmids`) |
| `with_publications` | Whether records have `publications` |
| `skipped_records`, `skipped_existing` | Unparseable records; records skipped by `--resume` |
| `total_projects` | Records in the output after this run |

## SQLite tables

| Table | One row per | Columns |
| --- | --- | --- |
| `projects` | project record (primary key `appl_id`) | `project_num`, `core_project_num`, `subproject_id`, `fiscal_year`, `project_title`, `application_type`, `activity_code`, `ic_code`, `serial_num`, `support_year`, `suffix_code`, `funding_mechanism`, `mechanism_code_dc`, `cfda_code`, `opportunity_number`, `agency_code`, `admin_ic` (abbreviation), `admin_ic_name`, `award_amount`, `direct_cost`, `indirect_cost`, `award_notice_date`, `budget_start`, `budget_end`, `project_start_date`, `project_end_date`, `date_added`, `is_active`, `is_new`, `arra_funded`, `covid_response`, `contact_pi_name`, `org_name`, `org_city`, `org_state`, `org_country`, `org_zipcode`, `org_type`, `org_dept_type`, `org_uei`, `org_duns`, `org_ipf_code`, `cong_dist`, `latitude`, `longitude`, `study_section_code`, `study_section`, `abstract_text`, `phr_text`, `publication_count`, `url`, `record_json`, `updated_at` |
| `principal_investigators` | PI of a record | `appl_id`, `profile_id`, `full_name`, `first_name`, `middle_name`, `last_name`, `title`, `is_contact_pi` |
| `program_officers` | program officer | `appl_id`, `full_name`, `first_name`, `middle_name`, `last_name` |
| `ic_fundings` | funding institute | `appl_id`, `fiscal_year`, `code`, `abbreviation`, `name`, `total_cost`, `direct_cost`, `indirect_cost` |
| `spending_categories` | RCDC category | `appl_id`, `category_id`, `name` |
| `terms` | preferred term | `appl_id`, `term` |
| `publications` | publication of a core project | `core_project_num`, `pmid` |
| `runs` | run | `run_id`, `finished_at`, `search_info` (JSON) |

Booleans are 0/1. Fields without a column are in `record_json` (`json_extract`, `json_each`).

## Example queries

```sql
-- Funding per fiscal year, without double-counting center components
SELECT fiscal_year, COUNT(*) AS awards, ROUND(SUM(award_amount) / 1e6, 1) AS million_usd
FROM projects WHERE subproject_id IS NULL GROUP BY fiscal_year ORDER BY fiscal_year;

-- Funding by institute (co-funded awards split by institute)
SELECT f.abbreviation, ROUND(SUM(f.total_cost) / 1e6, 1) AS million_usd
FROM ic_fundings f JOIN projects p USING (appl_id)
WHERE p.subproject_id IS NULL AND p.fiscal_year = 2025
GROUP BY f.abbreviation ORDER BY million_usd DESC;

-- Grants (not award-years) by activity code
SELECT activity_code, COUNT(DISTINCT core_project_num) AS grants
FROM projects WHERE subproject_id IS NULL GROUP BY activity_code ORDER BY grants DESC;

-- Top PIs by funding (each PI of a multi-PI award gets its full amount)
SELECT pi.full_name, pi.profile_id, COUNT(DISTINCT p.core_project_num) AS grants,
       ROUND(SUM(p.award_amount) / 1e6, 1) AS million_usd
FROM projects p JOIN principal_investigators pi USING (appl_id)
WHERE p.subproject_id IS NULL
GROUP BY pi.profile_id ORDER BY million_usd DESC LIMIT 20;

-- New and renewed grants per year (success stories)
SELECT fiscal_year, SUM(application_type = '1') AS new, SUM(application_type = '2') AS renewed
FROM projects WHERE subproject_id IS NULL GROUP BY fiscal_year ORDER BY fiscal_year;

-- Organizations receiving the most (for multi-organization searches)
SELECT org_name, org_state, COUNT(*) AS awards, ROUND(SUM(award_amount) / 1e6, 1) AS million_usd
FROM projects WHERE subproject_id IS NULL GROUP BY org_name ORDER BY million_usd DESC LIMIT 20;

-- Spending categories
SELECT name, COUNT(DISTINCT appl_id) AS projects
FROM spending_categories GROUP BY name ORDER BY projects DESC LIMIT 20;

-- Projects tagged with a term
SELECT DISTINCT p.project_num, p.project_title
FROM projects p JOIN terms t USING (appl_id) WHERE t.term = 'Immunotherapy';

-- Grants ending within a year
SELECT core_project_num, MAX(project_end_date) AS ends, MAX(contact_pi_name) AS pi
FROM projects WHERE subproject_id IS NULL
GROUP BY core_project_num HAVING ends BETWEEN date('now') AND date('now', '+1 year') ORDER BY ends;

-- Publications per grant
SELECT core_project_num, COUNT(*) AS papers FROM publications GROUP BY core_project_num ORDER BY papers DESC;

-- Which grants a PMID is linked to
SELECT DISTINCT p.core_project_num, p.contact_pi_name, p.org_name
FROM publications pub JOIN projects p USING (core_project_num) WHERE pub.pmid = 33298401;

-- Program officers, from record_json
SELECT json_extract(po.value, '$.full_name') AS officer, COUNT(*) AS awards
FROM projects, json_each(record_json, '$.program_officers') AS po GROUP BY officer ORDER BY awards DESC;

-- Run history
SELECT run_id, finished_at, json_extract(search_info, '$.retrieved') AS retrieved,
       json_extract(search_info, '$.total_projects') AS total, json_extract(search_info, '$.complete') AS complete
FROM runs;
```

## Activity codes, briefly

| Prefix | Kind | Common codes |
| --- | --- | --- |
| R | Research projects | `R01` (standard), `R21` (exploratory), `R03` (small), `R35` (outstanding investigator), `R37` (MERIT), `R61`/`R33` (phased) |
| K | Career development | `K99`/`R00` (pathway to independence), `K08`, `K23` (clinician scientists), `K01` |
| F, T | Fellowships, training grants | `F31`, `F32`, `T32` |
| P, U54 | Centers and program projects | `P01`, `P30` (center core), `P50` (specialized centers), `U54` |
| U | Cooperative agreements | `U01`, `U19`, `UM1`, `UG3`/`UH3` |
| R43, R44, R41, R42 | SBIR/STTR small business | |
| S10 | Shared instrumentation | |
| ZIA, ZIC | NIH intramural | |
