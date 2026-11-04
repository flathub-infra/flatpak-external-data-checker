import os
import unittest
from unittest import mock

from packaging.version import Version

from src.checkers.pypichecker import PyPIChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerQueryError
from src.lib.externaldata import ExternalFile
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.valvesoftware.Steam.yml")


class TestPyPIChecker(unittest.IsolatedAsyncioTestCase):
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

        self.assertEqual(len(ext_data), 6)
        for data in ext_data:
            if data.filename != "Pillow-7.2.0.tar.gz":
                self.assertIsNotNone(data.new_version)
                self.assertIsNotNone(data.new_version.url)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsNotNone(data.new_version.version)
                self.assertNotEqual(data.new_version.url, data.current_version.url)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
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
                    r"https://files.pythonhosted.org/packages/[a-f0-9/]+/(?i:PyYAML-)[\d\.]+.(tar.(gz|xz|bz2)|zip)",
                )
            elif data.filename == "vdf-3.1-py2.py3-none-any.whl":
                self.assertRegex(
                    data.new_version.url,
                    r"https://files.pythonhosted.org/packages/[a-f0-9/]+/vdf-[\d\.]+-[\S\.]+-none-any.whl",
                )
                self.assertEqual(data.new_version.version, "3.2")
            elif data.filename == "Pillow-7.2.0.tar.gz":
                self.assertIsNone(data.new_version)
            elif data.filename == "allow-prerelease":
                # Avoid false-success on the `disallow-prerelease` source assertions
                # in case there wasn't any prerelease on PyPI
                self.assertIsNotNone(data.new_version)
                self.assertIsNotNone(data.new_version.version)
                self.assertIsNotNone(Version(data.new_version.version).pre)
            elif data.filename == "disallow-prerelease":
                self.assertIsNotNone(data.new_version)
                self.assertIsNotNone(data.new_version.version)
                self.assertIsNone(Version(data.new_version.version).pre)
            else:
                self.fail(f"Unknown data {data.filename}")

    async def test_skips_any_wheels(self):
        mock_json_data = {
            "info": {"version": "1.0.0"},
            "urls": [
                {
                    "filename": "cryptography-1.0.0-cp311-cp311-manylinux_2_28_x86_64.whl",  # noqa: E501
                    "packagetype": "bdist_wheel",
                    "python_version": "py3",
                    "upload_time_iso_8601": "2026-01-01T00:00:00Z",
                    "url": "https://files.pythonhosted.org/packages/xx/cryptography-1.0.0-cp311-cp311-manylinux_2_28_x86_64.whl",
                    "digests": {"sha256": "abc123"},
                    "size": 1000,
                },
                {
                    "filename": "cryptography-1.0.0-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "python_version": "py3",
                    "upload_time_iso_8601": "2026-01-01T00:00:01Z",
                    "url": "https://files.pythonhosted.org/packages/xx/cryptography-1.0.0-py3-none-any.whl",
                    "digests": {"sha256": "def456"},
                    "size": 2000,
                },
            ],
        }
        mock_json_data["releases"] = {"1.0.0": mock_json_data["urls"]}

        dummy_ext_data = mock.MagicMock()
        dummy_ext_data.checker_data = {
            "name": "cryptography",
            "packagetype": "bdist_wheel",
        }

        mock_response = mock.AsyncMock()
        mock_response.__aenter__ = mock.AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = mock.AsyncMock(return_value=False)
        mock_response.json.return_value = mock_json_data

        mock_session = mock.MagicMock()
        mock_session.get.return_value = mock_response

        checker = PyPIChecker(mock_session)
        await checker.check(dummy_ext_data)

        dummy_ext_data.set_new_version.assert_called_once()
        call_args = dummy_ext_data.set_new_version.call_args[0][0]
        self.assertEqual(
            call_args.url,
            "https://files.pythonhosted.org/packages/xx/cryptography-1.0.0-py3-none-any.whl",
        )


class TestPyPIReleaseEligibility(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def download(version, **overrides):
        download = {
            "filename": f"example-{version}.tar.gz",
            "packagetype": "sdist",
            "python_version": "source",
            "upload_time_iso_8601": "2026-01-01T00:00:00Z",
            "url": f"https://example.org/example-{version}.tar.gz",
            "digests": {"sha256": "abc123"},
            "size": 1000,
            "yanked": False,
        }
        download.update(overrides)
        return download

    async def check_releases(self, releases, latest, filename=None, **options):
        response = mock.AsyncMock()
        response.__aenter__.return_value = response
        response.json.return_value = {
            "info": {"version": latest},
            "urls": releases.get(latest, []),
            "releases": releases,
        }
        session = mock.MagicMock()
        session.get.return_value = response
        data = mock.MagicMock()
        data.filename = "renamed-download"
        data.current_version = ExternalFile(
            url=f"https://example.org/{filename or 'example.tar.gz'}",
            checksum=MultiDigest(),
            size=None,
            version=None,
            timestamp=None,
        )
        data.checker_data = {"name": "example", **options}
        await PyPIChecker(session).check(data)
        data.set_new_version.assert_called_once()
        return data.set_new_version.call_args.args[0]

    async def test_release_kinds(self):
        for version, stable in (
            ("2.0", True),
            ("2.0.dev1", False),
            ("2.0a1", False),
            ("2.0b1", False),
            ("2.0rc1", False),
            ("2.0.post1", True),
            ("2.0.post1.dev1", False),
        ):
            for stable_only in (True, False):
                with self.subTest(version=version, stable_only=stable_only):
                    releases = {
                        version: [self.download(version)],
                        "1.0": [self.download("1.0")],
                    }
                    result = await self.check_releases(
                        releases, version, **{"stable-only": stable_only}
                    )
                    expected = "1.0" if stable_only and not stable else version
                    self.assertEqual(result.version, expected)

    async def test_yanked_artifacts(self):
        for stable_only in (True, False):
            with self.subTest(stable_only=stable_only):
                eligible = self.download("2.0")
                releases = {
                    "3.0": [self.download("3.0", yanked=True)],
                    "2.0": [
                        self.download("2.0", yanked=True),
                        eligible,
                        self.download(
                            "2.0", yanked=True, url="https://example.org/yanked"
                        ),
                    ],
                    "1.0": [self.download("1.0")],
                }
                result = await self.check_releases(
                    releases, "3.0", **{"stable-only": stable_only}
                )
                self.assertEqual(result.version, "2.0")
                self.assertEqual(result.url, eligible["url"])

    async def test_constraints_apply_to_remaining_candidates(self):
        releases = {
            "4.0": [self.download("4.0")],
            "3.0": [self.download("3.0", yanked=True)],
            "2.0": [self.download("2.0")],
            "1.0": [self.download("1.0")],
        }
        result = await self.check_releases(
            releases, "4.0", versions={"<": "4.0", ">=": "2.0"}
        )
        self.assertEqual(result.version, "2.0")

    async def test_legacy_versions_do_not_hide_eligible_releases(self):
        releases = {
            "2004d": [self.download("2004d")],
            "2026.6": [self.download("2026.6")],
        }
        result = await self.check_releases(
            releases, "2026.6", versions={">=": "2026.0"}
        )
        self.assertEqual(result.version, "2026.6")

    async def test_latest_stable_does_not_scan_release_history(self):
        releases = mock.MagicMock(
            wraps={
                "1.0": [self.download("1.0")],
                "2.0": [self.download("2.0")],
            }
        )
        result = await self.check_releases(releases, "2.0")
        self.assertEqual(result.version, "2.0")
        releases.items.assert_not_called()

    async def test_prereleases_newer_than_advertised_latest(self):
        releases = {
            "2.0": [self.download("2.0")],
            "3.0rc1": [self.download("3.0rc1")],
        }
        result = await self.check_releases(releases, "2.0", **{"stable-only": False})
        self.assertEqual(result.version, "3.0rc1")

    async def test_remaining_artifacts_must_be_compatible(self):
        for overrides in (
            {"packagetype": "sdist"},
            {"filename": "example-2.0-py2-none-any.whl"},
            {"filename": "example-2.0-cp311-cp311-linux_x86_64.whl"},
        ):
            with self.subTest(overrides=overrides):
                eligible = self.download(
                    "1.0",
                    packagetype="bdist_wheel",
                    python_version="py3",
                    filename="example-1.0-py3-none-any.whl",
                )
                incompatible = {**eligible, **overrides}
                releases = {
                    "2.0": [{**eligible, "yanked": True}, incompatible],
                    "1.0": [eligible],
                }
                result = await self.check_releases(
                    releases, "2.0", packagetype="bdist_wheel"
                )
                self.assertEqual(result.version, "1.0")

    async def test_wheel_tags_override_legacy_python_version(self):
        for tags, python_version in (
            ("py2.py3-none-any", "3.8"),
            ("py3-none-any", "3.7"),
        ):
            with self.subTest(tags=tags):
                wheel = self.download(
                    "1.0",
                    filename=f"example-1.0-{tags}.whl",
                    packagetype="bdist_wheel",
                    python_version=python_version,
                )
                result = await self.check_releases(
                    {"1.0": [wheel]}, "1.0", packagetype="bdist_wheel"
                )
                self.assertEqual(result.url, wheel["url"])

    async def test_platform_wheel_compatibility(self):
        cases = (
            [
                (f"cp39-abi3-manylinux_2_34_{arch}", f"cp39-abi3-manylinux_2_34_{arch}")
                for arch in ("aarch64", "x86_64")
            ]
            + [
                (
                    f"cp314-cp314-manylinux_2_27_{arch}.manylinux_2_28_{arch}",
                    f"cp314-cp314-manylinux_2_27_{arch}",
                )
                for arch in ("aarch64", "x86_64")
            ]
            + [
                (
                    "cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64",
                    "cp313-cp313-manylinux_2_28_x86_64.manylinux2014_x86_64",
                ),
                (
                    "cp36-abi3-manylinux_2_12_x86_64.manylinux2010_x86_64.manylinux_2_17_x86_64.manylinux2014_x86_64",
                    "cp36-abi3-manylinux2010_x86_64.manylinux_2_12_x86_64.manylinux_2_28_x86_64",
                ),
            ]
        )
        for current, candidate in cases:
            with self.subTest(current=current):
                eligible = self.download(
                    "2.0",
                    filename=f"example-2.0-{candidate}.whl",
                    packagetype="bdist_wheel",
                    python_version="cp313",
                    url="https://example.org/eligible.whl",
                )
                interpreter, abi, platform = candidate.split("-")
                incompatible = [
                    {**eligible, "filename": f"example-3.0-{tags}.whl"}
                    for tags in (
                        f"{interpreter}-cp999-{platform}",
                        f"cp999-{abi}-{platform}",
                        candidate.replace("x86_64", "s390x").replace(
                            "aarch64", "s390x"
                        ),
                        "cp315-cp315-manylinux_2_28_x86_64",
                        "cp313-cp313t-manylinux_2_28_x86_64",
                        "cp39-abi3-win_amd64",
                        "cp36-abi3-manylinux_2_12_s390x",
                        "cp39-abi3-manylinux_2_99_x86_64",
                    )
                ]
                for options in ({}, {"versions": {"<": "3.0"}}):
                    result = await self.check_releases(
                        {
                            "2.0": [eligible],
                            "3.0": incompatible + [{**eligible, "yanked": True}],
                        },
                        "3.0",
                        filename=f"example-1.0-{current}.whl",
                        packagetype="bdist_wheel",
                        **options,
                    )
                    self.assertEqual(result.version, "2.0")
                    self.assertEqual(result.url, eligible["url"])

    async def test_manylinux_baseline_is_preserved(self):
        cases = (
            ("manylinux_2_12.manylinux_2_28", "manylinux_2_28", False),
            ("manylinux2010.manylinux_2_28", "manylinux_2_28", False),
            ("manylinux_2_27.manylinux_2_28", "manylinux_2_28", False),
            ("manylinux2014.manylinux_2_28", "manylinux_2_28", False),
            ("manylinux_2_12", "manylinux2010", True),
            ("manylinux2010", "manylinux_2_12", True),
            ("manylinux1", "manylinux_2_5", True),
            ("manylinux_2_5", "manylinux1", True),
            ("manylinux2014", "manylinux_2_17", True),
            ("manylinux_2_17", "manylinux2014", True),
            ("manylinux_2_28", "manylinux_2_17", True),
            ("manylinux_2_12.manylinux_2_28", "manylinux_2_28.manylinux2010", True),
        )
        for arch in ("x86_64", "aarch64"):
            for current, candidate, compatible in cases:
                with self.subTest(arch=arch, current=current, candidate=candidate):
                    current_platform = ".".join(
                        f"{tag}_{arch}" for tag in current.split(".")
                    )
                    candidate_platform = ".".join(
                        f"{tag}_{arch}" for tag in candidate.split(".")
                    )
                    filename = f"example-1.0-cp39-abi3-{current_platform}.whl"
                    original = self.download(
                        "1.0",
                        filename=filename,
                        packagetype="bdist_wheel",
                        python_version="cp39",
                    )
                    updated = self.download(
                        "2.0",
                        filename=f"example-2.0-cp39-abi3-{candidate_platform}.whl",
                        packagetype="bdist_wheel",
                        python_version="cp39",
                    )
                    for options in ({}, {"versions": {"<": "3.0"}}):
                        result = await self.check_releases(
                            {"1.0": [original], "2.0": [updated]},
                            "2.0",
                            filename=filename,
                            packagetype="bdist_wheel",
                            **options,
                        )
                        self.assertEqual(result.version, "2.0" if compatible else "1.0")

    async def test_compressed_python_tags_do_not_allow_python2_only(self):
        eligible = self.download(
            "1.0",
            filename="example-1.0-py3-none-any.whl",
            packagetype="bdist_wheel",
            python_version="3.7",
        )
        releases = {
            "1.0": [eligible],
            "2.0": [{**eligible, "filename": "example-2.0-py2-none-any.whl"}],
            "3.0": [{**eligible, "filename": "broken.whl"}],
        }
        result = await self.check_releases(
            releases,
            "3.0",
            filename="example-0.1-py2.py3-none-any.whl",
            packagetype="bdist_wheel",
        )
        self.assertEqual(result.version, "1.0")

    async def test_no_eligible_candidates(self):
        cases = [
            ({}, {}),
            ({"2004d": [self.download("2004d")]}, {}),
            ({"2.0": []}, {}),
            ({"2.0.dev1": [self.download("2.0.dev1", yanked=True)]}, {}),
            ({"2.0": [self.download("2.0", yanked=True)]}, {"stable-only": False}),
            ({"2.0rc1": [self.download("2.0rc1")]}, {}),
            (
                {"2.0": [self.download("2.0", yanked=True)]},
                {"versions": {"==": "2.0"}},
            ),
            (
                {
                    "1.0": [self.download("1.0")],
                    "2.0": [self.download("2.0", yanked=True)],
                },
                {"versions": {">=": "2.0"}},
            ),
        ]
        for releases, options in cases:
            with self.subTest(releases=releases, options=options):
                with self.assertRaisesRegex(
                    CheckerQueryError, "Couldn't find sdist for package example"
                ):
                    await self.check_releases(
                        releases, next(iter(releases), "2.0"), **options
                    )


if __name__ == "__main__":
    unittest.main()
