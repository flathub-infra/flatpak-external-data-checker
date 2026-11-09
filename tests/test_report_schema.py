import copy
import unittest

import jsonschema

from .report_schema import REPORT_VALIDATOR, SOURCE_SCHEMA, validate_report


class TestReportSchema(unittest.TestCase):
    def report(self):
        return {
            "schema_version": 1,
            "manifest": "/manifests/app.json",
            "started_at": "2026-11-09T12:00:00+00:00",
            "finished_at": "2026-11-09T12:01:00+00:00",
            "mode": "check",
            "status": "completed",
            "delivery": "not_requested",
            "sources": [],
            "errors": [],
        }

    def source(self):
        return {
            "manifest": "app.json",
            "source_pointer": "/sources/0",
            "module": None,
            "type": "git",
            "checker_type": "git",
            "checkers_run": ["git"],
            "status": "checked",
            "reason": None,
            "state": "VALID|LATEST",
            "current": dict.fromkeys(
                SOURCE_SCHEMA["$defs"]["source-state"]["git"]["required"]
            )
            | {"url": "https://example.com/app.git"},
            "candidate": None,
            "selected_version": None,
            "update_group": None,
            "errors": [],
        }

    def test_required_report_fields(self):
        report = self.report()
        validate_report(report)
        for field in report:
            with self.subTest(field=field):
                invalid = report.copy()
                del invalid[field]
                with self.assertRaises(jsonschema.ValidationError):
                    validate_report(invalid)

    def test_invalid_report_values(self):
        for invalid in (
            {"schema_version": 2},
            {"schema_version": "1"},
            {"status": "success"},
            {"delivery": "published"},
            {"mode": "update"},
            {"finished_at": None},
            {"outdated": -1},
            {"outdated": True},
            {"sources": {}},
            {"errors": [{"type": "Error"}]},
            {"branches": [{"branch": "branch/25.08"}]},
            {"unexpected": True},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(jsonschema.ValidationError):
                    validate_report(self.report() | invalid)

    def test_source_contract(self):
        report = self.report() | {"sources": [self.source()]}
        validate_report(report)
        for field in report["sources"][0]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(report)
                del invalid["sources"][0][field]
                with self.assertRaises(jsonschema.ValidationError):
                    validate_report(invalid)
        for changes in (
            {"type": "unknown"},
            {"status": "completed"},
            {"reason": "unknown"},
            {"current": {}},
            {"candidate": {"url": "https://example.com/app.git"}},
            {"selected_version": 1},
            {"checkers_run": [1]},
            {"errors": [{"type": "Error", "message": 1}]},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(jsonschema.ValidationError):
                    validate_report(
                        self.report() | {"sources": [self.source() | changes]}
                    )

    @unittest.skipUnless(
        "date-time" in REPORT_VALIDATOR.format_checker.checkers,
        "jsonschema date-time format dependency not installed",
    )
    def test_invalid_timestamp_format(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate_report(self.report() | {"started_at": "yesterday"})

    @unittest.skipUnless(
        "json-pointer" in REPORT_VALIDATOR.format_checker.checkers,
        "jsonschema json-pointer format dependency not installed",
    )
    def test_invalid_pointer_format(self):
        with self.assertRaises(jsonschema.ValidationError):
            validate_report(
                self.report()
                | {"sources": [self.source() | {"source_pointer": "sources/0"}]}
            )

    def test_runtime_branch_aggregation(self):
        source = self.source()
        error = {"type": "CheckerError", "message": "failed"}
        report = self.report() | {
            "status": "failed",
            "sources": [source | {"branch": "branch/25.08"}],
            "errors": [error | {"branch": "branch/25.08"}],
            "branches": [
                {
                    "branch": "branch/25.08",
                    "status": "failed",
                    "delivery": "not_requested",
                    "sources": [source],
                    "errors": [error],
                    "outdated": 0,
                },
                {
                    "branch": "branch/24.08",
                    "status": "skipped",
                    "delivery": "not_requested",
                    "sources": [],
                    "errors": [],
                },
            ],
        }
        validate_report(report)

    def test_runtime_eol(self):
        parent = {
            "ref": "runtime/org.freedesktop.Sdk/x86_64/23.08",
            "end_of_life": "Retired SDK",
            "end_of_life_rebase": None,
        }
        branch = {
            "branch": "branch/23.08",
            "status": "skipped",
            "delivery": "not_requested",
            "sources": [],
            "errors": [],
            "skip_reason": "runtime-end-of-life",
            "runtime_eol": [parent],
        }
        for reason, rebase in (
            ("Retired SDK", None),
            (None, "org.freedesktop.Sdk/x86_64/24.08"),
            ("Retired SDK", "org.freedesktop.Sdk/x86_64/24.08"),
        ):
            with self.subTest(reason=reason, rebase=rebase):
                validate_report(
                    self.report()
                    | {
                        "branches": [
                            branch
                            | {
                                "runtime_eol": [
                                    parent
                                    | {
                                        "end_of_life": reason,
                                        "end_of_life_rebase": rebase,
                                    }
                                ]
                            }
                        ]
                    }
                )
        for field in parent:
            with self.subTest(missing=field):
                invalid = parent.copy()
                del invalid[field]
                with self.assertRaises(jsonschema.ValidationError):
                    validate_report(
                        self.report()
                        | {"branches": [branch | {"runtime_eol": [invalid]}]}
                    )
        for invalid in (
            {"skip_reason": "unknown"},
            {"runtime_eol": {}},
            {"runtime_eol": [parent | {"ref": None}]},
            {"runtime_eol": [parent | {"end_of_life": True}]},
            {"runtime_eol": [parent | {"end_of_life_rebase": 1}]},
            {"runtime_eol": [parent | {"unexpected": True}]},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(jsonschema.ValidationError):
                    validate_report(self.report() | {"branches": [branch | invalid]})
