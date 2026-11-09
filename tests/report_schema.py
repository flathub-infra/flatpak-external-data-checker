"""Validate reports offline."""

import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

DATA = Path(__file__).resolve().parents[1] / "data"
REPORT_SCHEMA = json.loads((DATA / "report.v1.schema.json").read_text())
SOURCE_SCHEMA = json.loads((DATA / "source-state.v1.schema.json").read_text())
for schema in (REPORT_SCHEMA, SOURCE_SCHEMA):
    jsonschema.Draft202012Validator.check_schema(schema)
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema))
    for schema in (REPORT_SCHEMA, SOURCE_SCHEMA)
)
REPORT_VALIDATOR = jsonschema.Draft202012Validator(
    REPORT_SCHEMA, registry=REGISTRY, format_checker=jsonschema.FormatChecker()
)


def validate_report(report):
    REPORT_VALIDATOR.validate(report)


def validate_branch_report(report):
    REPORT_VALIDATOR.evolve(
        schema={"$ref": REPORT_SCHEMA["$id"] + "#/$defs/branch"}
    ).validate(report)
