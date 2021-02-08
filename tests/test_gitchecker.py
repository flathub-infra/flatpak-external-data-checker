import copy
import os
import unittest

from src.checker import ManifestChecker
from src.lib.externaldata import ExternalGitRepo, ExternalGitRef
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.virustotal.Uploader.yml")


class TestGitChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check_and_update(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        self.assertEqual(len(ext_data), 8)
        for data in ext_data:
            self.assertIsInstance(data, ExternalGitRepo)
            self.assertIsInstance(data.current_version, ExternalGitRef)
            if data.filename == "jansson.git":
                self.assertEqual(data.state, data.State.UNKNOWN)
                self.assertIsNone(data.new_version)
            elif data.filename == "c-vtapi.git":
                self.assertEqual(data.state, data.State.BROKEN)
                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertNotEqual(
                    data.current_version.commit, data.new_version.commit
                )
                self.assertFalse(data.current_version.matches(data.new_version))
            elif data.filename == "qt-virustotal-uploader.git":
                self.assertEqual(data.state, data.State.VALID)
                self.assertIsNone(data.new_version)
            elif data.filename == "protobuf-c.git":
                self.assertEqual(data.state, data.State.UNKNOWN)
                self.assertIsNone(data.new_version)
            elif data.filename == "yara.git":
                self.assertEqual(data.state, data.State.BROKEN)
                self.assertIsNone(data.new_version)
            elif data.filename == "yara-python.git":
                self.assertEqual(data.state, data.State.UNKNOWN)
                self.assertIsNone(data.new_version)
            elif data.filename == "vt-py.git":
                self.assertEqual(data.state, data.State.VALID)
                self.assertIsNone(data.new_version)
            elif data.filename == "extra-cmake-modules.git":
                self.assertEqual(data.state, data.State.VALID)
                self.assertIsNotNone(data.new_version)
                self.assertIsNone(data.new_version.branch)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertRegex(data.new_version.tag, r"^[vV][\d.]+$")
                self.assertRegex(data.new_version.version, r"^[\d.]+$")
            else:
                self.fail(f"Unknown data {data.filename}")
            self._test_update_data(data, copy.deepcopy(data.source))

    def _test_update_data(self, data, orig_source):
        data.update()
        if data.filename == "qt-virustotal-uploader.git":
            self.assertEqual(data.source, orig_source)
        if data.filename == "protobuf-c.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "yara.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "yara-python.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "jansson.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "c-vtapi.git":
            self.assertNotEqual(data.source, orig_source)
            self.assertEqual(data.source.keys(), orig_source.keys())
            self.assertIn("commit", data.source)
            self.assertNotIn("tag", data.source)
            self.assertIn("branch", data.source)
            self.assertEqual(data.source["commit"], data.new_version.commit)
        elif data.filename == "vt-py.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "extra-cmake-modules.git":
            self.assertNotEqual(data.source, orig_source)
            self.assertIn("tag", data.source)
            self.assertIn("commit", data.source)
            self.assertNotEqual(data.source["commit"], orig_source["commit"])
            self.assertNotEqual(data.source["tag"], orig_source["tag"])


if __name__ == "__main__":
    unittest.main()
