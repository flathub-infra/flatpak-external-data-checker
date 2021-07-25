import os
import unittest

from src.checker import ManifestChecker
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.valvesoftware.Steam.yml")


class TestPyPIChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 4)
        for data in ext_data:
            if data.filename != "Pillow-7.2.0.tar.gz":
                self.assertIsNotNone(data.new_version)
                self.assertIsNotNone(data.new_version.url)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsNotNone(data.new_version.version)
                self.assertNotEqual(data.new_version.url, data.current_version.url)
                self.assertIsInstance(data.new_version.checksum, str)
                self.assertNotEqual(
                    data.new_version.checksum, data.current_version.checksum
                )
            if data.filename == "setuptools-50.3.2-py3-none-any.whl":
                self.assertRegex(
                    data.new_version.url,
                    r"https://files.pythonhosted.org/packages/[a-f0-9/]+/setuptools-[\d\.]+-[\S\.]+-none-any.whl",
                )
            elif data.filename == "PyYAML-5.3.1.tar.gz":
                self.assertRegex(
                    data.new_version.url,
                    r"https://files.pythonhosted.org/packages/[a-f0-9/]+/PyYAML-[\d\.]+.(tar.(gz|xz|bz2)|zip)",
                )
            elif data.filename == "vdf-3.1-py2.py3-none-any.whl":
                self.assertRegex(
                    data.new_version.url,
                    r"https://files.pythonhosted.org/packages/[a-f0-9/]+/vdf-[\d\.]+-[\S\.]+-none-any.whl",
                )
                self.assertEqual(data.new_version.version, "3.2")
            elif data.filename == "Pillow-7.2.0.tar.gz":
                self.assertIsNone(data.new_version)
            else:
                self.fail(f"Unknown data {data.filename}")
