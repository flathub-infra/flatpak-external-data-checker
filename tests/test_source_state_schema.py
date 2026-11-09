import datetime
import json
import unittest
from pathlib import Path

import jsonschema

from src.lib.checksums import MultiDigest, MultiHash
from src.lib.externaldata import ExternalBase

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "data/source-state.schema.json"


class TestSourceStateSchema(unittest.TestCase):
    schema_path = SCHEMA_PATH

    @classmethod
    def setUpClass(cls):
        with cls.schema_path.open() as stream:
            cls.schema = json.load(stream)
        validator_cls = jsonschema.validators.validator_for(cls.schema)
        validator_cls.check_schema(cls.schema)
        cls.validator = validator_cls(
            cls.schema, format_checker=jsonschema.FormatChecker()
        )

    def file_source(self, source_type="archive", **kwargs):
        return ExternalBase.from_source(
            "test.json",
            {
                "type": source_type,
                "url": "https://example.com/app-1.1.0.tar.gz",
                "sha256": "0" * 64,
                **kwargs,
            },
        )

    def git_source(self, **kwargs):
        return ExternalBase.from_source(
            "test.json",
            {"type": "git", "url": "https://example.com/app.git", **kwargs},
        )

    def validate_state(self, state):
        # Validate the JSON value seen by parent-source consumers.
        exported = json.loads(json.dumps(state.json))
        self.validator.validate(exported)
        return exported

    def test_current_file_states(self):
        for source_type in ("archive", "file", "extra-data"):
            for size in (None, 0, 12345):
                with self.subTest(source_type=source_type, size=size):
                    data = self.file_source(
                        source_type, **({"size": size} if size is not None else {})
                    )
                    state = self.validate_state(data.current_version)
                    self.assertIsNone(state["version"])
                    self.assertIsNone(state["timestamp"])
                    self.assertEqual(state["size"], size)
                    self.assertEqual(
                        state["checksum"],
                        {"md5": None, "sha1": None, "sha256": "0" * 64, "sha512": None},
                    )

    def test_new_file_states(self):
        for checksum in (MultiDigest(sha256="1" * 64), MultiHash(b"app").hexdigest()):
            with self.subTest(checksum=checksum):
                data = self.file_source()
                data.set_new_version(
                    data.current_version._replace(
                        url="https://example.com/app-1.2.0.tar.gz",
                        version="1.2.0",
                        checksum=checksum,
                        size=12345,
                        timestamp=datetime.datetime(
                            2026, 1, 2, tzinfo=datetime.timezone.utc
                        ),
                    )
                )
                state = self.validate_state(data.new_version)
                self.assertEqual(state["version"], "1.2.0")
                self.assertEqual(state["size"], 12345)

    def test_each_checksum_algorithm(self):
        digests = MultiHash(b"app").hexdigest()._asdict()
        for algorithm, digest in digests.items():
            with self.subTest(algorithm=algorithm):
                data = ExternalBase.from_source(
                    "test.json",
                    {
                        "type": "archive",
                        "url": "https://example.com/app.tar.gz",
                        algorithm: digest,
                    },
                )
                state = self.validate_state(data.current_version)
                self.assertEqual(state["checksum"][algorithm], digest)

    def test_current_and_new_git_states(self):
        for refs in ({}, {"commit": "a" * 40}, {"tag": "v1.1.0"}, {"branch": "main"}):
            with self.subTest(refs=refs):
                data = self.git_source(**refs)
                current = self.validate_state(data.current_version)
                for ref in ("commit", "tag", "branch"):
                    self.assertEqual(current[ref], refs.get(ref))
                self.assertIsNone(current["version"])
                self.assertIsNone(current["timestamp"])
                data.set_new_version(
                    data.current_version._replace(
                        commit="b" * 40,
                        version="1.2.0",
                        timestamp=datetime.datetime(
                            2026, 1, 2, tzinfo=datetime.timezone.utc
                        ),
                    )
                )
                self.validate_state(data.new_version)

    def test_timestamps(self):
        for timestamp in (
            datetime.datetime(2026, 1, 2, 3, 4, 5),  # noqa: DTZ001
            datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
            datetime.datetime(
                2026,
                1,
                2,
                3,
                4,
                5,
                123456,
                tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)),
            ),
        ):
            for data in (self.file_source(), self.git_source()):
                with self.subTest(timestamp=timestamp, source=type(data).__name__):
                    state = self.validate_state(
                        data.current_version._replace(timestamp=timestamp)
                    )
                    self.assertEqual(state["timestamp"], timestamp.isoformat())

    def test_invalid_field_types(self):
        for data, invalid_fields in (
            (
                self.file_source(),
                {"checksum": (None, "abc", []), "size": ("123", 1.5, True)},
            ),
            (
                self.git_source(),
                {ref: (123, [], {}) for ref in ("commit", "tag", "branch")},
            ),
        ):
            invalid_fields.update(
                url=(None, 123), version=(123, []), timestamp=(123, {})
            )
            for field, values in invalid_fields.items():
                for value in values:
                    with self.subTest(
                        source=type(data).__name__, field=field, value=value
                    ):
                        state = data.current_version.json | {field: value}
                        with self.assertRaises(jsonschema.ValidationError):
                            self.validator.validate(state)

    def test_invalid_checksum_values(self):
        for digest in MultiDigest._fields:
            for value in (123, True, [], {}):
                with self.subTest(digest=digest, value=value):
                    state = self.file_source().current_version.json
                    state["checksum"][digest] = value
                    with self.assertRaises(jsonschema.ValidationError):
                        self.validator.validate(state)

    def test_missing_exported_fields(self):
        for data in (self.file_source(), self.git_source()):
            for field in data.current_version.json:
                with self.subTest(source=type(data).__name__, field=field):
                    state = data.current_version.json
                    del state[field]
                    with self.assertRaises(jsonschema.ValidationError):
                        self.validator.validate(state)
        for digest in MultiDigest._fields:
            with self.subTest(digest=digest):
                state = self.file_source().current_version.json
                del state["checksum"][digest]
                with self.assertRaises(jsonschema.ValidationError):
                    self.validator.validate(state)

    def test_invalid_state_shapes(self):
        file_state = self.file_source().current_version.json
        git_state = self.git_source().current_version.json
        for state in (None, [], "state", {}, file_state | git_state):
            with self.subTest(state=state):
                with self.assertRaises(jsonschema.ValidationError):
                    self.validator.validate(state)

    def test_invalid_file_cannot_validate_as_git(self):
        # With non-null metadata, the old Git alternative accepted any file
        # checksum or size because it only required a URL.
        state = (
            self.file_source()
            .current_version._replace(
                version="1.1.0",
                timestamp=datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc),
                checksum=MultiHash(b"app").hexdigest(),
                size=3,
            )
            .json
        )
        self.validator.validate(state)
        for invalid in ({"checksum": "not an object"}, {"size": "not an integer"}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(jsonschema.ValidationError):
                    self.validator.validate(state | invalid)


class TestVersionedSourceStateSchema(TestSourceStateSchema):
    schema_path = SCHEMA_PATH.with_name("source-state.v1.schema.json")

    def test_matches_existing_contract(self):
        original = json.loads(SCHEMA_PATH.read_text())
        versioned = self.schema.copy()
        del versioned["$id"]
        del versioned["title"]
        original["description"] = versioned["description"]
        original["$defs"]["timestamp"]["description"] = versioned["$defs"]["timestamp"][
            "description"
        ]
        self.assertEqual(versioned, original)
