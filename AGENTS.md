# AGENTS.md

- Use `uv run` to run anything (never bare `python`), and `uv add` for dependencies (never `pip`).
- Package code is in `src/nihreporter_fetch/`: `query` (QuerySpec -> API criteria, validation, dates), `client` (API v2 paging, splitting searches over 15,000 records, ID and PMID lookups, publication links, raw files), `parse` (API project -> record), `outputs` (sinks), `state`, `pipeline` (`run()`), `cli`.
- Verify changes with:
  - `uv run -m pytest -q` (offline; must not touch the network)
  - `uv run ruff check . && uv run ruff format --check .`
  - `uv run mypy`
  - `NIHREPORTER_FETCH_LIVE=1 uv run -m pytest -q -m live` when changing anything that talks to RePORTER, especially criteria building: the API silently ignores unknown criteria (matching every project) and returns no matches for unknown values, and `test_every_filter_is_accepted` checks that each filter narrows a real search.
- Record changes to the output must update `src/nihreporter_fetch/schema/output.schema.json` (tests validate against it), the SQLite schema and CSV columns in `outputs.py` if relevant, and the CHANGELOG; bump `SCHEMA_VERSION` in `_meta.py` for breaking changes.
- `sample_config.ini` must equal `cli.SAMPLE_CONFIG` (a test checks this).
- The agent skill in `skills/nihreporter-fetch/` (`SKILL.md`, `reference.md`) documents the CLI for other agents; update it when options or output fields change. Keep its frontmatter portable: only `name` (must match the folder name) and `description` (at most 1024 characters), with no tool-specific fields.
- `tests/fixtures/projects_sample.json` holds real API responses covering edge cases (subproject, VA project without amounts or a real project number, CDC/FDA/AHRQ projects, ARRA, COVID-19 supplement, foreign organization, multi-PI, two funding institutes, a 1990 project) plus publication links. Regenerate it with `uv run python tests/fixtures/make_fixtures.py`; tests rely on its contents.
