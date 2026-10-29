import os
import unittest
from unittest import mock
from unittest.mock import MagicMock

from src.checkers.urlchecker import URLChecker, extract_version, is_same_version
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerMetadataError
from src.lib.externaldata import ExternalData
from src.lib.utils import init_logging
from src.manifest import ManifestChecker


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
            (r".*\.AppImage$", r"contains 0 group\(s\) instead of 1"),
            (r"(.*)(\.AppImage)$", r"contains 2 group\(s\) instead of 1"),
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

    def test_no_pattern(self):
        self.assertIsNone(extract_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL))

    def test_no_match(self):
        self.assertIsNone(
            extract_version(CHECKER_DATA_WITH_PATTERN, "https://example.com/other")
        )


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
            for pattern in (r".*\.AppImage$", r"(.*)(\.AppImage)$", "["):
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


class TestIsSameVersion(unittest.TestCase):
    def test_invalid_pattern_with_inferred_version(self):
        url = "https://example.com/prog.AppImage"
        nv = _make_version(url=url, version="1.2.3")
        with self.assertRaises(CheckerMetadataError):
            is_same_version({"pattern": r".*\.AppImage$"}, url, nv)

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
