# Changelog

## 0.2.0

nihreporter-fetch is now a package that can be imported by other scripts, as well as a command-line tool, built like its sister tools [pubmed-fetch](https://github.com/robert-mcdermott/pubmed-fetch) and [trials-fetch](https://github.com/robert-mcdermott/trials-fetch).

### Breaking changes
- The code is now the `nihreporter_fetch` package (in `src/`). Run it with `uv run nihreporter-fetch` or `uv run -m nihreporter_fetch`; `uv run main.py` no longer works.
- JSON output is a document with `schema_version`, `search_info` and `projects`, where each project is a flat, normalized record (schema version 1) instead of the API's object: dates without times, `null` instead of empty strings, Y/N flags as booleans, terms and categories as lists, and the organization, its type and location in one `organization` object. Use `--raw` for the API's objects, and `--from-raw` to convert files written by 0.1 (`{"meta", "results"}`).
- At most 1,000 projects are fetched by default (0.1 fetched every match). Use `--max-results all` for every match; a warning says when results were cut. `--max-records` still works as another name for `--max-results`.
- `--criteria-json` and `--criteria-file` still take a 0.1 request body, but only its `criteria`, `sort_field` and `sort_order` are used (and `include_fields` with `--raw`). The output file defaults to `projects.json` (was `output.json`).
- `--endpoint publications` is replaced by `--publications` (add the PubMed IDs linked to each project) and `--pmids` (fetch the projects that publications are linked to). `--page-limit`, `--rate`, `--timeout` and `--retries` are gone: pages are always 500 records, requests are paced at one per second as NIH asks, and transient errors are retried automatically.
- Requires Python 3.10+ (was 3.12+).

### Fixed
- `nihreporter_client.py`, which `main.py` imported, was never committed, so a fresh checkout didn't run.
- Searches matching more than 15,000 projects failed after fetching 15,000 of them, and wrote nothing: RePORTER serves at most 15,000 records per search and rejects later offsets. They are now split automatically by fiscal year, then by institute and agency, so `--max-results all` retrieves all but a handful of matches (2 of FY2025's 76,387 carry codes no search reaches; a warning reports such gaps).
- Paging relied on the API's default order. Searches are now sorted by application ID unless `--sort` says otherwise, so pages can't overlap or skip records, and records are de-duplicated.
- A 429 or server error during a run discarded everything fetched so far. Records are now written as they arrive (JSONL, CSV, SQLite), a failed page is recorded in `search_info.failed_batches`, and the exit code is 2 for partial results.
- Mistakes that RePORTER answers with every project or none at all are now caught before any request: unknown criteria names (which RePORTER ignores, matching all 3 million projects), unknown agencies and COVID-19 codes, unsortable fields and award amounts over $2,147,483,647 (server errors), lower-case state codes (no matches), award ranges without a maximum (ignored), and `--active` with `--fiscal-year` (the API returns both sets).
- Date ranges now include their end date: RePORTER compares timestamps, so records added during the last day of a range were missed.

### Added
- Search options: free text in titles, abstracts and terms with `and`/`or`/`advanced` operators (`TEXT`, `--text-field`, `--text-operator`), `--pi`, `--pi-profile-id`, `--po`, `--org`, `--org-exact`, `--org-city`, `--org-state`, `--org-country`, `--project-num`.
- Filters: `--fiscal-year` (with ranges such as `2020-2024`), `--agency`, `--activity-code`, `--application-type`, `--opportunity-number`, `--cong-dist`, `--spending-category`, `--covid-response`, `--min-award`/`--max-award`, `--active`, `--multi-pi`, `--no-subprojects`/`--subprojects-only`, `--newly-added`, and `--criteria` for any other API criteria.
- Date ranges on the award notice, project start, project end or added date (`--date-field`, `--date-from`, `--date-to`, `--days`).
- `--max-results`, `--sort`, `--count`.
- `--appl-ids` to fetch specific projects (missing IDs are reported) and `--pmids` to find the projects publications are linked to.
- `--publications` adds the PubMed IDs linked to each project. A core project whose links RePORTER can't serve (a server error for any batch containing it) no longer loses the rest of its batch: the batch is looked up again in halves, and only that project is listed in `search_info.failed_publications`.
- Output as JSON, JSONL, CSV or a normalized SQLite database; `--output -` for stdout; `--merge`, `--resume`, `--summary`.
- `search_info` provenance and completeness: tool version, data timestamp (RePORTER's latest weekly update), the API criteria sent, counts, `truncated`, `subsearches`, `complete`, `failed_batches`, `missing_ids`.
- `--since-last-run STATE` for incremental monitoring of newly added projects.
- INI config files (`--config`, `--create-config`).
- A library API (`ReporterClient`, `QuerySpec`, `run`, `Options`, `parse_project`), a published JSON Schema (`nihreporter_fetch.schema`), type hints checked with mypy, offline tests on real API responses, weekly live API tests in CI.
- An agent skill for AI coding agents in `skills/nihreporter-fetch/`.

## 0.1.0
- Fetch projects or publications from the NIH RePORTER API with a JSON request body, paging through results, and save the API's records as JSON.
