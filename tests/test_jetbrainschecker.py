import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lxml import etree

from src.checkers.jetbrainschecker import JetBrainsChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerMetadataError
from src.lib.externaldata import ExternalBase, ExternalFile
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.jetbrains.PhpStorm.json")


class TestJetBrainsChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    @upstream_test
    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        data = self._find_by_filename(ext_data, "phpstorm.tar.gz")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "phpstorm.tar.gz")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.jetbrains\.com/webide/PhpStorm-.+\.tar\.gz$",
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

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


class TestJetBrainsValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = mock.MagicMock()
        self.checker = JetBrainsChecker(self.session)

    def source(self, **metadata):
        return ExternalBase.from_source(
            "app.json",
            {
                "type": "archive",
                "url": "https://example.org/app-1.tar.gz",
                "sha256": "old",
                "x-checker-data": {"type": "jetbrains", **metadata},
            },
        )

    async def test_supported_release_types(self):
        for release_type in ("release", "eap", "rc", "preview"):
            with self.subTest(release_type=release_type):
                await self.checker.validate_checker_data(
                    self.source(code="IIU", **{"release-type": release_type})
                )
        self.session.get.assert_not_called()

    async def test_release_type_is_optional(self):
        await self.checker.validate_checker_data(self.source(code="IIU"))

    async def test_invalid_release_types(self):
        for release_type in (
            "stable",
            "beta",
            "EAP",
            "",
            "release,eap",
            None,
            1,
            True,
            [],
            {},
        ):
            with self.subTest(release_type=release_type):
                with self.assertRaises(CheckerMetadataError) as raised:
                    await self.checker.validate_checker_data(
                        self.source(code="IIU", **{"release-type": release_type})
                    )
                self.assertEqual(
                    list(raised.exception.__cause__.path), ["release-type"]
                )
        self.session.get.assert_not_called()

    async def test_code_is_required(self):
        with self.assertRaises(CheckerMetadataError):
            await self.checker.validate_checker_data(
                self.source(**{"release-type": "release"})
            )

    async def test_inline_release_notes_must_be_boolean(self):
        with self.assertRaises(CheckerMetadataError):
            await self.checker.validate_checker_data(
                self.source(code="IIU", **{"include-release-notes": "true"})
            )


class TestJetBrainsReleaseLinks(unittest.IsolatedAsyncioTestCase):
    NOTES = (
        "<h1>IntelliJ IDEA 2026.3 is now available!</h1>\n"
        '<p class="note">Better <em>search</em> &amp; '
        "<code>Map&lt;K, V&gt;</code> support.</p>\n"
        '<ol><li><a href="https://example.org/issue">Fixed startup on Linux.</a>'
        "</li></ol>\n"
        '<p>For more details, please refer to the <a href="https://example.org">'
        "release notes</a>.</p>\n"
        "<script>do not copy this</script><!-- or this -->\n"
        '<ul><li><a href="https://example.org/download">Download IntelliJ IDEA</a>'
        "</li></ul>"
    )

    async def checked_source(
        self,
        notes,
        *,
        link="https://example.org/notes/2026.3",
        repair=False,
        session=None,
        **options,
    ):
        source = ExternalBase.from_source(
            "app.json",
            {
                "type": "archive",
                "url": "https://example.org/app-2026.2.tar.gz",
                "sha256": "old",
                "x-checker-data": {"type": "jetbrains", "code": "IIU", **options},
            },
        )
        selected = {
            "version": "2026.3",
            "date": "2026-11-05",
            "notesLink": link,
            "downloads": {
                "linux": {
                    "link": "https://example.org/app-2026.3.tar.gz",
                    "size": 42,
                    "checksumLink": "https://example.org/app-2026.3.sha256",
                },
            },
        }
        if repair:
            source.current_version = ExternalFile(
                url=selected["downloads"]["linux"]["link"],
                checksum=MultiDigest(sha256="old"),
                size=42,
                version=selected["version"],
                timestamp=None,
            )
        if notes is not mock.sentinel.missing:
            selected["whatsnew"] = notes
        metadata = mock.AsyncMock()
        metadata.json.return_value = {
            "IIU": [selected, {**selected, "whatsnew": "<p>Wrong build notes</p>"}]
        }
        checksum = mock.AsyncMock()
        checksum.text.return_value = "abc  app-2026.3.tar.gz"
        if session is None:
            session = mock.MagicMock()
        session.get.return_value.__aenter__ = mock.AsyncMock(
            side_effect=[metadata, checksum]
        )
        await JetBrainsChecker(session).check(source)
        return source

    def assert_release_description(self, source, expected):
        self.assertIsNotNone(source.new_release_description)
        self.assertEqual(
            [
                etree.tostring(block, encoding="unicode")
                for block in source.new_release_description
            ],
            expected,
        )

    async def test_inline_notes_need_no_extra_requests(self):
        session = mock.MagicMock()
        await self.checked_source(
            self.NOTES, session=session, **{"include-release-notes": True}
        )
        self.assertEqual(
            session.get.call_args_list,
            [
                mock.call(
                    "https://data.services.jetbrains.com/products/releases",
                    params={"code": "IIU", "latest": "true", "type": "release"},
                ),
                mock.call("https://example.org/app-2026.3.sha256"),
            ],
        )

    async def test_inline_release_notes_require_stable_opt_in(self):
        for options in (
            {},
            {"include-release-notes": False},
            {"include-release-notes": True, "release-type": "rc"},
        ):
            with self.subTest(options=options):
                source = await self.checked_source(self.NOTES, **options)
                self.assertIsNone(source.new_release_description)

    async def test_inline_release_notes_are_normalized(self):
        source = await self.checked_source(
            self.NOTES, link=None, **{"include-release-notes": True}
        )
        self.assertIsNone(source.new_release_url)
        self.assert_release_description(
            source,
            [
                "<p>Better <em>search</em> &amp; "
                "<code>Map&lt;K, V&gt;</code> support.</p>",
                "<ol><li>Fixed startup on Linux.</li></ol>",
            ],
        )

    async def test_inline_release_notes_are_ignored_for_checksum_repairs(self):
        source = await self.checked_source(
            self.NOTES, repair=True, **{"include-release-notes": True}
        )
        self.assertFalse(source.has_version_changed)
        self.assertIsNone(source.new_release_description)

    async def test_inline_release_notes_keep_upgrade_qualifiers(self):
        qualifier = "What\u2019s new in 2026.3 when upgrading from 2026.1 or earlier:"
        source = await self.checked_source(
            "<div>Fixed <code>search</code> on Linux.</div>"
            f"<h5>{qualifier}</h5>"
            "<ul><li>Support for a new project format.</li></ul>",
            **{"include-release-notes": True},
        )
        self.assert_release_description(
            source,
            [
                "<p>Fixed <code>search</code> on Linux.</p>",
                f"<p>{qualifier}</p>",
                "<ul><li>Support for a new project format.</li></ul>",
            ],
        )

    async def test_inline_notes_preserve_blocks_inside_wrappers(self):
        source = await self.checked_source(
            "<div><p>Fixed startup on Linux.</p>"
            "<div><ul><li>Search works offline.</li></ul></div></div>",
            **{"include-release-notes": True},
        )
        self.assert_release_description(
            source,
            [
                "<p>Fixed startup on Linux.</p>",
                "<ul><li>Search works offline.</li></ul>",
            ],
        )

    async def test_inline_notes_keep_prose_after_a_release_notes_pointer(self):
        source = await self.checked_source(
            "<p>For details, see the release notes. This update fixes startup on Linux."
            "</p>",
            **{"include-release-notes": True},
        )
        self.assertIsNotNone(source.new_release_description)
        self.assertIn(
            "This update fixes startup on Linux.",
            source.new_release_description[0].text,
        )

    async def test_unusable_notes_leave_url_and_update_intact(self):
        for notes in (
            mock.sentinel.missing,
            None,
            42,
            " ",
            "x" * 20_000,
            "<h1>IntelliJ IDEA 2026.2 is now available!</h1><p>Improved search.</p>",
            "<h1>IntelliJ IDEA 2026.2 Release Notes</h1><p>Improved search.</p>",
            "<h1>Changes for IntelliJ IDEA 2026.2</h1><p>Improved search.</p>",
            "<h3>What's new in 2026.3-RC1:</h3><ul><li>Improved search.</li></ul>",
            "<h3>What\u2019s new in 2026.2 when upgrading from 2026.1:</h3>"
            "<ul><li>Improved search.</li></ul>",
            "<p>IntelliJ IDEA 2026.3 is out!</p><p>See the release notes.</p>",
            "<p><a href='https://example.org'>More information</a></p>",
            '<p>Visit our <a href="https://example.org">release notes</a> '
            "to learn about the changes.</p>",
            '<p><a href="https://example.org">Here are full release notes.</a></p>',
            "<p>Invalid XML character: \x01</p>",
            "<ul><li>Fix<ul><li>Details</li></ul></li></ul>",
            '<ol start="3" reversed><li>First fix.</li><li>Second fix.</li></ol>',
            '<ol><li value="3">Fix.</li></ol>',
            '<ol><li><a href="https://example.org">Read more.</a></li>'
            "<li>Second fix.</li></ol>",
            "<p>Incomplete note" + "<em>" * 300 + "truncated" + "</em>" * 300 + "</p>",
            "## Release notes [read more](https://example.org)",
        ):
            with self.subTest(notes=repr(notes)[:100]):
                source = await self.checked_source(
                    notes, **{"include-release-notes": True}
                )
                self.assertEqual(source.new_version.version, "2026.3")
                self.assertIsNone(source.new_release_description)
                self.assertEqual(
                    source.new_release_url, "https://example.org/notes/2026.3"
                )

    async def test_inline_notes_reach_new_metainfo_release(self):
        source = await self.checked_source(
            self.NOTES,
            **{
                "include-release-notes": True,
                "is-main-source": True,
                "release-url-template": "https://example.org/releases/$version",
            },
        )
        other = await self.checked_source(
            "<p>Dependency notes</p>", **{"include-release-notes": True}
        )
        manifest = mock.Mock()
        manifest.app_id = "org.example.Sample"
        manifest._root_manifest_path = "app.json"
        manifest._blocked_update_sources = set()
        manifest._find_repository.return_value = manifest._repository
        manifest.get_external_data.return_value = [other, source]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "org.example.Sample.metainfo.xml"
            path.write_text(
                "<component>\n"
                "  <id>org.example.Sample</id>\n"
                "  <name>Sample</name>\n"
                "  <summary>Demonstrates release metadata</summary>\n"
                "  <metadata_license>CC0-1.0</metadata_license>\n"
                "  <project_license>MIT</project_license>\n"
                '  <url type="homepage">https://example.org/</url>\n'
                "  <releases>\n"
                '    <release version="2026.2" date="2026-07-09">\n'
                "      <description><p>Old release notes.</p></description>\n"
                "    </release>\n"
                "  </releases>\n"
                "</component>\n"
            )
            before = path.read_text()
            new_version = source.new_version
            with mock.patch("src.manifest.find_appdata_file", return_value=str(path)):
                source.new_version = None
                ManifestChecker._update_appdata(manifest)
                self.assertEqual(path.read_text(), before)
                source.new_version = new_version
                ManifestChecker._update_appdata(manifest)
            new, old = etree.parse(path).findall("releases/release")
            self.assertEqual(new.get("version"), "2026.3")
            self.assertEqual(new.get("date"), "2026-11-05")
            self.assertEqual(new.findtext("url"), "https://example.org/releases/2026.3")
            self.assertEqual(new.findtext("description/p/em"), "search")
            self.assertEqual(
                new.findtext("description/ol/li"), "Fixed startup on Linux."
            )
            self.assertEqual(old.findtext("description/p"), "Old release notes.")
            if shutil.which("appstreamcli"):
                validation = subprocess.run(
                    ["appstreamcli", "validate", "--no-net", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(validation.returncode, 0, validation.stdout)

    async def test_release_notes_from_selected_release(self):
        for notes in ("https://example.org/notes/2", None, "javascript:alert(1)", 123):
            with self.subTest(notes=notes):
                source = await self.checked_source(mock.sentinel.missing, link=notes)
                expected = notes if notes == "https://example.org/notes/2" else None
                self.assertEqual(source.new_release_url, expected)


if __name__ == "__main__":
    unittest.main()
