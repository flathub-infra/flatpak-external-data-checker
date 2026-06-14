import datetime
import json
import os
import unittest
from unittest import mock

from src.checkers.jsonchecker import JSONChecker, _jq, parse_timestamp
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerQueryError
from src.lib.externaldata import ExternalFile, ExternalGitRef
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
    def setUp(self):
        init_logging()

    async def _make_checker(self):
        return JSONChecker.__new__(JSONChecker)

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
        checker = await self._make_checker()
        captured_headers: dict = {}

        async def fake_parent_get_json(self, url, headers=None):
            captured_headers.update(headers or {})
            return {"data": "ok"}

        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_TESTTOKEN123"}):
            with mock.patch.object(
                JSONChecker.__bases__[0], "_get_json", new=fake_parent_get_json
            ):
                await checker._get_json("https://api.github.com/repos/foo/bar")
        self.assertIn("Authorization", captured_headers)
        self.assertEqual(captured_headers["Authorization"], "token ghp_TESTTOKEN123")

    async def test_github_token_not_injected(self):
        checker = await self._make_checker()
        captured_headers: dict = {}

        async def fake_parent_get_json(self, url, headers=None):
            captured_headers.update(headers or {})
            return {}

        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_TESTTOKEN123"}):
            with mock.patch.object(
                JSONChecker.__bases__[0], "_get_json", new=fake_parent_get_json
            ):
                await checker._get_json("https://example.com/api/releases")
        self.assertNotIn("Authorization", captured_headers)

    async def test_no_github_token_env_var(self):
        checker = await self._make_checker()
        captured_headers: dict = {}

        async def fake_parent_get_json(self, url, headers=None):
            captured_headers.update(headers or {})
            return {}

        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(
                JSONChecker.__bases__[0], "_get_json", new=fake_parent_get_json
            ):
                await checker._get_json("https://api.github.com/repos/foo/bar")
        self.assertNotIn("Authorization", captured_headers)


if __name__ == "__main__":
    unittest.main()
