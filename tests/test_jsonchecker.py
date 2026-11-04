import datetime
import json
import os
import unittest
from unittest import mock

from src.checkers.jsonchecker import JSONChecker, _jq, parse_timestamp
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerQueryError
from src.lib.externaldata import ExternalBase, ExternalFile, ExternalGitRef
from src.lib.utils import init_logging
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "io.github.stedolan.jq.yml")


class TestJSONChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    async def test_jq_cannot_read_environment(self):
        environment = {
            "GITHUB_TOKEN": "github-token-sentinel",
            "FLATHUBBOT_TOKEN": "flathubbot-token-sentinel",
            "FEDC_TEST_VARIABLE": "unrelated-sentinel",
        }
        with mock.patch.dict(os.environ, environment):
            for query in ("env | tojson", "$ENV | tojson"):
                with self.subTest(query=query):
                    self.assertEqual(await _jq(query, {}, {}), "{}")
            for name, value in environment.items():
                self.assertEqual(os.environ[name], value)

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 9)
        for data in ext_data:
            self.assertIsNotNone(data)
            if data.filename == "jq-1.4.tar.gz":
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertNotEqual(data.current_version.url, data.new_version.url)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://github.com/jqlang/jq/releases/download/jq-[0-9\.\w]+/jq-[0-9\.\w]+\.tar.gz$",
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="0000000000000000000000000000000000000000000000000000000000000000"
                    ),
                )
            elif data.filename == "jq-1.4.tarball.tar.gz":
                self.assertEqual(
                    data.new_version.timestamp,
                    datetime.datetime.fromisoformat("2018-11-02T01:54:23+00:00"),
                )
            elif data.filename == "oniguruma.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertNotEqual(
                    data.new_version.commit, "e03900b038a274ee2f1341039e9003875c11e47d"
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertIsNotNone(data.new_version.timestamp)
            elif data.filename == "yasm.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertIsNone(data.new_version.timestamp)
            elif data.filename == "openal-soft.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.timestamp)
                self.assertIsInstance(data.new_version.timestamp, datetime.datetime)
            elif data.filename == "tdesktop.git":
                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.tag, "v3.7.3")
            elif data.filename == "tg_owt.git":
                self.assertIsNotNone(data.new_version)
                self.assertEqual(
                    data.new_version.commit, "63a934db1ed212ebf8aaaa20f0010dd7b0d7b396"
                )
            elif data.filename == "lib_webrtc.git" or data.filename == "tg_angle.git":
                self.assertIsNone(data.new_version)
            else:
                self.fail(f"Unhandled data {data.filename}")


class TestJSONCheckerMocked(unittest.IsolatedAsyncioTestCase):
    def git_source(self, extra):
        return ExternalBase.from_source(
            "git.json",
            {
                "type": "git",
                "url": "https://github.com/o/r.git",
                "tag": "v1",
                "x-checker-data": {
                    "type": "json",
                    "tag-query": '"v2"',
                    "commit-query": json.dumps("b" * 40),
                    **extra,
                },
            },
        )

    async def test_secondary_responses_supply_release_links(self):
        api = "https://api.github.com/repos/o/r/releases"
        data = self.git_source(
            {
                "version-data-url": json.dumps(api),
                "version-query": ".[1].tag_name",
            }
        )
        checker = await self._make_checker()
        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            fetch.return_value = [
                {
                    "tag_name": "v3",
                    "html_url": "https://github.com/o/r/releases/tag/v3",
                },
                {
                    "tag_name": "v2",
                    "html_url": "https://github.com/o/r/releases/tag/v2",
                },
            ]
            await checker.check(data)
            fetch.assert_awaited_once_with(api)
        self.assertEqual(data.new_release_url, "https://github.com/o/r/releases/tag/v2")

    async def test_release_query_cannot_fetch_another_url(self):
        checker = await self._make_checker()
        data = self.git_source(
            {
                "release-url-data-url": '"https://example.org/api/" + $tag',
                "release-url-query": '"https://example.org/notes/" + $tag',
            }
        )
        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            await checker.check(data)
            fetch.assert_not_awaited()
        self.assertEqual(data.new_release_url, "https://example.org/notes/v2")

    async def test_url_data_response_supplies_release_link(self):
        checker = await self._make_checker()
        api = "https://api.github.com/repos/o/r/releases/tags/v2"
        asset = "https://example.org/app-2.zip"
        source = ExternalBase.from_source(
            "app.json",
            {
                "type": "archive",
                "url": "https://example.org/app-1.zip",
                "sha256": "old",
                "x-checker-data": {
                    "type": "json",
                    "version-query": '"2"',
                    "url-data-url": json.dumps(api),
                    "url-query": ".assets[0].browser_download_url",
                },
            },
        )

        async def update(data, version, url, **kwargs):
            data.new_version = data.current_version._replace(version=version, url=url)

        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            fetch.return_value = {
                "tag_name": "v2",
                "html_url": "https://github.com/o/r/releases/tag/v2",
                "assets": [{"browser_download_url": asset}],
            }
            with mock.patch.object(checker, "_update_version", side_effect=update):
                await checker.check(source)
            fetch.assert_awaited_once_with(api)
        self.assertEqual(source.new_version.url, asset)
        self.assertEqual(
            source.new_release_url, "https://github.com/o/r/releases/tag/v2"
        )

    async def test_unrelated_secondary_release_links_are_ignored(self):
        checker = await self._make_checker()
        source = self.git_source(
            {
                "url": "https://api.github.com/repos/o/r/releases",
                "version-data-url": '"https://codeberg.org/api/v1/repos/o/r/releases"',
                "version-query": ".tag_name",
            }
        )
        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            fetch.side_effect = [
                {
                    "tag_name": "v2",
                    "html_url": "https://github.com/o/r/releases/tag/v2",
                },
                {
                    "tag_name": "v2",
                    "html_url": "https://codeberg.org/o/r/releases/tag/v2",
                },
            ]
            await checker.check(source)
        self.assertEqual(
            source.new_release_url, "https://github.com/o/r/releases/tag/v2"
        )
        self.assertEqual(source.new_version.tag, "v2")

    async def test_unrelated_repository_with_same_tag_has_no_link(self):
        checker = await self._make_checker()
        source = self.git_source(
            {"url": "https://api.github.com/repos/o/other/releases"}
        )
        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            fetch.return_value = {
                "tag_name": "v2",
                "html_url": "https://github.com/o/other/releases/tag/v2",
            }
            await checker.check(source)
        self.assertIsNone(source.new_release_url)

    def setUp(self):
        init_logging()

    async def _make_checker(self):
        return JSONChecker.__new__(JSONChecker)

    async def test_release_url_query_is_optional(self):
        checker = await self._make_checker()
        data = ExternalBase.from_source(
            "git.json",
            {
                "type": "git",
                "url": "https://github.com/o/r.git",
                "tag": "v1",
                "x-checker-data": {
                    "type": "json",
                    "tag-query": '"v2"',
                    "commit-query": json.dumps("b" * 40),
                    "release-url-query": '"https://example.org/notes/" + $tag',
                },
            },
        )
        await checker.check(data)
        self.assertEqual(data.new_release_url, "https://example.org/notes/v2")
        for query in ("null", "empty", 'error("missing")', '"javascript:alert(1)"'):
            with self.subTest(query=query):
                data.checker_data["release-url-query"] = query
                await checker.check(data)
                self.assertEqual(data.new_version.tag, "v2")
                self.assertIsNone(data.new_release_url)

    async def test_github_release_api_link_reuses_response(self):
        checker = await self._make_checker()
        api = "https://api.github.com/repos/o/r/releases"
        release = {
            "tag_name": "project-v2",
            "html_url": "https://github.com/o/r/releases/tag/project-v2",
            "assets": [{"browser_download_url": "https://cdn.example.org/app.zip"}],
        }
        data = ExternalBase.from_source(
            "archive.json",
            {
                "type": "archive",
                "url": "https://example.org/old.zip",
                "sha256": "abc",
                "x-checker-data": {
                    "type": "json",
                    "url": api,
                    "version-query": '.[1].tag_name | ltrimstr("project-v")',
                    "url-query": ".[1].assets[0].browser_download_url",
                },
            },
        )

        async def update(source, version, url, **kwargs):
            source.new_version = source.current_version._replace(
                version=version, url=url
            )

        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            fetch.return_value = [{"tag_name": "v3"}, release]
            with mock.patch.object(checker, "_update_version", side_effect=update):
                await checker.check(data)
            fetch.assert_awaited_once_with(api)
        self.assertEqual(data.new_version.version, "2")
        self.assertEqual(data.new_release_url, release["html_url"])

    async def test_git_query_sequence_skips_url(self):
        data = ExternalBase.from_source(
            "git.json",
            {
                "type": "git",
                "url": "https://github.com/o/r.git",
                "tag": "v1",
                "x-checker-data": {
                    "type": "json",
                    "tag-query": '"v2"',
                    "commit-query": json.dumps("b" * 40),
                    "version-query": '$tag | ltrimstr("v")',
                    "url-query": 'error("Git must skip url-query")',
                    "timestamp-query": '"2026-01-01T00:00:00Z"',
                },
            },
        )
        checker = await self._make_checker()
        await checker.check(data)
        self.assertEqual(data.new_version.tag, "v2")
        self.assertEqual(data.new_version.commit, "b" * 40)
        self.assertEqual(data.new_version.version, "2")

        data.checker_data["url"] = "https://api.github.com/repos/o/r/releases"
        with mock.patch.object(
            checker, "_get_json", new_callable=mock.AsyncMock
        ) as fetch:
            fetch.return_value = [
                {
                    "tag_name": "v3",
                    "html_url": "https://github.com/o/r/releases/tag/v3",
                },
                {
                    "tag_name": "v2",
                    "html_url": "https://github.com/o/r/releases/tag/v2",
                },
            ]
            await checker.check(data)
            fetch.assert_awaited_once()
        self.assertEqual(data.new_release_url, "https://github.com/o/r/releases/tag/v2")

    async def test_jq_invalid_json_output_raises(self):
        with mock.patch(
            "src.checkers.jsonchecker.utils.Command.run",
            new_callable=mock.AsyncMock,
            return_value=(b"this is not json", b""),
        ):
            with self.assertRaises(CheckerQueryError) as ctx:
                await _jq(".foo", {"foo": 1}, {})
        self.assertIn("Error reading jq output", str(ctx.exception))

    async def test_jq_object_result_raises(self):
        with mock.patch(
            "src.checkers.jsonchecker.utils.Command.run",
            new_callable=mock.AsyncMock,
            return_value=(json.dumps({"key": "value"}).encode(), b""),
        ):
            with self.assertRaises(CheckerQueryError) as ctx:
                await _jq(".", {"key": "value"}, {})
        self.assertIn("Invalid jq output type", str(ctx.exception))

    async def test_jq_string_result_ok(self):
        with mock.patch(
            "src.checkers.jsonchecker.utils.Command.run",
            new_callable=mock.AsyncMock,
            return_value=(json.dumps("hello").encode(), b""),
        ):
            result = await _jq(".x", {"x": "hello"}, {})
        self.assertEqual(result, "hello")

    def test_parse_timestamp_partial_date_raises(self):
        with self.assertRaises(CheckerQueryError):
            parse_timestamp("2024-13-99T99:99:99")

    def test_parse_timestamp_utc_z_suffix(self):
        result = parse_timestamp("2018-11-02T01:54:23Z")
        self.assertEqual(
            result,
            datetime.datetime(2018, 11, 2, 1, 54, 23, tzinfo=datetime.timezone.utc),
        )

    def test_parse_timestamp_explicit_offset(self):
        result = parse_timestamp("2018-11-02T01:54:23+00:00")
        self.assertEqual(
            result,
            datetime.datetime(2018, 11, 2, 1, 54, 23, tzinfo=datetime.timezone.utc),
        )

    async def test_github_token_injected(self):
        for url, token, expected in (
            ("https://api.github.com/repos/foo/bar", "test-token", "token test-token"),
            (
                "https://api.github.com:443/repos/foo/bar",
                "test-token",
                "token test-token",
            ),
            ("http://api.github.com/repos/foo/bar", "test-token", None),
            ("http://api.github.com:443/repos/foo/bar", "test-token", None),
            ("https://api.github.com:8443/repos/foo/bar", "test-token", None),
            ("https://api.github.com.example.org/repos/foo/bar", "test-token", None),
            ("https://example.com/api/releases", "test-token", None),
            ("https://api.github.com/repos/foo/bar", "", None),
        ):
            with self.subTest(url=url, token=token):
                session = mock.MagicMock()
                response = session.get.return_value.__aenter__.return_value
                response.url.name = "releases"
                response.json = mock.AsyncMock(return_value={})
                checker = JSONChecker(session)
                headers = {"Accept": "application/json"}
                with mock.patch.dict(os.environ, {"GITHUB_TOKEN": token}):
                    await checker._get_json(url, headers)
                sent_headers = session.get.call_args.kwargs["headers"]
                self.assertEqual(sent_headers.get("Authorization"), expected)
                self.assertEqual(sent_headers["Accept"], "application/json")
                self.assertEqual(headers, {"Accept": "application/json"})


if __name__ == "__main__":
    unittest.main()
