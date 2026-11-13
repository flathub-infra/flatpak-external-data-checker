import os
import socket
import unittest
from itertools import pairwise
from unittest import mock
from unittest.mock import MagicMock

from aiohttp import web
from yarl import URL

from src.checkers.urlchecker import URLChecker, extract_version, is_same_version
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerMetadataError
from src.lib.externaldata import ExternalData
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import LocalHTTPTestCase, upstream_test


def _make_version(url, version=None):
    v = MagicMock()
    v.url = url
    v.version = version
    return v


class TestURLChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    @upstream_test
    async def test_check_appimage(self):
        checker = ManifestChecker(
            os.path.join(os.path.dirname(__file__), "com.heroicgameslauncher.hgl.yaml")
        )
        ext_data = await checker.check()

        filename = "Heroic-2.21.0-linux-x86_64.AppImage"
        data = self._find_by_filename(ext_data, filename)

        self.assertIsNotNone(data)
        self.assertEqual(data.filename, filename)
        self.assertIsNotNone(data.new_version)
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
        self.assertIsNotNone(data.new_version.version)

    @upstream_test
    async def test_check_deb(self):
        checker = ManifestChecker(
            os.path.join(os.path.dirname(__file__), "com.google.Chrome.yaml")
        )
        ext_data = await checker.check()

        data = self._find_by_filename(ext_data, "chrome.deb")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "chrome.deb")
        self.assertIsNotNone(data.new_version)
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
        self.assertIsNotNone(data.new_version.version)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


CHECKER_DATA_WITH_PATTERN = {"pattern": r"https://example\.com/prog_([\d.]+)\.tar\.gz"}
CHECKER_DATA_NO_PATTERN = {}
CURRENT_URL = "https://example.com/prog_1.2.3.tar.gz"


class TestExtractVersion(unittest.TestCase):
    def test_invalid_patterns(self):
        for pattern, message in (
            (r"(.*)(\.AppImage)$", r"contains 2 group\(s\) instead of 0 or 1"),
            ("[", "Invalid regex"),
        ):
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(CheckerMetadataError, message):
                    extract_version(
                        {"pattern": pattern}, "https://example.com/prog.AppImage"
                    )

    def test_valid_pattern(self):
        self.assertEqual(
            extract_version(CHECKER_DATA_WITH_PATTERN, CURRENT_URL), "1.2.3"
        )

    def test_pattern_without_capture_uses_only_matched_text(self):
        self.assertEqual(
            extract_version(
                {"pattern": r"https://example\.com/prog_\d+(?:\.\d+)+"}, CURRENT_URL
            ),
            "https://example.com/prog_1.2.3",
        )
        self.assertEqual(
            extract_version({"pattern": r"https://example\.com/.*"}, CURRENT_URL),
            CURRENT_URL,
        )
        self.assertIsNone(
            extract_version({"pattern": r"https://other\.com/.*"}, CURRENT_URL)
        )

    def test_no_pattern(self):
        self.assertIsNone(extract_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL))

    def test_no_match(self):
        self.assertIsNone(
            extract_version(CHECKER_DATA_WITH_PATTERN, "https://example.com/other")
        )

    def test_unmatched_optional_capture(self):
        self.assertIsNone(extract_version({"pattern": r"(?:version-(\d+))?"}, ""))


class TestURLCheckerPatterns(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.checker = URLChecker(mock.Mock())

    @staticmethod
    def _make_data(checker_data):
        return ExternalData.from_source(
            "test.yaml",
            {
                "type": "extra-data",
                "filename": "prog.AppImage",
                "url": "https://example.com/prog.AppImage",
                "sha256": "0" * 64,
                "size": 0,
                **({"x-checker-data": checker_data} if checker_data else {}),
            },
        )

    async def test_validate_invalid_patterns(self):
        for checker_type in ("rotating-url", "html"):
            for pattern in (r"(.*)(\.AppImage)$", "["):
                with self.subTest(checker_type=checker_type, pattern=pattern):
                    data = self._make_data(
                        {
                            "type": checker_type,
                            "url": CURRENT_URL,
                            "pattern": pattern,
                        }
                    )
                    self.assertTrue(self.checker.should_check(data))
                    with self.assertRaises(CheckerMetadataError):
                        await self.checker.validate_checker_data(data)

    async def test_validate_optional_pattern_and_implicit_url(self):
        for checker_data in (
            {},
            {"type": "html", **CHECKER_DATA_WITH_PATTERN},
            {"type": "rotating-url", "url": CURRENT_URL},
            {"type": "rotating-url", "url": CURRENT_URL, "pattern": r".*\.AppImage$"},
            {
                "type": "rotating-url",
                "url": CURRENT_URL,
                **CHECKER_DATA_WITH_PATTERN,
            },
        ):
            with self.subTest(checker_data=checker_data):
                data = self._make_data(checker_data)
                await self.checker.validate_checker_data(data)

    async def test_appimage_version_inference(self):
        for checker_type in ("rotating-url", "html"):
            with self.subTest(checker_type=checker_type):
                data = self._make_data(
                    {
                        "type": checker_type,
                        "url": "https://example.com/prog.AppImage",
                        "pattern": r".*prog-([\d.]+)\.AppImage$",
                    }
                )
                new_version = data.current_version._replace(
                    checksum=MultiDigest(sha256="1" * 64)
                )
                with (
                    mock.patch(
                        "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
                        new_callable=mock.AsyncMock,
                        return_value=new_version,
                    ),
                    mock.patch(
                        "src.checkers.urlchecker.utils.extract_appimage_version",
                        new_callable=mock.AsyncMock,
                        return_value="1.2.3",
                    ) as extract,
                ):
                    await self.checker.validate_checker_data(data)
                    await self.checker.check(data)
                extract.assert_awaited_once()
                self.assertEqual(data.new_version.version, "1.2.3")
                self.assertEqual(data.new_version.url, data.current_version.url)

    async def test_rotating_url_uses_rendered_mirror(self):
        primary = "https://example.com/prog_2.0.0.tar.gz"
        mirror = "https://mirror.example.com/prog_2.0.0.tar.gz"
        data = ExternalData.from_source(
            "test.yaml",
            {
                "type": "archive",
                "url": CURRENT_URL,
                "mirror-urls": ["https://mirror.example.com/prog_1.2.3.tar.gz"],
                "sha256": "0" * 64,
                "x-checker-data": {
                    "type": "rotating-url",
                    "url": "https://example.com/latest",
                    **CHECKER_DATA_WITH_PATTERN,
                    "mirror-url-templates": [
                        "https://mirror.example.com/prog_$version.tar.gz"
                    ],
                },
            },
        )
        head = MagicMock()
        head.url = URL(primary)
        head_context = MagicMock()
        head_context.__aenter__ = mock.AsyncMock(return_value=head)
        head_context.__aexit__ = mock.AsyncMock(return_value=False)
        self.checker.session.head = MagicMock(return_value=head_context)
        downloaded = data.current_version._replace(
            url=primary,
            checksum=MultiDigest(sha256="1" * 64),
        )

        with mock.patch(
            "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
            new_callable=mock.AsyncMock,
            return_value=downloaded,
        ) as fetch:
            await self.checker.check(data)

        self.assertEqual(
            fetch.await_args.kwargs["mirror_urls"]([primary]), (primary, [mirror])
        )
        self.assertTrue(fetch.await_args.kwargs["preserve_primary_url_on_mirror"])
        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.url, primary)
        self.assertEqual(data.new_version.version, "2.0.0")
        data.update()
        self.assertEqual(data.source["mirror-urls"], [mirror])

    async def test_no_autodetect(self):
        for suffix in ("AppImage", "deb"):
            with self.subTest(suffix=suffix):
                data = self._make_data(
                    {
                        "type": "rotating-url",
                        "url": f"https://example.com/prog.{suffix}",
                        "pattern": rf".*prog-([\d.]+)\.{suffix}$",
                        "no-extract-version": True,
                    }
                )
                new_version = data.current_version._replace(
                    url=f"https://example.com/prog-1.2.3.{suffix}",
                    checksum=MultiDigest(sha256="1" * 64),
                )
                with (
                    mock.patch(
                        "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
                        return_value=new_version,
                    ),
                    mock.patch(
                        "src.checkers.urlchecker.utils.extract_appimage_version",
                    ) as extract_appimage,
                    mock.patch(
                        "src.checkers.urlchecker.utils.extract_deb_version",
                    ) as extract_deb,
                ):
                    await self.checker.validate_checker_data(data)
                    await self.checker.check(data)
                extract_appimage.assert_not_called()
                extract_deb.assert_not_called()
                self.assertEqual(data.new_version.version, "1.2.3")
                self.assertEqual(data.new_version.url, new_version.url)

    async def test_validate_no_extract_version(self):
        for value in (True, False, "false", 0, None):
            with self.subTest(value=value):
                data = self._make_data(
                    {
                        "type": "rotating-url",
                        "url": "https://example.com/prog.AppImage",
                        "no-extract-version": value,
                    }
                )
                if isinstance(value, bool):
                    await self.checker.validate_checker_data(data)
                else:
                    with self.assertRaises(CheckerMetadataError):
                        await self.checker.validate_checker_data(data)


class TestURLCheckerMirrors(LocalHTTPTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.head_statuses = {}
        self.truncated_paths = set()
        self.checker = URLChecker(self.http)
        self.content = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03new archive"

    async def _respond(self, request):
        if request.method == "GET" and request.path in self.truncated_paths:
            self.requests.append((request.method, request.path_qs))
            response = web.StreamResponse(headers={"Content-Length": "1000"})
            await response.prepare(request)
            await response.write(self.content)
            request.transport.close()
            return response
        if request.method == "HEAD" and request.path in self.head_statuses:
            self.requests.append((request.method, request.path_qs))
            return web.Response(status=self.head_statuses[request.path])
        return await super()._respond(request)

    def make_data(self, checker_url):
        return ExternalData.from_source(
            "test.yaml",
            {
                "type": "archive",
                "url": str(self.server.make_url("/app-1.0.tar.gz")),
                "mirror-urls": [str(self.server.make_url("/mirror-1.0.tar.gz"))],
                "sha256": "0" * 64,
                "x-checker-data": {
                    "type": "rotating-url",
                    "url": checker_url,
                    "pattern": r".*/app-([\d.]+)\.tar\.gz",
                    "mirror-url-templates": [
                        str(self.server.make_url("/mirror-$version.tar.gz"))
                    ],
                },
            },
        )

    async def test_versioned_url_does_not_require_head(self):
        path = "/app-2.0.tar.gz"
        self.head_statuses[path] = 405
        self.responses[path] = {"body": self.content}
        primary = str(self.server.make_url(path))
        data = self.make_data(primary)

        await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.url, primary)
        self.assertEqual(data.new_version.version, "2.0")
        self.assertEqual(self.requests, [("GET", path)])

    async def test_primary_redirect_updates_version_and_mirrors(self):
        old_path = "/app-1.0.tar.gz"
        new_path = "/app-2.0.tar.gz"
        self.responses[old_path] = {
            "status": 302,
            "headers": {"Location": new_path},
        }
        self.responses[new_path] = {"body": self.content}
        data = self.make_data(str(self.server.make_url(old_path)))

        await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.version, "2.0")
        self.assertEqual(data.new_version.url, str(self.server.make_url(new_path)))
        self.assertTrue(data.state & data.State.OUTDATED)
        self.assertFalse(data.state & data.State.BROKEN)
        data.update()
        self.assertEqual(data.source["url"], str(self.server.make_url(new_path)))
        self.assertEqual(
            data.source["mirror-urls"],
            [str(self.server.make_url("/mirror-2.0.tar.gz"))],
        )
        self.assertEqual(self.requests, [("GET", old_path), ("GET", new_path)])

    async def test_mirror_redirect_preserves_primary_version_and_url(self):
        path = "/app-2.0.tar.gz"
        mirror_path = "/mirror-2.0.tar.gz"
        download_path = "/cdn/payload"
        self.responses[mirror_path] = {
            "status": 302,
            "headers": {"Location": download_path},
        }
        self.responses[download_path] = {"body": self.content}
        primary = str(self.server.make_url(path))
        data = self.make_data(primary)

        await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.version, "2.0")
        self.assertEqual(data.new_version.url, primary)
        data.update()
        self.assertEqual(data.source["url"], primary)
        self.assertEqual(
            data.source["mirror-urls"], [str(self.server.make_url(mirror_path))]
        )
        self.assertEqual(
            self.requests,
            [("GET", path), ("GET", mirror_path), ("GET", download_path)],
        )

    async def test_failed_redirect_uses_mirrors_for_resolved_version(self):
        old_path = "/app-1.0.tar.gz"
        new_path = "/app-2.0.tar.gz"
        mirror_path = "/mirror-2.0.tar.gz"
        self.responses[old_path] = {
            "status": 302,
            "headers": {"Location": new_path},
        }
        self.responses["/mirror-1.0.tar.gz"] = {"body": self.content + b"old"}
        self.responses[mirror_path] = {"body": self.content}

        for truncated in (False, True):
            with self.subTest(truncated=truncated):
                self.requests.clear()
                if truncated:
                    self.truncated_paths.add(new_path)
                data = self.make_data(str(self.server.make_url(old_path)))

                await self.checker.check(data)

                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.version, "2.0")
                data.update()
                self.assertEqual(
                    data.source["url"], str(self.server.make_url(new_path))
                )
                self.assertEqual(
                    data.source["mirror-urls"],
                    [str(self.server.make_url(mirror_path))],
                )
                self.assertEqual(
                    self.requests,
                    [("GET", old_path), ("GET", new_path), ("GET", mirror_path)],
                )

    async def test_redirect_connection_failure_uses_resolved_version(self):
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            port = unavailable.getsockname()[1]
            target = f"http://127.0.0.1:{port}/app-2.0.tar.gz"
            self.responses["/app-1.0.tar.gz"] = {
                "status": 302,
                "headers": {"Location": target},
            }
            self.responses["/mirror-1.0.tar.gz"] = {"body": self.content + b"old"}
            self.responses["/mirror-2.0.tar.gz"] = {"body": self.content}
            data = self.make_data(str(self.server.make_url("/app-1.0.tar.gz")))

            await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.version, "2.0")
        self.assertEqual(data.new_version.url, target)
        self.assertEqual(
            self.requests,
            [("GET", "/app-1.0.tar.gz"), ("GET", "/mirror-2.0.tar.gz")],
        )

    async def test_versionless_cdn_failure_keeps_latest_versioned_primary(self):
        paths = ("/app-1.0.tar.gz", "/app-2.0.tar.gz", "/cdn/payload")
        for start, target in pairwise(paths):
            self.responses[start] = {"status": 302, "headers": {"Location": target}}
        self.responses[paths[-1]] = {"status": 503}
        self.responses["/mirror-1.0.tar.gz"] = {"body": self.content + b"old"}
        self.responses["/mirror-2.0.tar.gz"] = {"body": self.content}

        for start in paths[:2]:
            with self.subTest(start=start):
                self.requests.clear()
                data = self.make_data(str(self.server.make_url(start)))

                await self.checker.check(data)

                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.version, "2.0")
                data.update()
                self.assertEqual(
                    data.source["url"], str(self.server.make_url(paths[1]))
                )
                self.assertEqual(
                    data.source["mirror-urls"],
                    [str(self.server.make_url("/mirror-2.0.tar.gz"))],
                )
                self.assertEqual(
                    self.requests,
                    [("GET", path) for path in paths[paths.index(start) :]]
                    + [("GET", "/mirror-2.0.tar.gz")],
                )

    async def test_cdn_failure_retains_version_from_head_redirects(self):
        self.responses["/latest"] = {
            "status": 302,
            "headers": {"Location": "/app-2.0.tar.gz"},
        }
        self.responses["/app-2.0.tar.gz"] = {
            "status": 302,
            "headers": {"Location": "/cdn/payload"},
        }
        self.head_statuses["/cdn/payload"] = 200
        self.responses["/cdn/payload"] = {"status": 503}
        self.responses["/mirror-2.0.tar.gz"] = {"body": self.content}
        data = self.make_data(str(self.server.make_url("/latest")))

        await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.version, "2.0")
        self.assertEqual(
            data.new_version.url, str(self.server.make_url("/app-2.0.tar.gz"))
        )
        self.assertEqual(
            self.requests,
            [
                ("HEAD", "/latest"),
                ("HEAD", "/app-2.0.tar.gz"),
                ("HEAD", "/cdn/payload"),
                ("GET", "/cdn/payload"),
                ("GET", "/mirror-2.0.tar.gz"),
            ],
        )

    async def test_successful_cdn_download_retains_discovered_version(self):
        paths = ("/latest", "/app-1.0.tar.gz", "/app-2.0.tar.gz", "/cdn/payload")
        for start, target in pairwise(paths):
            self.responses[start] = {"status": 302, "headers": {"Location": target}}
        self.responses[paths[-1]] = {"body": self.content}

        for start in paths[:-1]:
            with self.subTest(start=start):
                self.requests.clear()
                data = self.make_data(str(self.server.make_url(start)))

                await self.checker.check(data)

                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.version, "2.0")
                self.assertEqual(
                    data.new_version.url, str(self.server.make_url(paths[-1]))
                )
                data.update()
                self.assertEqual(
                    data.source["mirror-urls"],
                    [str(self.server.make_url("/mirror-2.0.tar.gz"))],
                )
                expected = [("GET", path) for path in paths[paths.index(start) :]]
                if start == "/latest":
                    expected = [("HEAD", path) for path in paths] + [("GET", paths[-1])]
                self.assertEqual(self.requests, expected)

    async def test_embedded_version_takes_precedence_over_redirect_history(self):
        path = "/app-2.0.AppImage"
        self.responses[path] = {
            "status": 302,
            "headers": {"Location": "/cdn/payload"},
        }
        self.responses["/cdn/payload"] = {"body": self.content}
        for embedded, expected in (("3.0", "3.0"), (None, "2.0")):
            with self.subTest(embedded=embedded):
                data = self.make_data(str(self.server.make_url(path)))
                data.checker_data["pattern"] = r".*/app-([\d.]+)\.AppImage"
                with mock.patch(
                    "src.checkers.urlchecker.utils.extract_appimage_version",
                    new_callable=mock.AsyncMock,
                    return_value=embedded,
                ):
                    await self.checker.check(data)

                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.version, expected)
                data.update()
                self.assertEqual(
                    data.source["mirror-urls"],
                    [str(self.server.make_url(f"/mirror-{expected}.tar.gz"))],
                )

    async def test_versioned_url_falls_back_to_mirror(self):
        path = "/app-2.0.tar.gz"
        mirror_path = "/mirror-2.0.tar.gz"
        self.responses[mirror_path] = {"body": self.content}
        primary = str(self.server.make_url(path))
        data = self.make_data(primary)

        await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.url, primary)
        self.assertEqual(data.new_version.version, "2.0")
        data.update()
        self.assertEqual(data.source["url"], primary)
        self.assertEqual(
            data.source["mirror-urls"], [str(self.server.make_url(mirror_path))]
        )
        self.assertEqual(self.requests, [("GET", path), ("GET", mirror_path)])

    async def test_head_redirect_preserves_embedded_version_extraction(self):
        def extract_version(download):
            download.seek(0)
            self.assertEqual(download.read(), self.content)
            return "2.0"

        for suffix in ("AppImage", "deb"):
            for generic_url in (False, True):
                with self.subTest(suffix=suffix, generic_url=generic_url):
                    self.requests.clear()
                    package_path = f"/latest.{suffix}"
                    self.responses["/latest"] = {
                        "status": 302,
                        "headers": {"Location": package_path},
                    }
                    self.responses[package_path] = {
                        "status": 302,
                        "headers": {"Location": "/cdn/payload"},
                    }
                    self.responses["/cdn/payload"] = {"body": self.content}
                    start = "/latest" if generic_url else package_path
                    data = self.make_data(str(self.server.make_url(start)))
                    del data.checker_data["pattern"]

                    with (
                        mock.patch(
                            "src.checkers.urlchecker.utils.extract_appimage_version",
                            new_callable=mock.AsyncMock,
                            side_effect=extract_version,
                        ) as appimage,
                        mock.patch(
                            "src.checkers.urlchecker.utils.extract_deb_version",
                            side_effect=extract_version,
                        ) as deb,
                    ):
                        await self.checker.check(data)

                    if suffix == "AppImage":
                        appimage.assert_awaited_once()
                        deb.assert_not_called()
                    else:
                        deb.assert_called_once()
                        appimage.assert_not_called()
                    self.assertEqual(data.new_version.version, "2.0")
                    self.assertEqual(
                        data.new_version.url, str(self.server.make_url("/cdn/payload"))
                    )
                    data.update()
                    self.assertEqual(
                        data.source["mirror-urls"],
                        [str(self.server.make_url("/mirror-2.0.tar.gz"))],
                    )
                    expected = [("HEAD", "/latest")] if generic_url else []
                    self.assertEqual(
                        self.requests,
                        expected
                        + [
                            ("HEAD", package_path),
                            ("HEAD", "/cdn/payload"),
                            ("GET", "/cdn/payload"),
                        ],
                    )

    async def test_failed_head_allows_get_to_discover_version(self):
        path = "/app-2.0.tar.gz"
        self.head_statuses["/latest"] = 405
        self.responses["/latest"] = {"status": 302, "headers": {"Location": path}}
        self.responses[path] = {"body": self.content}
        data = self.make_data(str(self.server.make_url("/latest")))

        await self.checker.check(data)

        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.url, str(self.server.make_url(path)))
        self.assertEqual(data.new_version.version, "2.0")
        data.update()
        self.assertEqual(
            data.source["mirror-urls"],
            [str(self.server.make_url("/mirror-2.0.tar.gz"))],
        )
        self.assertEqual(
            self.requests, [("HEAD", "/latest"), ("GET", "/latest"), ("GET", path)]
        )


class TestIsSameVersion(unittest.TestCase):
    def test_invalid_pattern_with_inferred_version(self):
        url = "https://example.com/prog.AppImage"
        nv = _make_version(url=url, version="1.2.3")
        with self.assertRaises(CheckerMetadataError):
            is_same_version({"pattern": r"(.*)(\.AppImage)$"}, url, nv)

    def test_no_pattern_with_inferred_version_falls_back_to_url(self):
        for url, expected in (
            (CURRENT_URL, True),
            ("https://example.com/other", False),
        ):
            with self.subTest(url=url):
                nv = _make_version(url=url, version="1.2.3")
                self.assertEqual(
                    is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, nv), expected
                )

    def test_none_new_version_returns_false(self):
        self.assertFalse(is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, None))

    def test_no_version_same_url_returns_true(self):
        nv = _make_version(url=CURRENT_URL, version=None)
        self.assertTrue(is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, nv))

    def test_no_version_different_url_returns_false(self):
        nv = _make_version(url="https://example.com/prog_1.2.4.tar.gz", version=None)
        self.assertFalse(is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, nv))

    def test_pattern_no_match_on_current_url_same_url(self):
        bad_url = "https://other.example.com/something"
        nv = _make_version(url=bad_url, version="1.2.3")
        self.assertTrue(is_same_version(CHECKER_DATA_WITH_PATTERN, bad_url, nv))

    def test_pattern_no_match_on_current_url_different_url(self):
        bad_url = "https://other.example.com/something"
        nv = _make_version(
            url="https://other.example.com/something-else", version="1.2.4"
        )
        self.assertFalse(is_same_version(CHECKER_DATA_WITH_PATTERN, bad_url, nv))

    def test_pattern_match_same_version(self):
        nv = _make_version(url="https://example.com/prog_1.2.3.tar.gz", version="1.2.3")
        self.assertTrue(is_same_version(CHECKER_DATA_WITH_PATTERN, CURRENT_URL, nv))

    def test_pattern_match_different_version(self):
        nv = _make_version(url="https://example.com/prog_1.2.4.tar.gz", version="1.2.4")
        self.assertFalse(is_same_version(CHECKER_DATA_WITH_PATTERN, CURRENT_URL, nv))


if __name__ == "__main__":
    unittest.main()
