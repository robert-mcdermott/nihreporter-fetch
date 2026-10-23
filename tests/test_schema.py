"""The output must match the published JSON Schema, so changes to it are deliberate."""

import json

import jsonschema
import pytest
from conftest import fake_api

from nihreporter_fetch import Options, QuerySpec, parse_project, run
from nihreporter_fetch.schema import load_schema, project_schema


@pytest.fixture(scope="module")
def validator():
    jsonschema.Draft202012Validator.check_schema(load_schema())
    return jsonschema.Draft202012Validator(project_schema())


def test_every_fixture_record_matches_schema(validator, raw_projects):
    for raw in raw_projects:
        validator.validate(parse_project(raw))


def test_full_output_document_matches_schema(tmp_path):
    out = tmp_path / "out.json"
    opts = Options(spec=QuerySpec(org_names=["X*"]), display=False, output_file=str(out), publications=True)
    run(opts, session=fake_api())
    doc = json.loads(out.read_text())
    jsonschema.validate(doc, load_schema())
    assert any(p["publications"] for p in doc["projects"])


@pytest.mark.parametrize("mode", [{"appl_ids": [11124893, 99999999]}, {"pmids": [33298401, 1]}])
def test_other_modes_match_schema(tmp_path, mode):
    out = tmp_path / "out.json"
    run(Options(display=False, output_file=str(out), **mode), session=fake_api())
    jsonschema.validate(json.loads(out.read_text()), load_schema())


def test_schema_rejects_unexpected_fields(validator, records):
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**records[11124893], "surprise": 1})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**records[11124893], "project_start_date": "2024-07-01T00:00:00"})
