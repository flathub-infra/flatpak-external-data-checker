# Copyright © 2020–2021 Maximiliano Sandoval <msandova@protonmail.com>
#
# Authors:
#       Maximiliano Sandoval <msandova@protonmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import os
import unittest
from unittest import mock

from aiohttp import ClientError

from src.checkers.gnomechecker import GNOMEChecker, VersionScheme, _is_stable
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerQueryError
from src.lib.utils import init_logging
from src.lib.version import LooseVersion
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.gnome.baobab.json")


class TestGNOMEChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    def test_is_stable(self):
        self.assertTrue(_is_stable("3.28.0"))
        self.assertTrue(_is_stable("41"))
        self.assertTrue(_is_stable("41.1"))
        self.assertTrue(_is_stable("41.2"))
        self.assertTrue(_is_stable("4.1"))
        self.assertTrue(_is_stable("4.2"))
        self.assertTrue(_is_stable("1.7"))
        self.assertTrue(_is_stable("1.2"))
        self.assertTrue(_is_stable("2.7"))
        self.assertTrue(_is_stable("2.2"))

        self.assertFalse(_is_stable("4.rc"))
        self.assertFalse(_is_stable("4.2.beta"))
        self.assertFalse(_is_stable("4.alpha.0"))
        self.assertFalse(_is_stable("48.0.alpha2"))
        self.assertFalse(_is_stable("48.alpha2"))

    def test_minor_scheme_is_stable(self):
        scheme = VersionScheme.ODD_MINOR_IS_UNSTABLE
        self.assertTrue(_is_stable("1", scheme))
        self.assertTrue(_is_stable("2", scheme))

        self.assertTrue(_is_stable("1.2", scheme))
        self.assertTrue(_is_stable("1.2.0", scheme))
        self.assertTrue(_is_stable("2.2", scheme))
        self.assertTrue(_is_stable("2.2.1", scheme))
        self.assertTrue(_is_stable("2.2.2", scheme))
        self.assertTrue(_is_stable("1.2.2", scheme))
        self.assertTrue(_is_stable("1.2.1", scheme))
        self.assertTrue(_is_stable("0.10.0", scheme))

        self.assertFalse(_is_stable("1.1", scheme))
        self.assertFalse(_is_stable("1.1.0", scheme))
        self.assertFalse(_is_stable("2.3", scheme))
        self.assertFalse(_is_stable("2.1.1", scheme))
        self.assertFalse(_is_stable("2.3.2", scheme))
        self.assertFalse(_is_stable("1.1.2", scheme))
        self.assertFalse(_is_stable("1.3.1", scheme))
        self.assertFalse(_is_stable("0.11.0", scheme))

        # This should at least not crash
        self.assertFalse(_is_stable("40.alpha", scheme))

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        for data in ext_data:
            if data.filename == "cairo-1.17.6.tar.gz":
                self.assertIsNone(data.new_version)
                continue

            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertNotEqual(
                data.new_version.checksum,
                MultiDigest(
                    sha256="0000000000000000000000000000000000000000000000000000000000000000"
                ),
            )
            self.assertIsNotNone(data.new_version.version)
            self.assertIsInstance(data.new_version.version, str)

            if data.filename == "baobab-3.34.0.tar.xz":
                self._test_stable_only(data)
            elif data.filename == "pygobject-3.36.0.tar.xz":
                self._test_include_unstable(data)
                self.assertLess(
                    LooseVersion(data.new_version.version), LooseVersion("3.38.0")
                )
            elif data.filename == "alleyoop-0.9.8.tar.xz":
                self._test_non_standard_version(data)
            elif data.filename == "tracker-3.4.2.tar.xz":
                self.assertIsNotNone(data.new_version)

    def _test_stable_only(self, data):
        self.assertEqual(data.filename, "baobab-3.34.0.tar.xz")
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.gnome\.org/sources/baobab/.+/baobab-.+\.tar\.xz$",
        )

    def _test_include_unstable(self, data):
        self.assertEqual(data.filename, "pygobject-3.36.0.tar.xz")
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.gnome\.org/sources/pygobject/.+/pygobject-.+\.tar\.xz$",
        )

    def _test_non_standard_version(self, data):
        self.assertEqual(data.filename, "alleyoop-0.9.8.tar.xz")
        self.assertEqual(
            data.new_version.version,
            "0.9.8",
        )


class TestGNOMECheckerMocked(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    def _make_checker(self):
        checker = GNOMEChecker.__new__(GNOMEChecker)
        checker.robots_cache = None
        return checker

    def _make_session(self, cache_json, checksum_text="abc123  proj-1.0.tar.xz\n"):
        cache_resp = mock.AsyncMock()
        cache_resp.json = mock.AsyncMock(return_value=cache_json)

        checksum_resp = mock.AsyncMock()
        checksum_resp.text = mock.AsyncMock(return_value=checksum_text)

        session = mock.MagicMock()
        session.get.return_value.__aenter__ = mock.AsyncMock(
            side_effect=[cache_resp, checksum_resp]
        )
        session.get.return_value.__aexit__ = mock.AsyncMock(return_value=False)
        return session

    async def test_network_error_raises_checker_query_error(self):
        checker = self._make_checker()
        session = mock.MagicMock()
        session.get.return_value.__aenter__ = mock.AsyncMock(side_effect=ClientError())
        session.get.return_value.__aexit__ = mock.AsyncMock(return_value=False)
        checker.session = session

        external_data = mock.MagicMock()
        external_data.checker_data = {"name": "baobab"}

        with self.assertRaises(CheckerQueryError):
            await checker.check(external_data)

    async def test_no_stable_version_falls_back_to_latest(self):
        proj = "baobab"
        versions = ["3.34.0.alpha", "3.35.0.beta"]

        cache_json = [
            4,
            {
                proj: {
                    "3.34.0.alpha": {
                        "tar.xz": "3.34.0.alpha/baobab-3.34.0.alpha.tar.xz",
                        "sha256sum": "3.34.0.alpha/baobab-3.34.0.alpha.sha256sum",
                    },
                    "3.35.0.beta": {
                        "tar.xz": "3.35.0.beta/baobab-3.35.0.beta.tar.xz",
                        "sha256sum": "3.35.0.beta/baobab-3.35.0.beta.sha256sum",
                    },
                }
            },
            {proj: versions},
            {
                "3.34.0.alpha": [],
                "3.35.0.beta": [],
            },
        ]

        checksum_text = "deadbeef  baobab-3.35.0.beta.tar.xz\n"

        checker = self._make_checker()
        checker.session = self._make_session(cache_json, checksum_text)

        external_data = mock.MagicMock()
        external_data.checker_data = {"name": proj}

        with self.assertLogs("src.checkers.gnomechecker", level="WARNING") as cm:
            await checker.check(external_data)

        self.assertTrue(
            any("Couldn't find any stable version" in line for line in cm.output)
        )

        external_data.set_new_version.assert_called_once()
        new_ver = external_data.set_new_version.call_args[0][0]

        self.assertEqual(new_ver.version, "3.35.0.beta")


if __name__ == "__main__":
    unittest.main()
