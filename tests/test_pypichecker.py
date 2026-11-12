import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest import mock

import aiohttp
from packaging.version import Version

from src.checkers.pypichecker import PyPIChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError, CheckerQueryError
from src.lib.externaldata import ExternalData, ExternalFile
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import LocalHTTPTestCase, upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.valvesoftware.Steam.yml")


class TestPyPIRequestErrors(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.manifest = Path(directory.name) / "test.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "name": "test",
                    "modules": [
                        {
                            "name": name,
                            "sources": [
                                {
                                    "type": "archive",
                                    "url": f"https://example.org/{name}-1.0.tar.gz",
                                    "sha256": "0" * 64,
                                    "x-checker-data": {"type": "pypi", "name": name},
                                }
                            ],
                        }
                        for name in ("PyNaCl", "independent")
                    ],
                }
            )
        )

    async def check_error(self, error, *, body=False, metadata=None, payload=None):
        @asynccontextmanager
        async def response(url, **kwargs):
            if url == "https://pypi.org/pypi/PyNaCl/json":
                if not body:
                    raise error
                yield mock.Mock(
                    json=mock.AsyncMock(side_effect=error, return_value=payload)
                )
            elif url == "https://pypi.org/pypi/independent/json":
                await asyncio.sleep(0)
                download = TestPyPIReleaseEligibility.download("2.0")
                yield mock.Mock(
                    json=mock.AsyncMock(
                        return_value={
                            "info": {"version": "2.0"},
                            "urls": [download],
                        }
                    )
                )
            else:
                self.fail(f"Unexpected request: {url}")

        checker = ManifestChecker(str(self.manifest))
        if metadata is not None:
            data = checker.get_external_data()[0]
            data.checker_data.update(metadata)
        with mock.patch("aiohttp.ClientSession.get", side_effect=response):
            await checker.check()
        return checker

    async def test_request_failures_are_source_errors(self):
        request_info = mock.Mock(real_url="https://pypi.org/pypi/PyNaCl/json")
        cases = (
            (
                aiohttp.ConnectionTimeoutError("Connection timeout to host PyNaCl"),
                False,
            ),
            (asyncio.TimeoutError(), False),
            (aiohttp.ClientResponseError(request_info, (), status=503), False),
            (aiohttp.ContentTypeError(request_info, (), message="not JSON"), True),
            (aiohttp.ClientPayloadError("incomplete body"), True),
            (asyncio.TimeoutError("reading body"), True),
            (json.JSONDecodeError("Invalid JSON", "", 0), True),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"), True),
        )
        for error, body in cases:
            with self.subTest(error=type(error).__name__, body=body):
                with self.assertLogs("src.manifest", level="ERROR") as logs:
                    checker = await self.check_error(error, body=body)
                failed, independent = checker.get_external_data()
                self.assertTrue(failed.check_failed)
                self.assertTrue(failed.checked.is_set())
                self.assertIsNone(failed.new_version)
                self.assertFalse(independent.check_failed)
                self.assertTrue(independent.checked.is_set())
                self.assertEqual(independent.new_version.version, "2.0")
                (query_error,) = checker.get_errors()
                self.assertIsInstance(query_error, CheckerQueryError)
                self.assertIs(query_error.__cause__, error)
                detail = str(error) or type(error).__name__
                self.assertIn(detail, str(query_error))
                self.assertIn("PyNaCl", str(query_error))
                self.assertIn("PyNaCl", " ".join(logs.output))
                failed_report, independent_report = checker.get_source_report()
                self.assertEqual(failed_report["module"], "PyNaCl")
                self.assertEqual(
                    failed_report["source_pointer"], "/modules/0/sources/0"
                )
                self.assertEqual(failed_report["status"], "failed")
                self.assertEqual(failed_report["checkers_run"], ["PyPIChecker"])
                self.assertEqual(
                    failed_report["errors"],
                    [{"type": "CheckerQueryError", "message": str(query_error)}],
                )
                self.assertEqual(independent_report["status"], "update_available")
                self.assertEqual(independent_report["errors"], [])

    async def test_unexpected_errors_propagate(self):
        for error in (asyncio.CancelledError(), RuntimeError("bug"), ValueError("bug")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as raised:
                    await self.check_error(error, body=True)
                self.assertIs(raised.exception, error)

    async def test_release_data_errors_propagate(self):
        for payload, expected in (({}, KeyError), ({"info": None}, TypeError)):
            with self.subTest(payload=payload):
                with self.assertRaises(expected):
                    await self.check_error(None, body=True, payload=payload)

    async def test_invalid_version_constraint_propagates(self):
        with self.assertRaises(ValueError):
            await self.check_error(None, metadata={"versions": {">=": "not-a-version"}})


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


class TestPyPIChecksums(LocalHTTPTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.content = b"example release 2.0\n"
        self.digests = {
            kind: hashlib.new(kind, self.content).hexdigest()
            for kind in ("md5", "sha256", "sha512")
        }
        self.artifact_url = self.serve(self.content)
        download = {
            "filename": "example-2.0.tar.gz",
            "packagetype": "sdist",
            "python_version": "source",
            "upload_time_iso_8601": "2026-01-01T00:00:00Z",
            "url": self.artifact_url,
            "digests": {kind: self.digests[kind] for kind in ("md5", "sha256")},
            "size": len(self.content),
        }
        self.responses["/pypi/example/json"] = {
            "body": json.dumps(
                {
                    "info": {"version": "2.0"},
                    "urls": [download],
                    "releases": {"2.0": [download]},
                }
            ).encode(),
            "content_type": "application/json",
        }
        patcher = mock.patch(
            "src.checkers.pypichecker.PYPI_INDEX",
            str(self.server.make_url("/pypi")),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checker = PyPIChecker(self.http)

    def make_data(self, kinds, *, current=False):
        return ExternalData.from_source(
            "source.json",
            {
                "type": "archive",
                "url": self.artifact_url
                if current
                else "https://example.org/old.tar.gz",
                **{kind: self.digests[kind] if current else "0" for kind in kinds},
                "x-checker-data": {"type": "pypi", "name": "example"},
            },
        )

    async def test_updates_all_declared_checksums(self):
        for kinds in (("sha512",), ("sha256", "sha512")):
            with self.subTest(kinds=kinds):
                self.requests.clear()
                data = self.make_data(kinds)
                await self.checker.check(data)

                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.version, "2.0")
                self.assertEqual(data.new_version.size, len(self.content))
                self.assertTrue(data.state & data.State.OUTDATED)
                data.update()
                self.assertEqual(data.source["url"], self.artifact_url)
                for kind in kinds:
                    self.assertEqual(data.source[kind], self.digests[kind])
                self.assertEqual(
                    MultiDigest.from_source(data.source).digests, set(kinds)
                )
                self.assertEqual(
                    self.requests,
                    [("GET", "/pypi/example/json"), ("GET", "/response-0")],
                )

    async def test_metadata_digests_need_no_download(self):
        data = self.make_data(("sha256",))
        await self.checker.check(data)
        data.update()
        self.assertEqual(data.source["sha256"], self.digests["sha256"])
        self.assertEqual(self.requests, [("GET", "/pypi/example/json")])

    async def test_completed_digests_detect_unchanged_content(self):
        data = self.make_data(("sha512",), current=True)
        await self.checker.check(data)
        self.assertIsNone(data.new_version)
        self.assertTrue(data.state & data.State.LATEST)
        self.assertTrue(data.state & data.State.VALID)

    async def test_completion_rejects_checksum_mismatch(self):
        self.responses["/response-0"]["body"] = b"wrong artifact"
        for kinds in (("sha512",), ("sha256", "sha512")):
            with self.subTest(kinds=kinds):
                data = self.make_data(kinds)
                original = data.source.copy()
                with self.assertRaisesRegex(CheckerFetchError, "Checksum mismatch"):
                    await self.checker.check(data)
                self.assertIsNone(data.new_version)
                self.assertEqual(data.source, original)


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
