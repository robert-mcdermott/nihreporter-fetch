# NIH RePORTER Data Fetch CLI

A command-line tool for fetching grant Projects or Publications from the NIH RePORTER API and writing results to JSON. It supports:
- Projects and Publications search endpoints (V2)
- Criteria provided via inline JSON or a JSON file
- Pagination across all results with optional max-records cap
- Rate limiting (default 1 req/sec) and simple retries/backoff for 429 and transient errors

API docs: https://api.reporter.nih.gov/

## Requirements
- Python 3.12
- uv (package/dependency manager). If you don’t have it: https://docs.astral.sh/uv/

## Install / Setup
From the repository root:

```bash
# Install dependencies and build/install the local package + console script
uv sync
```

This installs the console script `nihreporter-fetch` for use with `uv run`.

## Quick Start
Fetch 10 projects from fiscal year 2023 and write to projects_2023.json:

```bash
uv run nihreporter-fetch \
  --endpoint projects \
  --criteria-json '{"criteria": {"fiscal_years": [2023]}, "include_fields": ["ProjectNum","FiscalYear","AwardAmount"], "limit": 10}' \
  --page-limit 10 \
  --max-records 10 \
  --output projects_2023.json \
  --verbose
```

Fetch 5 publications for a project number pattern:

```bash
uv run nihreporter-fetch \
  --endpoint publications \
  --criteria-json '{"criteria": {"core_project_nums": ["5UG1HD0784*"]}, "limit": 5}' \
  --page-limit 5 \
  --max-records 5 \
  --output pubs.json \
  --verbose
```

## Using a Criteria File
Create a JSON file specifying the full POST body expected by the API. Example: `criteria.json`

```json
{
  "criteria": {
    "fiscal_years": [2023],
    "org_states": ["WA"],
    "include_active_projects": true
  },
  "include_fields": ["ProjectNum", "OrgState", "AwardAmount", "FiscalYear"],
  "limit": 500
}
```

Run using the file:

```bash
uv run nihreporter-fetch --endpoint projects --criteria-file criteria.json --output wa_2023.json --verbose
```

Notes:
- The CLI enforces a per-page limit using `--page-limit` (max 500). If `limit` is set in your JSON body, the CLI will clamp it to the `--page-limit` you pass (default 500).
- The tool paginates using `offset` internally until the API-reported total is reached, or until `--max-records` is met if provided.

## Example: All fields for Fred Hutch awards in the last 2 years

To return all fields, do not pass `include_fields` or `exclude_fields` in your request body. The API will return its full default field set.

Query all awards to Fred Hutch researchers (organization name variations) within the last 2 years:

Static date example (edit the dates accordingly):

```bash
uv run nihreporter-fetch \
  --endpoint projects \
  --criteria-json '{
    "criteria": {
      "org_names": ["FRED HUTCH*", "FRED HUTCHINSON*"],
      "award_notice_date": { "from_date": "YYYY-MM-DD", "to_date": "YYYY-MM-DD" }
    },
    "limit": 500
  }' \
  --page-limit 500 \
  --output fred_hutch_last2y.json \
  --verbose
```

On macOS (zsh), dynamically compute the 2-year window using the system date command:

```bash
FROM=$(date -v-2y +%F); TO=$(date +%F)
uv run nihreporter-fetch \
  --endpoint projects \
  --criteria-json "{\"criteria\": {\"org_names\": [\"FRED HUTCH*\", \"FRED HUTCHINSON*\"], \"award_notice_date\": { \"from_date\": \"$FROM\", \"to_date\": \"$TO\" }}, \"limit\": 500}" \
  --page-limit 500 \
  --output fred_hutch_last2y.json \
  --verbose
```

Notes:
- Using `org_names` leverages wildcard matching by default, so partial names like "FRED HUTCH*" work.
- Leaving out `include_fields` and `exclude_fields` returns all available fields.
- The command paginates across results using `limit`/`offset` under the hood. Consider adding `--max-records` to cap the total fetched if desired.

## Exact match vs wildcard and filtering by Activity Codes

By default, `org_names` does wildcard/partial matching. If you want exact organization name matching, use `org_names_exact_match`.

Exact match example with dynamic last-2-years window:

```bash
FROM=$(date -v-2y +%F); TO=$(date +%F)
uv run nihreporter-fetch \
  --endpoint projects \
  --criteria-json "{\"criteria\": {\"org_names_exact_match\": [\"FRED HUTCHINSON CANCER RESEARCH CENTER\"], \"award_notice_date\": { \"from_date\": \"$FROM\", \"to_date\": \"$TO\" }}, \"limit\": 500}" \
  --page-limit 500 \
  --output fred_hutch_exact_last2y.json \
  --verbose
```

Filter by activity codes (e.g., R01 and U01) with exact org match:

```bash
FROM=$(date -v-2y +%F); TO=$(date +%F)
uv run nihreporter-fetch \
  --endpoint projects \
  --criteria-json "{\"criteria\": {\"org_names_exact_match\": [\"FRED HUTCHINSON CANCER RESEARCH CENTER\"], \"activity_codes\": [\"R01\", \"U01\"], \"award_notice_date\": { \"from_date\": \"$FROM\", \"to_date\": \"$TO\" }}, \"limit\": 500}" \
  --page-limit 500 \
  --output fred_hutch_exact_r01_u01_last2y.json \
  --verbose
```

## Output Format
The output JSON contains two top-level keys:

```json
{
  "meta": { "total": 12345, "search_id": "...", "url": "..." },
  "results": [ { /* record 1 */ }, { /* record 2 */ } ]
}
```

- `meta` is returned by the NIH API (fields vary by endpoint). `total` is used to paginate.
- `results` is an array of project or publication records depending on the chosen endpoint.

## CLI Reference
```text
nihreporter-fetch
  --endpoint {projects,publications}   Which API to query (default: projects)
  --criteria-file PATH                 Path to JSON file with the full request body
  --criteria-json STRING               Inline JSON for the full request body
  -o, --output PATH                    Output JSON file path (default: output.json)
  --page-limit INT                     Records per page (1–500). Default: 500
  --max-records INT                    Optional cap on total records to fetch
  --rate FLOAT                         Requests per second. Default: 1.0 (recommended <= 1.0)
  --timeout FLOAT                      HTTP timeout per request in seconds (default: 60)
  --retries INT                        Retry attempts for transient errors (default: 3)
  --verbose                            Verbose logs to stderr
```

Parameters in `--criteria-json` or `--criteria-file` are passed directly to the NIH API and can include:
- `criteria`: the search filters (see API docs for allowed fields)
- `include_fields` or `exclude_fields`
- `limit`, `offset` (the CLI sets/overrides for paging)
- `sort_field`, `sort_order`, etc.

Refer to the NIH RePORTER docs for full field lists and allowed values.

## Rate Limiting and Pagination
- The CLI defaults to 1 request/second (`--rate 1.0`), per NIH guidance. You can lower this for large jobs or when seeing 429 responses.
- The tool continues fetching pages until all results are retrieved or `--max-records` is reached.
- If the API returns HTTP 429, the client exponentially backs off and retries automatically.

## Development
Common commands:

```bash
# Run the installed console script
uv run nihreporter-fetch --help

# Run via module entrypoint (equivalent behavior)
uv run main.py --help

# Add dependencies
uv add <package>

# Add dev dependencies
uv add --dev <package>
```

Project layout:
- `main.py` — argparse CLI that loads criteria, calls the client, and writes output
- `nihreporter_client.py` — API client with rate limiting, retries, and pagination
- `pyproject.toml` — project config and console script entry point

## Troubleshooting
- 429 Too Many Requests: reduce `--rate` (e.g., 0.5) and/or run during low-usage windows (NIH suggests weekends or 9pm–5am EST).
- Invalid criteria: verify your JSON body matches NIH API expectations. Start small: try a single filter and include_fields.
- Page size errors: ensure `--page-limit` is 1–500.
