import os
import unittest
from distutils.version import LooseVersion

from src.manifest import ManifestChecker
from src.lib.externaldata import ExternalFile, ExternalGitRef
from src.lib.checksums import MultiDigest
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.flatpak.Flatpak.yml")


class TestAnityaChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 5)
        for data in ext_data:
            if data.filename == "glib-networking-2.74.0.tar.xz":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://download.gnome.org/sources/glib-networking/\d+.\d+/glib-networking-[\d.]+.tar.xz$",  # noqa: E501
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertGreater(
                    LooseVersion(data.new_version.version), LooseVersion("2.76")
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="1f185aaef094123f8e25d8fa55661b3fd71020163a0174adb35a37685cda613b",  # noqa: E501
                    ),
                )
            elif data.filename == "boost_1_74_0.tar.bz2":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://boostorg\.jfrog\.io/artifactory/main/release/[\d.]+/source/boost_[\d]+_[\d]+_[\d]+.tar.bz2$",  # noqa: E501
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertGreater(
                    LooseVersion(data.new_version.version), LooseVersion("1.74.0")
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="83bfc1507731a0906e387fc28b7ef5417d591429e51e788417fe9ff025e116b1"  # noqa: E501
                    ),
                )
            elif data.filename == "flatpak-1.8.2.tar.xz":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://github.com/flatpak/flatpak/releases/download/[\w\d.]+/flatpak-[\w\d.]+.tar.xz$",  # noqa: E501
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertEqual(
                    LooseVersion(data.new_version.version), LooseVersion("1.10.1")
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="7926625df7c2282a5ee1a8b3c317af53d40a663b1bc6b18a2dc8747e265085b0"  # noqa: E501
                    ),
                )
            elif data.filename == "ostree.git":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.tag)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertGreater(
                    LooseVersion(data.new_version.version), LooseVersion("2020.7")
                )
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
            elif data.filename == "gr-iqbal.git":
                self.assertIsNone(data.new_version)
            else:
                self.fail(f"Unknown data {data.filename}")


if __name__ == "__main__":
    unittest.main()
