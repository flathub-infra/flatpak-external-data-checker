import unittest

from src.lib.checksums import MultiDigest
from src.lib.errors import SourceLoadError
from src.lib.externaldata import BuilderModule, ExternalBase
from src.lib.sourceupdates import SourceUpdate, format_source_updates


class TestSourceUpdates(unittest.TestCase):
    def test_numeric_git_revisions(self):
        for field in ("tag", "commit", "branch"):
            for value in (0, 123, 1.0):
                with self.subTest(field=field, value=value):
                    source = {
                        "type": "git",
                        "url": "https://github.com/owner/app.git",
                        field: value,
                    }
                    data = ExternalBase.from_source("test.yml", source)
                    self.assertEqual(getattr(data.current_version, field), str(value))
                    self.assertEqual(source[field], value)
                    new = data.current_version._replace(**{field: str(value)})
                    data.set_new_version(new)
                    self.assertIsNone(data.new_version)
                    self.assertTrue(data.current_version.is_same_version(new))
                    data.new_version = new._replace(**{field: "456"})
                    update = SourceUpdate.from_source(data)
                    self.assertEqual(
                        update.comparison_url,
                        f"https://github.com/owner/app/compare/{value}...456"
                        if field in ("tag", "commit")
                        else None,
                    )
                    data.update()
                    self.assertEqual(source[field], "456")

    def test_unsupported_git_revisions(self):
        for field in ("tag", "commit", "branch"):
            for value in (True, False, [], {}, float("inf"), float("nan")):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(SourceLoadError):
                        ExternalBase.from_source(
                            "test.yml",
                            {
                                "type": "git",
                                "url": "https://github.com/o/r",
                                field: value,
                            },
                        )

    def test_git_missing_revisions_and_same_numeric_tag(self):
        for repo in ("https://github.com/owner/app.git", "https://example.org/app.git"):
            with self.subTest(repo=repo):
                data = ExternalBase.from_source(
                    "test.yml", {"type": "git", "url": repo}
                )
                data.new_version = data.current_version._replace(tag="v2")
                update = SourceUpdate.from_source(data)
                self.assertEqual(update.old_version, "unknown")
                self.assertIsNone(update.comparison_url)
                self.assertIsNone(update.release_url)

                data = ExternalBase.from_source(
                    "test.yml",
                    {"type": "git", "url": repo, "tag": 1.0, "commit": "abc"},
                )
                data.set_new_version(
                    data.current_version._replace(
                        tag="1.0", commit="def", version="1.0"
                    )
                )
                self.assertTrue(data.state & data.State.BROKEN)
                self.assertFalse(data.state & data.State.OUTDATED)
                self.assertFalse(data.has_version_changed)
                self.assertEqual(
                    SourceUpdate.from_source(data).comparison_url,
                    "https://github.com/owner/app/compare/abc...def"
                    if "github.com" in repo
                    else None,
                )

    def test_source_names(self):
        for module_name, filename, expected in (
            ("ostree", "ostree", "ostree"),
            ("appstream", "appstream.git", "appstream"),
            ("app", "dependency.git", "app: dependency.git"),
            (None, "app.git", "app.git"),
        ):
            with self.subTest(module=module_name, filename=filename):
                module = (
                    BuilderModule.from_manifest("manifest.json", {"name": module_name})
                    if module_name
                    else None
                )
                data = ExternalBase.from_source(
                    "manifest.json",
                    {
                        "type": "git",
                        "url": f"https://example.org/{filename}",
                        "tag": "v1",
                    },
                    module,
                )
                data.new_version = data.current_version._replace(tag="v2")
                self.assertEqual(SourceUpdate.from_source(data).source, expected)
                data.source["only-arches"] = ["aarch64"]
                self.assertEqual(
                    SourceUpdate.from_source(data).source, f"{expected} (aarch64)"
                )

    def test_comparison_links(self):
        for host, project, route in (
            ("github.com", "owner/app", "compare"),
            ("codeberg.org", "owner/app", "compare"),
            ("gitlab.com", "group/subgroup/app", "-/compare"),
        ):
            with self.subTest(host=host):
                repo = f"https://{host}/{project}"
                data = ExternalBase.from_source(
                    "manifest.json",
                    {"type": "git", "url": repo + ".git", "tag": "release/1"},
                )
                data.new_version = data.current_version._replace(tag="release/2")
                update = SourceUpdate.from_source(data)
                self.assertEqual(
                    update.comparison_url,
                    f"{repo}/{route}/release%2F1...release%2F2",
                )
                text = format_source_updates([update])
                self.assertIn(
                    "[<code>tag release/1</code> → <code>tag release/2</code>]"
                    f"({update.comparison_url})",
                    text,
                )
                self.assertEqual(text.count(update.comparison_url), 1)
                self.assertNotIn("| Changes |", text)
                data.current_version = data.current_version._replace(commit="a" * 40)
                data.new_version = data.current_version._replace(commit="b" * 40)
                self.assertEqual(
                    SourceUpdate.from_source(data).comparison_url,
                    f"{repo}/{route}/{'a' * 40}...{'b' * 40}",
                )
                data.new_version = data.current_version
                self.assertIsNone(SourceUpdate.from_source(data).comparison_url)
                data.new_version = data.current_version._replace(
                    url="https://github.com/other/app.git", tag="v3"
                )
                self.assertIsNone(SourceUpdate.from_source(data).comparison_url)

    def test_download_comparison_links(self):
        data = self.file_source()
        base = "https://github.com/owner/app"
        data.current_version = data.current_version._replace(
            url=f"{base}/releases/download/release%2F1/app.zip"
        )
        data.new_version = data.new_version._replace(
            url=f"{base}/releases/download/release%2F2/app.zip"
        )
        self.assertEqual(
            SourceUpdate.from_source(data).comparison_url,
            f"{base}/compare/release%2F1...release%2F2",
        )
        data.new_version = data.new_version._replace(
            url="https://github.com/other/app/releases/download/v2/app.zip"
        )
        self.assertIsNone(SourceUpdate.from_source(data).comparison_url)

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
        text = format_source_updates([update])
        self.assertIn(r"| app\-1\.0\.tar\.gz |", text)
        self.assertNotIn(data.new_version.url, text)

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

    def test_previous_archive_tag(self):
        data = ExternalBase.from_source(
            "manifest.json",
            {
                "type": "archive",
                "url": "https://github.com/benhoyt/inih/archive/refs/tags/r58.tar.gz",
                "sha256": "abc",
                "x-checker-data": {"type": "anitya", "project-id": 15151},
            },
        )
        data.new_version = data.current_version._replace(
            url="https://github.com/benhoyt/inih/archive/refs/tags/r63.tar.gz",
            version="63",
        )
        update = SourceUpdate.from_source(data)
        self.assertEqual(update.old_version, "tag r58")
        self.assertEqual(update.new_version, "63")
        self.assertIn(
            "<code>tag r58</code> → <code>63</code>",
            format_source_updates([update]),
        )
        self.assertIsNone(update.release_url)
        self.assertIsNone(update.comparison_url)

    def test_archive_tag_encoding_and_version_precedence(self):
        for filename, tag, rendered in (
            ("r58.tar.gz", "r58", "r58"),
            ("r58.zip", "r58", "r58"),
            ("release%2Fr58.tar.gz", "release/r58", "release/r58"),
            ("release/r58.zip", "release/r58", "release/r58"),
            ("r58%252Ffinal.zip", "r58%2Ffinal", "r58%2Ffinal"),
            ("r58.tar.gz.tar.gz", "r58.tar.gz", r"r58\.tar\.gz"),
            ("r58%2Ezip.zip", "r58.zip", r"r58\.zip"),
            ("r58%2B%C3%A9.zip", "r58+é", r"r58\+é"),
            ("r58%7C%3Ctag%3E%26.zip", "r58|<tag>&", r"r58\|&lt;tag&gt;&amp;"),
        ):
            with self.subTest(filename=filename):
                data = self.file_source()
                data.current_version = data.current_version._replace(
                    url="https://github.com/benhoyt/inih/archive/refs/tags/"
                    + filename
                    + "?download=1#fragment"
                )
                data.new_version = data.current_version
                update = SourceUpdate.from_source(data)
                self.assertEqual(update.old_version, f"tag {tag}")
                self.assertEqual(update.new_version, f"tag {tag}")
                self.assertIn(
                    f"<code>tag {rendered}</code> → <code>tag {rendered}</code>",
                    format_source_updates([update]),
                )
                data.current_version = data.current_version._replace(version="58")
                self.assertEqual(SourceUpdate.from_source(data).old_version, "58")

        data = self.file_source(
            {"type": "rotating-url", "pattern": r"https://.*/r(\d+)\.tar\.gz"}
        )
        data.current_version = data.current_version._replace(
            url="https://github.com/benhoyt/inih/archive/refs/tags/r58.tar.gz"
        )
        self.assertEqual(SourceUpdate.from_source(data).old_version, "58")

    def test_ambiguous_and_untrusted_archives(self):
        for url in (
            "https://github.com/owner/app/archive/refs/heads/main.tar.gz",
            "https://github.com/owner/app/archive/" + "a" * 40 + ".zip",
            "https://github.com/owner/app/archive/v1.tar.gz",
            "https://github.com/owner/app/archive/refs/tags/.tar.gz",
            "https://github.com/owner/app/archive/refs/tags/v1.tar.xz",
            "https://github.com/owner/app/archive/refs/tags/v1.zip/extra",
            "https://github.com/owner/app/archive/refs/tags/../heads/main.tar.gz",
            "https://github.com/owner/app/archive/refs/tags/%2e%2e/heads/main.zip",
            "https://github.com/owner/app/archive/refs/tags/release/../../heads/main.zip",
            "https://github.com/owner/app/archive/refs/tags/..%2Fheads%2Fmain.zip",
            "https://github.com/owner/app/archive/refs/tags/./v1.zip",
            "https://gitlab.com/owner/app/-/archive/v1/app-v1.tar.gz",
            "https://codeberg.org/owner/app/archive/v1.tar.gz",
            "https://api.github.com/repos/owner/app/tarball/v1",
            "https://codeload.github.com/owner/app/zip/v1",
            "https://example.org/owner/app/archive/refs/tags/v1.zip",
            "https://github.com.evil.test/owner/app/archive/refs/tags/v1.zip",
            "https://github.com@evil.test/owner/app/archive/refs/tags/v1.zip",
            "https://evil.test@github.com/owner/app/archive/refs/tags/v1.zip",
            "https://github.com/owner%2Fevil/app/archive/refs/tags/v1.zip",
            "ftp://github.com/owner/app/archive/refs/tags/v1.zip",
            "https://[invalid",
        ):
            with self.subTest(url=url):
                data = self.file_source()
                data.current_version = data.current_version._replace(url=url)
                data.new_version = data.current_version
                update = SourceUpdate.from_source(data)
                self.assertEqual(update.old_version, "unknown")
                self.assertEqual(update.new_version, "unknown")

    def test_exiv2_release_asset_to_archive(self):
        data = self.file_source({"type": "anitya", "project-id": 769})
        data.current_version = data.current_version._replace(
            url="https://github.com/Exiv2/exiv2/releases/download/"
            "v0.28.0/exiv2-0.28.0-Source.tar.gz"
        )
        data.new_version = data.new_version._replace(
            url="https://github.com/Exiv2/exiv2/archive/refs/tags/v0.28.9.tar.gz",
            version="0.28.9",
        )
        update = SourceUpdate.from_source(data)
        self.assertEqual(update.old_version, "tag v0.28.0")
        self.assertEqual(update.new_version, "0.28.9")
        self.assertIn(
            r"<code>tag v0\.28\.0</code> → <code>0\.28\.9</code>",
            format_source_updates([update]),
        )
        self.assertIsNone(update.release_url)
        self.assertIsNone(update.comparison_url)

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
        self.assertIn("| Source | Change | Release notes |", text)
        self.assertIn("<code>commit 1234567890ab</code> →", text)
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
        self.assertIn(r"<code>\[old\]</code> → <code>\*new\*</code>", text)
        self.assertIn("https://example.org/notes/%282%29%7Cx%20y", text)
        self.assertEqual(text.count("[Release notes]"), 2)
        self.assertEqual(format_source_updates([]), "")
