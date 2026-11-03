import unittest

from src.lib.checksums import MultiDigest
from src.lib.externaldata import ExternalBase
from src.lib.sourceupdates import SourceUpdate, format_source_updates


class TestSourceUpdates(unittest.TestCase):
    def file_source(self, checker_data=None):
        data = ExternalBase.from_source(
            "manifest.json",
            {
                "type": "archive",
                "url": "https://example.org/app-1.0.tar.gz",
                "sha256": "abc",
                "x-checker-data": checker_data or {"type": "json"},
            },
        )
        data.new_version = data.current_version._replace(
            url="https://example.org/app-2.0.tar.gz", version="2.0"
        )
        return data

    def test_configured_pattern_and_template(self):
        data = self.file_source(
            {
                "type": "rotating-url",
                "url": "https://example.org/latest.tar.gz",
                "pattern": r"https://example.org/app-([\d.]+)\.tar\.gz",
                "release-url-template": "https://example.org/notes/$version",
            }
        )
        update = SourceUpdate.from_source(data)
        self.assertEqual(update.old_version, "1.0")
        self.assertEqual(update.new_version, "2.0")
        self.assertEqual(update.release_url, "https://example.org/notes/2.0")

    def test_unknown_and_unresolved_links(self):
        data = self.file_source()
        self.assertEqual(SourceUpdate.from_source(data).old_version, "unknown")
        for template in (
            "https://example.org/$version",
            "javascript:alert(1)",
            "https://[invalid",
        ):
            with self.subTest(template=template):
                data.checker_data["release-url-template"] = template
                data.new_version = data.new_version._replace(version=None)
                update = SourceUpdate.from_source(data)
                self.assertIsNone(update.release_url)
                self.assertEqual(update.new_version, "unknown")
        data.checker_data["release-url-template"] = "https://example.org/changelog"
        self.assertEqual(
            SourceUpdate.from_source(data).release_url,
            "https://example.org/changelog",
        )

    def test_checksum_only_update(self):
        data = self.file_source()
        data.current_version = data.current_version._replace(version="1.0")
        data.new_version = data.current_version._replace(
            checksum=MultiDigest(sha256="def")
        )
        update = SourceUpdate.from_source(data)
        self.assertEqual(update.old_version, update.new_version)
        self.assertNotIn("upgrade", format_source_updates([update]))

    def test_previous_release_download_tag(self):
        for arch in ("x86_64", "aarch64"):
            with self.subTest(arch=arch):
                data = self.file_source()
                data.current_version = data.current_version._replace(
                    url="https://github.com/litocpp/lito/releases/download/"
                    f"v0.8.7/lito-v0.8.7-linux-{arch}.tar.gz"
                )
                data.new_version = data.new_version._replace(version="0.8.8")
                update = SourceUpdate.from_source(data)
                self.assertEqual(update.old_version, "tag v0.8.7")
                self.assertEqual(update.new_version, "0.8.8")
                data.current_version = data.current_version._replace(version="0.8.7")
                self.assertEqual(SourceUpdate.from_source(data).old_version, "0.8.7")

    def test_github_release_download(self):
        data = self.file_source()
        data.new_version = data.new_version._replace(
            url="https://github.com/owner/app/releases/download/v2.0/app.tar.gz?raw=1",
            version=None,
        )
        self.assertEqual(
            SourceUpdate.from_source(data).release_url,
            "https://github.com/owner/app/releases/tag/v2.0",
        )
        data.new_version = data.new_version._replace(
            url="https://github.com/owner/app/releases/download/release%2F2/app.zip"
        )
        self.assertEqual(
            SourceUpdate.from_source(data).release_url,
            "https://github.com/owner/app/releases/tag/release%2F2",
        )
        self.assertEqual(SourceUpdate.from_source(data).new_version, "tag release/2")
        data.checker_data["release-url-template"] = "https://example.org/changelog"
        self.assertEqual(
            SourceUpdate.from_source(data).release_url,
            "https://example.org/changelog",
        )
        data.checker_data["release-url-template"] = "https://example.org/$version"
        self.assertIsNone(SourceUpdate.from_source(data).release_url)

    def test_no_guessed_release_links(self):
        data = self.file_source()
        for url in (
            "https://github.com/owner/app/archive/refs/tags/v2.0.tar.gz",
            "https://github.com/owner/app.git",
            "https://github.com/owner/app/releases/latest/download/app.zip",
            "https://example.org/owner/app/releases/download/v2.0/app.zip",
            "https://github.com.evil.test/owner/app/releases/download/v2.0/app.zip",
            "https://github.com@evil.test/owner/app/releases/download/v2.0/app.zip",
            "https://[invalid",
        ):
            with self.subTest(url=url):
                data.new_version = data.new_version._replace(url=url)
                self.assertIsNone(SourceUpdate.from_source(data).release_url)

    def test_git_tag_and_commit_updates(self):
        data = ExternalBase.from_source(
            "manifest.json",
            {
                "type": "git",
                "url": "https://example.org/app.git",
                "tag": "v1.0",
                "commit": "abc",
            },
        )
        data.new_version = data.current_version._replace(commit="def")
        update = SourceUpdate.from_source(data)
        self.assertEqual(update.old_version, "tag v1.0, commit abc")
        self.assertEqual(update.new_version, "tag v1.0, commit def")

    def test_commit_display_preserves_full_revisions(self):
        old_commit = "1234567890abcdef" * 2 + "12345678"
        new_commit = "abcdef1234567890" * 2 + "abcdef12"
        data = ExternalBase.from_source(
            "manifest.json",
            {
                "type": "git",
                "url": "https://example.org/app.git",
                "commit": old_commit,
            },
        )
        data.new_version = data.current_version._replace(commit=new_commit)
        update = SourceUpdate.from_source(data)
        text = format_source_updates([update])
        self.assertIn("| Source | Previous | Updated | Release notes |", text)
        self.assertEqual(update.old_version, "commit 1234567890ab")
        self.assertEqual(update.new_version, "commit abcdef123456")
        self.assertEqual(data.current_version.commit, old_commit)
        self.assertEqual(data.new_version.commit, new_commit)
        data.update()
        self.assertEqual(data.source["commit"], new_commit)

    def test_checker_release_link_and_template_precedence(self):
        data = ExternalBase.from_source(
            "git.json",
            {
                "type": "git",
                "url": "https://github.com/owner/app.git",
                "commit": "abc",
            },
        )
        data.new_version = data.current_version._replace(commit="def", version="2")
        data.new_release_url = "https://github.com/owner/app/releases/tag/v2"
        update = SourceUpdate.from_source(data)
        text = format_source_updates([update])
        self.assertIn(
            "[Release notes](https://github.com/owner/app/releases/tag/v2)", text
        )
        data.checker_data["release-url-template"] = "https://example.org/$version"
        self.assertEqual(
            SourceUpdate.from_source(data).release_url, "https://example.org/2"
        )

    def test_architecture_and_markdown(self):
        data = self.file_source()
        data.source["only-arches"] = ["aarch64"]
        self.assertIn("(aarch64)", SourceUpdate.from_source(data).source)
        update = SourceUpdate(
            "a|b\n<script>",
            "[old]",
            "*new*",
            "https://example.org/notes/(2)|x y",
        )
        text = format_source_updates([update, update])
        self.assertIn(r"a\|b &lt;script&gt;", text)
        self.assertIn(r"\[old\]", text)
        self.assertIn("https://example.org/notes/%282%29%7Cx%20y", text)
        self.assertEqual(text.count("[Release notes]"), 2)
        self.assertEqual(format_source_updates([]), "")
