import asyncio
import contextlib
import copy
import datetime as dt
import os
import unittest
from unittest import mock

import aiohttp
import apt
import apt_pkg

from src.checkers.debianrepochecker import DebianRepoChecker, LoggerAcquireProgress
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError, CheckerQueryError
from src.lib.externaldata import ExternalBase
from src.lib.utils import init_logging
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "org.debian.tracker.pkg.apt.yml"
)


class TestDebianRepoChecker(unittest.IsolatedAsyncioTestCase):
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
        for data in ext_data:
            self.assertIsNotNone(data)
            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.url)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsNotNone(data.new_version.version)
            self.assertIsNotNone(data.new_version.timestamp)
            self.assertIsInstance(data.new_version.timestamp, dt.date)
            self.assertNotEqual(data.new_version.url, data.current_version.url)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertNotEqual(
                data.new_version.checksum, data.current_version.checksum
            )
            self.assertRegex(
                data.new_version.url, r"http://deb.debian.org/debian/pool/main/.+"
            )
            if data.filename == "python-apt-source.tar.xz":
                self.assertRegex(
                    data.new_version.url,
                    r"http://deb.debian.org/debian/pool/main/p/python-apt/python-apt_(\d[\d\.-]+\d).tar.xz",
                )
            elif data.filename == "apt-aarch64.deb":
                self.assertRegex(
                    data.new_version.url,
                    r"http://deb.debian.org/debian/pool/main/a/apt/apt_(\d[\d\.-]+\d)_arm64.deb",
                )
            else:
                self.fail(f"Unknown data {data.filename}")


class TestDebianRepoCheckerMocked(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    def _make_external_data(self, component="main", dist="trixie", arches=None):
        ed = mock.MagicMock()
        ed.checker_data = {
            "package-name": "apt",
            "root": "http://deb.debian.org/debian",
            "dist": dist,
            "component": component,
            "source": False,
        }
        ed.arches = arches or ["x86_64"]
        ed.filename = "apt-amd64.deb"
        return ed

    def test_file_like_flush_is_noop(self):
        logger = mock.MagicMock()
        progress = LoggerAcquireProgress(logger)
        progress._file.flush()

    async def test_check_returns_early(self):
        checker = DebianRepoChecker.__new__(DebianRepoChecker)
        checker.session = mock.AsyncMock()
        checker.robots_cache = mock.MagicMock()

        external_data = self._make_external_data(component="", dist="trixie")

        with mock.patch.object(DebianRepoChecker, "should_check", return_value=True):
            with mock.patch.object(DebianRepoChecker, "_load_repo") as mock_load:
                await checker.check(external_data)
                mock_load.assert_not_called()

        external_data.set_new_version.assert_not_called()

    async def test_check_raises_missing_package(self):
        checker = DebianRepoChecker.__new__(DebianRepoChecker)
        checker.session = mock.AsyncMock()
        checker.robots_cache = mock.MagicMock()

        external_data = self._make_external_data()
        external_data.checker_data["source"] = True

        fake_cache = mock.MagicMock(spec=apt.Cache)
        src_record = mock.MagicMock(spec=apt_pkg.SourceRecords)
        src_record.lookup.return_value = False

        with mock.patch.object(DebianRepoChecker, "should_check", return_value=True):
            with mock.patch.object(
                DebianRepoChecker,
                "_load_repo",
                return_value=mock.MagicMock(
                    __enter__=mock.Mock(return_value=fake_cache),
                    __exit__=mock.Mock(return_value=False),
                ),
            ):
                with mock.patch("apt_pkg.SourceRecords", return_value=src_record):
                    with self.assertRaises(CheckerQueryError):
                        await checker.check(external_data)


class TestDebianRepoCheckerErrors(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.checker = DebianRepoChecker(mock.AsyncMock())
        self.cache_class = apt.Cache
        manifest = ManifestChecker(TEST_MANIFEST)
        self.data = next(
            data
            for data in manifest.get_external_data()
            if not data.checker_data.get("source")
        )
        for target in ("apt.Cache", "apt_pkg.init", "apt_pkg.config"):
            patcher = mock.patch(target)
            patched = patcher.start()
            self.addCleanup(patcher.stop)
            if target == "apt.Cache":
                self.cache_factory = patched
                self.cache = patched.return_value

    async def test_missing_binary_package(self):
        self.cache.__getitem__.side_effect = KeyError("apt")
        with self.assertRaisesRegex(CheckerQueryError, "apt") as caught:
            await self.checker.check(self.data)
        self.assertIsInstance(caught.exception.__cause__, KeyError)
        self.assertIsNone(self.data.new_version)
        self.cache.close.assert_called_once_with()

    async def test_missing_candidate(self):
        self.cache.__getitem__.return_value.candidate = None
        with self.assertRaisesRegex(CheckerQueryError, "candidate.*apt"):
            await self.checker.check(self.data)
        self.assertIsNone(self.data.new_version)
        self.cache.close.assert_called_once_with()

    async def test_missing_candidate_uri(self):
        self.cache.__getitem__.return_value.candidate.uri = None
        with self.assertRaisesRegex(CheckerQueryError, "URI.*apt"):
            await self.checker.check(self.data)
        self.assertIsNone(self.data.new_version)
        self.cache.close.assert_called_once_with()

    def test_repository_fetch_failure(self):
        error = apt.cache.FetchFailedException("404 Not Found")
        self.cache.update.side_effect = error
        with self.assertRaisesRegex(CheckerFetchError, "deb.debian.org") as caught:
            with self.checker._load_repo(
                "http://deb.debian.org/debian", "stable", "main", "amd64"
            ):
                self.fail("A failed repository must not be yielded")
        self.assertIs(caught.exception.__cause__, error)
        self.assertIn("404 Not Found", str(caught.exception))
        self.cache.open.assert_not_called()
        self.cache.close.assert_called_once_with()

    def test_cache_closed_on_success(self):
        with self.checker._load_repo(
            "http://example.com", "stable", "main", "amd64"
        ) as cache:
            self.assertIs(cache, self.cache)
            self.cache.close.assert_not_called()
        self.cache.close.assert_called_once_with()

    def test_cache_closed_on_open_failure(self):
        self.cache.open.side_effect = RuntimeError("unexpected failure")
        with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
            with self.checker._load_repo(
                "http://example.com", "stable", "main", "amd64"
            ):
                self.fail("A failed cache must not be yielded")
        self.cache.close.assert_called_once_with()

    def test_cache_closed_on_body_failure(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
            with self.checker._load_repo(
                "http://example.com", "stable", "main", "amd64"
            ):
                raise RuntimeError("unexpected failure")
        self.cache.close.assert_called_once_with()

    async def test_manifest_records_errors_and_checks_independent_source(self):
        for failure, error_type in (
            ("missing-package", CheckerQueryError),
            ("fetch-failed", CheckerFetchError),
        ):
            with self.subTest(failure=failure):
                manifest = ManifestChecker(TEST_MANIFEST)
                failed = next(
                    data
                    for data in manifest.get_external_data()
                    if not data.checker_data.get("source")
                )
                source = copy.deepcopy(failed.source)
                source["x-checker-data"]["package-name"] = "working"
                working = ExternalBase.from_source(TEST_MANIFEST, source, None)
                manifest._external_data = {TEST_MANIFEST: [failed, working]}
                manifest._checkers = [DebianRepoChecker]

                bad_cache = mock.MagicMock(spec=self.cache_class)
                if failure == "missing-package":
                    bad_cache.__getitem__.side_effect = KeyError("apt")
                else:
                    bad_cache.update.side_effect = apt.cache.FetchFailedException(
                        "404 Not Found"
                    )
                good_cache = mock.MagicMock(spec=self.cache_class)
                candidate = good_cache.__getitem__.return_value.candidate
                candidate.uri = "http://example.com/working-2.deb"
                candidate.version = "2.0"
                candidate.size = 123
                self.cache_factory.side_effect = [bad_cache, good_cache]

                with (
                    mock.patch.object(
                        DebianRepoChecker,
                        "_get_timestamp_for_candidate",
                        return_value=None,
                    ),
                    mock.patch(
                        "src.checkers.debianrepochecker.read_deb_hashes",
                        return_value=MultiDigest(sha256="1" * 64),
                    ),
                ):
                    checked = await manifest.check()

                self.assertEqual(checked, [failed, working])
                self.assertEqual(len(manifest.get_errors()), 1)
                self.assertIsInstance(manifest.get_errors()[0], error_type)
                self.assertIsNone(failed.new_version)
                self.assertIsNotNone(working.new_version)
                self.assertEqual(working.new_version.url, candidate.uri)
                self.assertTrue(failed.checked.is_set())
                self.assertTrue(working.checked.is_set())
                bad_cache.close.assert_called_once_with()
                good_cache.close.assert_called_once_with()


class TestDebianRepoCheckerTimestamps(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manifest = ManifestChecker(TEST_MANIFEST)
        self.manifest._checkers = [DebianRepoChecker]
        self.data = self.manifest.get_external_data()
        self.url = "https://example.com/package-2.tar.xz"
        self.cache = mock.MagicMock()
        candidate = self.cache.__getitem__.return_value.candidate
        candidate.uri = self.url
        candidate.version = "2.0"
        candidate.size = 123
        self.source_record = mock.MagicMock()
        self.source_record.version = "2.0"
        self.source_record.files = [
            mock.Mock(type="tar", path="package-2.tar.xz", size=123)
        ]
        self.head = mock.MagicMock()
        self.response = self.head.return_value.__aenter__.return_value
        self.response.url = self.url
        self.response.headers = {}
        for target, options in (
            (
                "src.checkers.debianrepochecker.DebianRepoChecker._load_repo",
                {"side_effect": lambda *a: contextlib.nullcontext(self.cache)},
            ),
            ("apt_pkg.SourceRecords", {"return_value": self.source_record}),
            (
                "src.checkers.debianrepochecker.read_deb_hashes",
                {"return_value": MultiDigest(sha256="1" * 64)},
            ),
            ("aiohttp.ClientSession.head", {"new": self.head}),
            (
                "src.lib.robots.RobotsCache.ensure_allowed",
                {"new_callable": mock.AsyncMock},
            ),
        ):
            patcher = mock.patch(target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = mock.MagicMock()
        self.session.head = self.head
        self.checker = DebianRepoChecker(self.session)

    def _prepare(self, data):
        # SourceRecords.lookup advances through the repository's source records.
        self.source_record.lookup.side_effect = [True, False]
        if data.checker_data.get("source"):
            return data.checker_data["root"].rstrip("/") + "/package-2.tar.xz"
        return self.url

    async def test_head_failures_are_checker_errors(self):
        for data in self.data:
            for error in (
                aiohttp.ConnectionTimeoutError("connection timed out"),
                asyncio.TimeoutError(),
                aiohttp.ClientConnectionError("connection reset"),
                aiohttp.ClientResponseError(
                    mock.Mock(real_url=self.url), (), status=503
                ),
            ):
                with self.subTest(source=data.checker_data.get("source"), error=error):
                    url = self._prepare(data)
                    self.head.return_value.__aenter__.side_effect = error
                    with self.assertRaises(CheckerQueryError) as caught:
                        await self.checker.check(data)
                    self.assertIs(caught.exception.__cause__, error)
                    self.assertIn(url, str(caught.exception))
                    self.assertIsNone(data.new_version)

    async def test_manifest_records_head_timeout_and_checks_independent_source(self):
        for data in self.data:
            with self.subTest(source=data.checker_data.get("source")):
                failed = copy.deepcopy(data)
                working = copy.deepcopy(data)
                working.checker_data["source"] = False
                working.checker_data["package-name"] = "working"
                self.manifest._external_data = {TEST_MANIFEST: [failed, working]}
                self._prepare(failed)
                error = aiohttp.ConnectionTimeoutError("connection timed out")
                self.head.return_value.__aenter__.side_effect = [error, self.response]
                self.manifest._errors.clear()

                checked = await self.manifest.check()

                self.assertEqual(checked, [failed, working])
                self.assertEqual(len(self.manifest.get_errors()), 1)
                caught = self.manifest.get_errors()[0]
                self.assertIsInstance(caught, CheckerQueryError)
                self.assertIs(caught.__cause__, error)
                self.assertIsNone(failed.new_version)
                self.assertIsNotNone(working.new_version)
                self.assertEqual(working.new_version.url, self.url)
                self.assertTrue(failed.checked.is_set())
                self.assertTrue(working.checked.is_set())

    async def test_head_cancellation_propagates(self):
        for data in self.data:
            with self.subTest(source=data.checker_data.get("source")):
                self._prepare(data)
                error = asyncio.CancelledError()
                self.head.return_value.__aenter__.side_effect = error
                with self.assertRaises(asyncio.CancelledError) as caught:
                    await self.checker.check(data)
                self.assertIs(caught.exception, error)
                self.assertIsNone(data.new_version)

    async def test_missing_last_modified_uses_date(self):
        self.response.headers = {"Date": "Thu, 29 Oct 2026 12:00:00 GMT"}
        for data in self.data:
            with self.subTest(source=data.checker_data.get("source")):
                url = self._prepare(data)
                await self.checker.check(data)
                self.head.assert_called_with(url, allow_redirects=True)
                self.assertEqual(
                    data.new_version.timestamp,
                    dt.datetime(2026, 10, 29, 12, tzinfo=dt.timezone.utc),
                )

    async def test_missing_date_headers_still_succeeds(self):
        for data in self.data:
            with self.subTest(source=data.checker_data.get("source")):
                self._prepare(data)
                before = dt.datetime.now(tz=dt.timezone.utc)
                await self.checker.check(data)
                after = dt.datetime.now(tz=dt.timezone.utc)
                self.assertGreaterEqual(data.new_version.timestamp, before)
                self.assertLessEqual(data.new_version.timestamp, after)

    async def test_unexpected_head_errors_propagate(self):
        for data in self.data:
            with self.subTest(source=data.checker_data.get("source")):
                self._prepare(data)
                error = RuntimeError("unexpected failure")
                self.head.return_value.__aenter__.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    await self.checker.check(data)
                self.assertIs(caught.exception, error)
                self.assertIsNone(data.new_version)


if __name__ == "__main__":
    unittest.main()
