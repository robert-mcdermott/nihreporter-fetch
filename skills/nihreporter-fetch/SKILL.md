---
name: nihreporter-fetch
description: >-
  Search NIH RePORTER and collect NIH-funded research project (grant) data with the nihreporter-fetch CLI:
  find projects by text, principal investigator, program officer, organization, location, project number,
  institute/agency, activity code (R01, U54, K99...), application type, funding opportunity, fiscal year,
  award amount, dates, COVID-19 funding or spending category; fetch known application IDs; find the grants
  that funded given PubMed IDs; get abstracts, award amounts, funding institutes, PIs, organizations,
  study sections, RCDC categories and linked publications; export JSON/JSONL/CSV/SQLite; monitor new
  awards over time. Use when the user asks about NIH grants or funding, RePORTER, awards to an
  institution or investigator, funding trends, R01s and other activity codes, which grants funded a
  paper, or building a research funding dataset.
---

# nihreporter-fetch

`nihreporter-fetch` searches NIH RePORTER (API v2) and writes structured, schema-versioned project records. Use it instead of scraping the website or calling the API by hand: it handles paging, the 1 request/second rate limit, retries, RePORTER's 15,000-record-per-search limit, and API pitfalls that otherwise return every project or none without an error.

Detailed field and database reference: [reference.md](reference.md).

## 1. Run the tool

Pick the first that works:

| Situation | Command prefix |
| --- | --- |
| Inside the nihreporter-fetch repository | `uv run nihreporter-fetch` |
| Installed (`uv tool install`) | `nihreporter-fetch` |
| Anywhere else (no install needed) | `uvx --from git+https://github.com/robert-mcdermott/nihreporter-fetch nihreporter-fetch` |

Below, `nihreporter-fetch` stands for whichever prefix applies. Check it works with `nihreporter-fetch --version` (expect 0.2.0 or later). No API key is needed.

## 2. Workflow

1. **Translate the request into search fields and filters** (section 3). Prefer structured filters (`--org`, `--pi`, `--agency`, `--activity-code`, `--fiscal-year`) over free text, which matches titles, abstracts and a long list of terms and is noisier.
2. **Count first** when the scope is uncertain. It costs one request:
   ```bash
   nihreporter-fetch --org "FRED HUTCH*" --fiscal-year 2025 --activity-code R01 --count
   ```
   If the count is in the tens of thousands, confirm the scope with the user or narrow it. If it is 0, loosen the search; check name spellings (RePORTER stores upper-case names such as `FRED HUTCHINSON CANCER CENTER`) and use `*` wildcards.
3. **Collect** into a file. Choose the format by purpose (section 4) and set `--max-results` deliberately (default 1000; `all` for every match). More than 15,000 matches is fine: the search is split automatically, after a minute or so of counting.
4. **Check completeness before using the data.** Exit code `0` means complete, `2` means partial (data is still written), `1` means error. In the output, `search_info.failed_batches` lists pages that failed, `failed_publications` the core projects whose publications RePORTER couldn't serve (a few are broken upstream; their `publications` is `null`), `truncated` whether `--max-results` capped the results, and `missing_ids` IDs not found (`--appl-ids`/`--pmids`).
5. **Read results with `jq` or `sqlite3`.** Never `cat` an output file into the conversation; records include long abstracts and hundreds of terms. Extract only the fields you need (section 6).
6. **Report the scope** when answering: the filters used, `total_found` versus retrieved, whether results were capped, whether subprojects were counted, and `search_info.data_timestamp` (RePORTER's latest weekly update).

## 3. Building searches

Values within one option are ORed; different options are ANDed. Names, organizations and project numbers take `*` wildcards. Put positional text first, before options that take several values.

| User wants | Option |
| --- | --- |
| An institution | `--org "FRED HUTCH*"` (partial name; check `organization.name` in results), or `--org-exact` for one exact name |
| An investigator | `--pi "Corey, Lawrence"` (last, first) or `--pi Bedford`; `--pi-profile-id ID` once you know their profile ID |
| A topic | `"CAR T"` (positional, all words in title, abstract or terms); `--text-field title` for titles only; `--text-operator or` (any word) or `advanced` (`'"CAR T" AND (glioma OR glioblastoma)'`) |
| A funder | `--agency NCI NIAID` (administering institute; whole agencies: `NIH`, `ALLCDC` (CDC), `AHRQ`, `FDA`, `VA`, `SAMHSA`, `HRSA`) |
| A grant type | `--activity-code R01 R21` (research), `K99 K08` (career), `T32` (training), `P30 U54` (centers), `R43 R44` (SBIR) |
| New awards only | `--application-type 1` (new) `2` (competing renewal); `5` = non-competing continuation (yearly renewal) |
| A place | `--org-state WA` (two-letter code), `--org-city SEATTLE`, `--org-country CANADA`, `--cong-dist WA-07` |
| A specific grant | `--project-num R01AI165821` (all years) or `R01CA*` (all NCI R01s) |
| A funding opportunity | `--opportunity-number PAR-21-123` |
| Award size | `--min-award 1000000`, `--max-award 250000` (total cost, one fiscal year) |
| COVID-19 funding | `--covid-response C3 C4 C5 C6` (supplemental acts) `Reg-CV` (regular) |
| Currently active | `--active` (not with `--fiscal-year`; RePORTER would return both sets combined) |
| Leave out center components | `--no-subprojects` (always when summing money) |
| Anything else | `--criteria '{"project_start_date": {"from_date": "2020-01-01"}}'` (API criteria, snake_case) |

**Fiscal years and dates:** `--fiscal-year 2025` or `2020-2024` is usually what "funding in year X" means. Date ranges (`--date-from`, `--date-to`, `--days N`) apply to `--date-field`:
- `award_notice` (default): when the award was issued ("awards in the last 2 years")
- `project_start`, `project_end`: the whole grant period ("grants ending this year")
- `added`: when RePORTER added the record ("what's new")

**Inputs other than a search:** `--appl-ids 11124893 11014863` (or a file, or `-` for stdin); `--pmids 33298401 ...` finds the projects publications are linked to ("which grants funded this paper?").

**Ordering:** `--sort award_amount:desc --max-results 20` gives the 20 largest awards; other sort fields include `fiscal_year`, `award_notice_date`, `project_end_date`, `date_added`, `org_name`, `contact_pi_name`. The "Only N of M matching projects" warning is expected then.

## 4. Choosing an output

| Purpose | Use |
| --- | --- |
| Quick look, piping to jq | `--output - --format jsonl -q` (one project per line on stdout) |
| Analysis, sums over years, repeated runs | `--output funding.sqlite` (normalized tables; reruns upsert) |
| Spreadsheet for the user | `--output projects.csv` (lists joined with `; `) |
| Publications of each grant | add `--publications` (PMIDs in `publications`; one extra request per 100 projects) |

Add `-q` to suppress the human-readable listing, and `--summary FILE` (or `--summary -`) to get `search_info` separately. Interrupted? Rerun the same command with `--resume`.

## 5. Worked examples

**"How much NIH funding did Fred Hutch get in each of the last five fiscal years, by institute?"**
```bash
nihreporter-fetch --org "FRED HUTCH*" --fiscal-year 2021-2025 --max-results all -q \
  --output /tmp/fh.sqlite --summary - | jq '{total_found, total_projects, complete, data_timestamp}'
sqlite3 -header -column /tmp/fh.sqlite \
  "SELECT DISTINCT org_name FROM projects;"
sqlite3 -header -column /tmp/fh.sqlite \
  "SELECT fiscal_year, admin_ic, COUNT(*) AS awards, ROUND(SUM(award_amount) / 1e6, 1) AS million_usd
   FROM projects WHERE subproject_id IS NULL GROUP BY fiscal_year, admin_ic ORDER BY fiscal_year, million_usd DESC;"
```
Check the organization names the wildcard matched, and sum only rows with `subproject_id IS NULL`: subproject amounts are already part of their parent award.

**"Find active NCI R01s on CAR T cell therapy, with PIs and institutions"**
```bash
nihreporter-fetch "CAR T" --agency NCI --activity-code R01 --active -q --output - --format jsonl \
  | jq -c '{project_num, project_title, pis: [.principal_investigators[].full_name], org: .organization.name, end: .project_end_date}'
```

**"Which grants funded PMID 33298401, and who are the PIs?"**
```bash
nihreporter-fetch --pmids 33298401 -q --output - --format jsonl \
  | jq -c '{core_project_num, activity_code, agency: .admin_ic.abbreviation, pis: [.principal_investigators[].full_name], org: .organization.name}'
```
RePORTER links publications to core projects (all years of a grant), so each result is one grant.

**"What has Lawrence Corey's research funded, and what did it publish?"**
```bash
nihreporter-fetch --pi "Corey, Lawrence" --max-results all --publications -q --output /tmp/corey.jsonl
jq -s 'group_by(.core_project_num) | map({grant: .[0].core_project_num, years: [.[].fiscal_year] | "\(min)-\(max)", total_usd: (map(.award_amount // 0) | add), papers: (.[0].publications | length)})' /tmp/corey.jsonl
```
Names are ambiguous: check `principal_investigators` and `organization` in the results, then switch to `--pi-profile-id` with the right `profile_id`. To get the papers themselves, pass the PMIDs to the pubmed-fetch skill or tool (`pubmed-fetch --pmids -`).

**"What did the 20 largest NIAID awards in FY2025 go to?"**
```bash
nihreporter-fetch --agency NIAID --fiscal-year 2025 --no-subprojects --sort award_amount:desc --max-results 20 -q \
  --output - --format jsonl | jq -r '"\(.award_amount)\t\(.activity_code)\t\(.organization.name)\t\(.project_title)"'
```

**"Track new awards to the University of Washington every week"**
```bash
nihreporter-fetch --org "UNIVERSITY OF WASHINGTON" --max-results all --since-last-run ~/nih/uw.state.json \
  --output ~/nih/uw.sqlite -q --summary - | jq '{since: .filters.date_from, retrieved, total_projects, complete}'
sqlite3 -header -column ~/nih/uw.sqlite \
  "SELECT date_added, project_num, contact_pi_name, award_amount, project_title FROM projects
   WHERE application_type IN ('1', '2') ORDER BY date_added DESC LIMIT 20;"
```
- The first run collects every current match as a baseline. Later runs fetch only projects added since the last run (`date_added`), with a one-day overlap that SQLite de-duplicates.
- Use `--max-results all`: the state only advances after a complete, uncapped run. Use SQLite or `--merge`, never `--resume`.

## 6. Reading results efficiently

```bash
# Compact listing
jq -c '{appl_id, project_num, fiscal_year, award_amount, project_title}' out.jsonl
# Counts and sums by institute / activity code / organization
jq -r '.admin_ic.abbreviation' out.jsonl | sort | uniq -c | sort -rn
jq -r '.activity_code' out.jsonl | sort | uniq -c | sort -rn
jq -s 'group_by(.organization.name) | map({org: .[0].organization.name, n: length, usd: (map(.award_amount // 0) | add)}) | sort_by(-.usd) | .[:10]' out.jsonl
# PIs
jq -r '.principal_investigators[] | .full_name' out.jsonl | sort | uniq -c | sort -rn | head
# One project's abstract
jq -r 'select(.project_num == "5R01AI165821-04") | .abstract_text' out.jsonl
# JSON documents (not JSONL): iterate .projects[]
jq -c '.projects[] | {project_num, project_title}' out.json
```
For SQLite, see the tables and example queries in [reference.md](reference.md). Child tables have one row per item, so use `COUNT(DISTINCT appl_id)`.

## 7. Pitfalls

- **A record is one project in one fiscal year.** A 5-year grant has 5 records (same `core_project_num`). Count grants with `COUNT(DISTINCT core_project_num)`, award-years with `COUNT(*)`.
- **Subprojects double-count money.** Components of P01/P30/U54 grants have their own `award_amount`, which is also inside the parent's. Use `--no-subprojects` or `subproject_id IS NULL` when summing.
- **Organization names change and vary** ("FRED HUTCHINSON CANCER RESEARCH CENTER" became "FRED HUTCHINSON CANCER CENTER"). Use a wildcard (`--org "FRED HUTCH*"`) and check which names matched; a short wildcard can also match unrelated organizations.
- **PI names are ambiguous.** Confirm with organization and topic, then use `--pi-profile-id`.
- `award_amount` is the total (direct + indirect) cost for one fiscal year; VA projects have none (`null`).
- `--active` can't be combined with `--fiscal-year`; for "active in 2025" use `--fiscal-year 2025` (projects funded that year).
- Free text matches RePORTER's long term lists, so it is broad; restrict with `--text-field title abstract` or check `pref_terms` before drawing conclusions.
- `--max-results` defaults to 1000; mention it when `search_info.truncated` is true. With `--sort` on a search split past 15,000 matches, the order holds only within each fiscal year or agency. A split of an older fiscal year can leave a few hundred projects unreachable; the warning and `truncated` say so.
- Publications are linked to core projects (all years) and come from RePORTER's own links, which can lag behind PubMed.
- `--days`/`--date-from` can't be combined; `--since-last-run` can't be used with `--date-from`, `--appl-ids` or `--pmids`; `--raw` works only with JSON/JSONL and not with `--publications`; `--output -` can't be combined with SQLite, `--merge` or `--resume`.
- Don't run several fetches in parallel: NIH asks for at most one request per second in total, and for very large jobs on weekends or overnight (US Eastern).
