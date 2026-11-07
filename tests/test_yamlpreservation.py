import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.lib.utils import dump_manifest, read_manifest


class TestYamlPreservation(unittest.TestCase):
    def roundtrip(self, original, update):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(original)
            contents, header = read_manifest(path)
            update(contents)
            dump_manifest(contents, path, header)
            self.assertEqual(read_manifest(path)[0], contents)
            return path.read_text()

    def test_freerdp_update_preserves_full_manifest(self):
        # Base of https://github.com/flathub/io.github.mfat.sshpilot/pull/202:
        # 0ae7c67c5f4ac98541e82a4a5026797ed77bfbd2
        original = Path(__file__).with_name("io.github.mfat.sshpilot.yaml").read_text()
        old_url = "https://pub.freerdp.com/releases/freerdp-3.32.1.tar.xz"
        new_url = "https://pub.freerdp.com/releases/freerdp-3.33.0.tar.xz"
        old_hash = "4dd5bf474deff7a2a82dff7edca4ea0a608da376a9698514901feab3637711e8"
        new_hash = "9cea38e972e3775e1ce83b30e9ae0cbf8d00af1c708c640e6244fc81bdb1e08b"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(original)
            contents, header = read_manifest(path)
            module = next(
                m
                for m in contents["modules"]
                if isinstance(m, dict) and m.get("name") == "freerdp"
            )
            module["sources"][0].update(url=new_url, sha256=new_hash)
            dump_manifest(contents, path, header)
            self.assertEqual(
                path.read_text(),
                original.replace(old_url, new_url).replace(old_hash, new_hash),
            )

    def test_scalar_update_preserves_surroundings(self):
        body = """# A comment before the document
app-id: "example.App"
runtime-version: '51'
date: "2024-07-02"
number: '00123'

modules:
  - name: test
    build-commands:
    - |-
      printf '%s\\n' "hello"
      # Inside a literal block
    - >-
      echo one
      two
    sources:
      - type: archive
        url: 'https://example.com/old.tar.xz' # Keep this comment
        sha256: "old-hash"

# Final comment
"""
        for header in ("", "---\n", "# Header comment\n---\n"):
            with self.subTest(header=header):
                original = header + body
                output = self.roundtrip(
                    original,
                    lambda data: data["modules"][0]["sources"][0].update(
                        url="https://example.com/new.tar.xz", sha256="00123"
                    ),
                )
                self.assertEqual(
                    output,
                    original.replace("old.tar.xz", "new.tar.xz").replace(
                        '"old-hash"', '"00123"'
                    ),
                )

    def test_changed_strings_remain_strings(self):
        for value in ("00123", "2024-07-02", "true", "null", "a: b", "a # b"):
            for quotes in ("", "'", '"'):
                with self.subTest(value=value, quotes=quotes):
                    original = f"tag: {quotes}old{quotes} # Keep\n"
                    output = self.roundtrip(
                        original, lambda data, value=value: data.update(tag=value)
                    )
                    self.assertIn(" # Keep\n", output)
                    if quotes:
                        self.assertEqual(output, original.replace("old", value))

    def test_structural_changes_keep_quotes(self):
        original = "name: 'test'\nsources:\n  - type: git\n    tag: old\n"
        output = self.roundtrip(
            original,
            lambda data: data["sources"][0].update(commit="abc123"),
        )
        self.assertEqual(output, original + "    commit: abc123\n")

    def test_unchanged_typed_scalar_spellings_are_preserved(self):
        original = (
            "builddir: True\n"
            "disabled: FALSE\n"
            "optional: ~\n"
            "empty: Null\n"
            "count: +12\n"
            "ratio: .INF\n"
            "unknown: .NaN\n"
            "date: 2024-07-02\n"
            "timestamp: 2024-07-02T01:02:03+00:00\n"
            "url: old\n"
        )
        output = self.roundtrip(original, lambda data: data.update(url="new"))
        self.assertEqual(output, original.replace("url: old", "url: new"))

    def test_unchanged_yaml_11_scalar_spellings_are_preserved(self):
        original = "%YAML 1.1\n---\nenabled: YES\ncount: 012\nurl: old\n"
        output = self.roundtrip(original, lambda data: data.update(url="new"))
        self.assertEqual(output, original.replace("url: old", "url: new"))

    def test_scalar_type_changes_are_not_treated_as_equal(self):
        for old, new, expected in (
            ("true", 1, "1"),
            ("1", True, "true"),
            ("1", "1", "'1'"),
            ("2024-07-02", "2024-07-02", "'2024-07-02'"),
            ("null", "null", "'null'"),
            ("1", 2, "2"),
        ):
            with self.subTest(old=old, new=new):
                output = self.roundtrip(
                    f"value: {old}\n",
                    lambda data, new=new: data.update(value=new),
                )
                self.assertEqual(output, f"value: {expected}\n")

    def test_changed_block_scalar(self):
        original = "name: 'test'\ncommand: |-\n  echo old\n"
        output = self.roundtrip(
            original, lambda data: data.update(command="echo new\necho two")
        )
        self.assertEqual(output, "name: 'test'\ncommand: |-\n  echo new\n  echo two\n")

    def test_alias_update(self):
        original = "sources:\n  - &source\n    url: 'old'\n  - *source\n"
        output = self.roundtrip(
            original, lambda data: data["sources"][0].update(url="new")
        )
        self.assertIn("url: 'new'", output)
        self.assertIn("*source", output)

    def test_yaml_directive_is_per_manifest(self):
        first = "%YAML 1.1\n---\nname: 'first'\n"
        second = "name: second\nvalue: yes\n"
        for original in (first, second, first, second):
            with self.subTest(original=original):
                output = self.roundtrip(original, lambda data: data.update(added="new"))
                self.assertEqual(output, original + "added: new\n")

    def test_replacements_are_validated_in_flow_context(self):
        for value in ("one,two", "one]two"):
            with self.subTest(value=value):
                output = self.roundtrip(
                    "sources: [{url: old}]\n",
                    lambda data, value=value: data.update(sources=[{"url": value}]),
                )
                self.assertEqual(output, f"sources:\n  - url: {value}\n")

    def test_editorconfig_and_file_isolation(self):
        long_value = " ".join(["word"] * 12)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".editorconfig").write_text(
                "root = true\n\n[narrow.yaml]\nmax_line_length = 30\n"
                "indent_style = space\nindent_size = 8\n"
            )
            narrow = root / "narrow.yaml"
            mixed = root / "mixed.yaml"
            original = '---\nitems:\n- "keep"\nurl: old # Keep\n'
            for paths in ((narrow, mixed), (mixed, narrow)):
                with self.subTest(paths=paths):
                    narrow.write_text("description: old\n")
                    mixed.write_text(original)
                    # Read both before writing either, as multi-file manifests do.
                    documents = {path: read_manifest(path) for path in paths}
                    for path in paths:
                        contents, header = documents[path]
                        if path == narrow:
                            contents["description"] = long_value
                        else:
                            contents["url"] = "new"
                        dump_manifest(contents, path, header)
                        self.assertEqual(read_manifest(path)[0], contents)
                    self.assertEqual(mixed.read_text(), original.replace("old", "new"))
                    self.assertEqual(
                        narrow.read_text(),
                        "description: word word word word\n"
                        "  word word word word word word\n"
                        "  word word\n",
                    )

    def test_noop_preserves_document_boundaries(self):
        for original in (
            "# Header\n---\nname: 'test'\n...\n\n",
            "name: 'test'",
        ):
            with self.subTest(original=original):
                self.assertEqual(self.roundtrip(original, lambda data: None), original)

    def test_explicit_width_reflows_unchanged_scalar(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.yaml"
            original = (
                "description: word word word word word word word word"
                " word word word word\n"
                "url: old\n"
            )
            for width in ("30", "80", "unset", "notanumber"):
                with self.subTest(width=width):
                    (root / ".editorconfig").write_text(
                        f"root = true\n[*.yaml]\nmax_line_length = {width}\n"
                    )
                    path.write_text(original)
                    contents, header = read_manifest(path)
                    contents["url"] = "new"
                    dump_manifest(contents, path, header)
                    expected = (
                        "description: word word word word\n"
                        "  word word word word word word\n"
                        "  word word\nurl: new\n"
                        if width == "30"
                        else original.replace("url: old", "url: new")
                    )
                    self.assertEqual(path.read_text(), expected)

    def test_explicit_width_preserves_layout_when_no_reflow_is_needed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.yaml"
            (root / ".editorconfig").write_text(
                "root = true\n[*.yaml]\nmax_line_length = 80\n"
            )
            original = 'items:\n- "keep"\nbuilddir: True\nurl: old\n'
            path.write_text(original)
            contents, header = read_manifest(path)
            contents["url"] = "new"
            dump_manifest(contents, path, header)
            self.assertEqual(path.read_text(), original.replace("url: old", "url: new"))
