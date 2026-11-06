import copy
import json
from unittest import mock

import jsonschema

from src.checkers.anityachecker import AnityaChecker
from src.lib.errors import CheckerFetchError, CheckerMetadataError, CheckerQueryError
from src.lib.externaldata import ExternalData, ExternalGitRef, ExternalGitRepo
from tests.support import LocalHTTPTestCase

URL = "https://github.com/libpinyin/libpinyin"
COMMITS = {
    "v2.11.92": "85905e59cb19d0343cae84d831027aee113420b8",
    "2.11.91": "0c5e80e1200f84fab185d1c5bde458b770a0636c",
    "2.10.3": "954ecc3522686fb30a78d12080da1145df3f24da",
}
# Reduced from /api/v2/versions/?project_id=1353, preserving server order.
RESPONSE = {
    "latest_version": "2.11.92",
    "stable_versions": ["2.11.92", "2.11.91", "2.10.3"],
    "versions": ["2.11.92", "2.11.91", "2.10.3"],
    "version_details": [
        {"version": "v2.11.92", "pre_release": False},
        {"version": "2.11.91", "pre_release": False},
        {"version": "2.10.3", "pre_release": False},
    ],
}


class TestAnityaOriginalTags(LocalHTTPTestCase):
    def data(self, settings=None):
        return ExternalGitRepo.from_source_impl(
            source_path="test.json",
            source={
                "type": "git",
                "url": URL,
                "tag": "2.9.92",
                "commit": "a" * 40,
                "x-checker-data": {
                    "type": "anitya",
                    "project-id": 1353,
                    "baseurl": str(self.server.make_url("/")),
                }
                | (settings or {}),
            },
        )

    async def check(self, data, payload=None, refs=None):
        self.responses["/api/v2/versions/"] = {
            "text": json.dumps(RESPONSE if payload is None else payload),
            "content_type": "application/json",
        }
        checker = AnityaChecker(self.http)
        await checker.validate_checker_data(data)
        with mock.patch(
            "src.lib.utils.git_ls_remote",
            return_value=refs
            if refs is not None
            else {f"refs/tags/{tag}": sha for tag, sha in COMMITS.items()},
        ) as remote:
            await checker.check(data)
        return remote

    async def test_transition_and_historical_versions(self):
        for constraints, tag in (
            ({}, "v2.11.92"),
            ({"<": "2.11.92"}, "2.11.91"),
            ({"<": "2.11.91"}, "2.10.3"),
        ):
            for annotated in (False, True):
                with self.subTest(tag=tag, annotated=annotated):
                    data = self.data({"versions": constraints} if constraints else {})
                    if tag == "v2.11.92":
                        data.current_version = data.current_version._replace(
                            tag="2.11.91", commit=COMMITS["2.11.91"]
                        )
                    refs = {f"refs/tags/{tag}": COMMITS[tag]}
                    if annotated:
                        refs[f"refs/tags/{tag}"] = "b" * 40
                        refs[f"refs/tags/{tag}^{{}}"] = COMMITS[tag]
                    remote = await self.check(data, refs=refs)
                    remote.assert_awaited_once_with(URL)
                    self.assertEqual(data.new_version.version, tag.removeprefix("v"))
                    self.assertEqual(data.new_version.tag, tag)
                    self.assertEqual(data.new_version.commit, COMMITS[tag])
                    data.update()
                    self.assertEqual(data.source["url"], URL)
                    self.assertEqual(data.source["tag"], tag)
                    self.assertEqual(data.source["commit"], COMMITS[tag])
        self.assertTrue(
            all(
                path == "/api/v2/versions/?project_id=1353" for _, path in self.requests
            )
        )

    async def test_stable_policy_and_constraints(self):
        payload = copy.deepcopy(RESPONSE)
        payload["latest_version"] = "3.0.0"
        payload["versions"].insert(0, "3.0.0")
        payload["version_details"].insert(0, {"version": "v3.0.0", "pre_release": True})
        refs = {f"refs/tags/{tag}": sha for tag, sha in COMMITS.items()}
        refs["refs/tags/v3.0.0"] = "c" * 40
        for settings, expected in (
            ({}, "2.11.92"),
            ({"stable-only": False}, "3.0.0"),
            ({"stable-only": False, "versions": {"<": "3.0.0"}}, "2.11.92"),
        ):
            with self.subTest(settings=settings):
                data = self.data(settings)
                await self.check(data, payload, refs)
                self.assertEqual(data.new_version.version, expected)

    async def test_excluded_versions_do_not_require_details_or_git(self):
        payload = {k: v for k, v in RESPONSE.items() if k != "version_details"}
        data = self.data({"versions": {">": "9.0"}})
        remote = await self.check(data, payload)
        remote.assert_not_awaited()
        self.assertIsNone(data.new_version)
        self.assertEqual(data.state, data.State.LATEST)

    async def test_missing_malformed_and_ambiguous_details(self):
        cases = [
            {k: v for k, v in RESPONSE.items() if k != "version_details"},
            RESPONSE | {"version_details": None},
            RESPONSE | {"version_details": {}},
            RESPONSE | {"version_details": []},
            RESPONSE | {"version_details": RESPONSE["version_details"][:2]},
            RESPONSE | {"versions": None},
            RESPONSE | {"versions": "2.11.92"},
            RESPONSE | {"versions": [None, "2.11.91", "2.10.3"]},
            RESPONSE
            | {
                "versions": ["9.0", "2.11.91", "2.10.3"],
                "stable_versions": ["9.0", "2.11.91", "2.10.3"],
            },
            RESPONSE
            | {
                "versions": ["2.11.92", "2.11.92", "2.10.3"],
                "stable_versions": ["2.11.92", "2.11.92", "2.10.3"],
            },
            RESPONSE | {"stable_versions": ["2.11.91", "2.10.3"]},
        ]
        for bad_record in (
            None,
            {},
            {"version": 2, "pre_release": False},
            {"version": "", "pre_release": False},
            {"version": "v2.11.92"},
            {"version": "v2.11.92", "pre_release": "false"},
        ):
            cases.append(
                RESPONSE
                | {"version_details": [bad_record, *RESPONSE["version_details"][1:]]}
            )
        for payload in cases:
            with self.subTest(payload=payload):
                data = self.data({"stable-only": False})
                with mock.patch.object(ExternalGitRef, "fetch_remote") as remote:
                    with self.assertRaises(CheckerQueryError):
                        await self.check(data, payload)
                remote.assert_not_awaited()
                self.assertIsNone(data.new_version)

    async def test_exact_tag_required(self):
        for refs in (
            {},
            {"refs/heads/v2.11.92": "b" * 40},
            {"refs/tags/2.11.92": "b" * 40},
        ):
            with self.subTest(refs=refs):
                data = self.data()
                with self.assertRaises(CheckerFetchError):
                    await self.check(data, refs=refs)
                self.assertIsNone(data.new_version)

    async def test_other_normalization_schemes_and_template_syntax(self):
        for normalized, raw in (
            ("2.11.92", "release-2.11.92"),
            ("1.0rc1", "v01.0RC01"),
        ):
            for template, tag in (
                ("$original_version", raw),
                ("${original_version}", raw),
                (
                    "upstream/${original_version}-$version",
                    f"upstream/{raw}-{normalized}",
                ),
            ):
                with self.subTest(normalized=normalized, raw=raw, template=template):
                    data = self.data({"tag-template": template})
                    payload = {
                        "latest_version": normalized,
                        "versions": [normalized],
                        "stable_versions": [normalized],
                        "version_details": [{"version": raw, "pre_release": False}],
                    }
                    await self.check(data, payload, {f"refs/tags/{tag}": "b" * 40})
                    self.assertEqual(data.new_version.tag, tag)
                    self.assertEqual(data.new_version.version, normalized)

    async def test_peeled_ref_is_not_a_tag_name(self):
        data = self.data()
        payload = copy.deepcopy(RESPONSE)
        payload["version_details"][0]["version"] = "v2.11.92^{}"
        with self.assertRaisesRegex(CheckerQueryError, "not a Git tag"):
            await self.check(data, payload, {"refs/tags/v2.11.92^{}": "b" * 40})
        self.assertIsNone(data.new_version)

    async def test_escaped_and_similarly_named_variables(self):
        payload = {k: v for k, v in RESPONSE.items() if k != "version_details"}
        for template, tag in (
            ("$$original_version", "$original_version"),
            ("$${original_version}", "${original_version}"),
        ):
            data = self.data({"tag-template": template})
            await self.check(data, payload, {f"refs/tags/{tag}": "b" * 40})
            self.assertEqual(data.new_version.tag, tag)
        for template in ("$original_version_suffix", "${original_versions}", "$!"):
            with self.subTest(template=template):
                with self.assertRaises(CheckerMetadataError):
                    await self.check(self.data({"tag-template": template}), payload)

    async def test_legacy_template_without_details(self):
        data = self.data({"tag-template": "v$version"})
        payload = {k: v for k, v in RESPONSE.items() if k != "version_details"}
        await self.check(data, payload)
        self.assertEqual(data.new_version.tag, "v2.11.92")
        self.assertEqual(data.new_version.version, "2.11.92")

    async def test_legacy_template_does_not_implicitly_use_original_tag(self):
        data = self.data({"tag-template": "${version}"})
        with self.assertRaises(CheckerFetchError):
            await self.check(data)
        self.assertIsNone(data.new_version)

    def test_schema(self):
        git_schema = AnityaChecker.get_json_schema(ExternalGitRepo)
        archive_schema = AnityaChecker.get_json_schema(ExternalData)
        for settings in (
            {},
            {"tag-template": "$version"},
            {"tag-template": "$original_version"},
            {"tag-template": "${original_version}"},
        ):
            jsonschema.validate({"project-id": 1353} | settings, git_schema)
        for settings in (
            {"tag-template": True},
            {"tag-template": None},
        ):
            with self.subTest(settings=settings):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate({"project-id": 1353} | settings, git_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({}, git_schema)
        archive = {"project-id": 1353, "url-template": "https://example.org/$version"}
        jsonschema.validate(archive, archive_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"project-id": 1353}, archive_schema)
