import hashlib
import subprocess
from pathlib import Path
from unittest import mock

from gi.repository import GLib

from src.checkers.urlchecker import URLChecker
from src.lib.externaldata import ExternalData
from tests.support import LocalHTTPTestCase


class TestAppImageVersion(LocalHTTPTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.body = b"\x7fELF" + b"\0" * 124
        self.responses["/package.AppImage"] = {
            "body": self.body,
            "content_type": "application/octet-stream",
        }
        self.desktops = {"package.desktop": "[Desktop Entry]\nName=Package\n"}
        elf_patch = mock.patch("src.lib.utils.ELFFile")
        self.elf = elf_patch.start()
        self.addCleanup(elf_patch.stop)
        self.elf.return_value.header = {
            "e_shoff": 64,
            "e_shnum": 2,
            "e_shentsize": 32,
        }
        command_patch = mock.patch("src.lib.utils.Command")
        self.command = command_patch.start()
        self.addCleanup(command_patch.stop)
        self.command.side_effect = self.extract

    def extract(self, argv, *, cwd, **kwargs):
        async def run():
            root = Path(cwd) / "squashfs-root"
            root.mkdir()
            for name, contents in self.desktops.items():
                (root / name).write_text(contents)
            return b"", b""

        return mock.Mock(run=mock.AsyncMock(side_effect=run))

    def make_source(self, *, opt_out=False, unchanged=False):
        source = {
            "type": "extra-data",
            "url": str(self.server.make_url("/package.AppImage")),
            "filename": "package.AppImage",
            "sha256": hashlib.sha256(self.body).hexdigest() if unchanged else "0" * 64,
            "size": len(self.body),
        }
        if opt_out:
            source["x-checker-data"] = {
                "type": "rotating-url",
                "url": source["url"],
                "no-extract-version": True,
            }
        return ExternalData.from_source("test.json", source)

    async def check_version(self, expected, *, opt_out=False, unchanged=False):
        data = self.make_source(opt_out=opt_out, unchanged=unchanged)
        await URLChecker(self.http).check(data)
        if unchanged:
            self.assertIsNone(data.new_version)
            self.assertTrue(data.state & data.State.VALID)
        else:
            self.assertIsNotNone(data.new_version)
            self.assertEqual(data.new_version.version, expected)
        data.update()
        self.assertEqual(data.source["sha256"], hashlib.sha256(self.body).hexdigest())
        self.assertEqual(data.source["size"], len(self.body))
        return data

    async def test_missing_version(self):
        await self.check_version(None)

    async def test_missing_version_unchanged_checksum(self):
        await self.check_version(None, unchanged=True)

    async def test_later_valid_candidate(self):
        self.desktops["versioned.desktop"] = (
            "[Desktop Entry]\nX-AppImage-Version=8.0.4\n"
        )
        # Pin candidate order so this exercises skipping the unversioned file.
        original_glob = Path.glob
        with mock.patch.object(
            Path, "glob", lambda path, pattern: sorted(original_glob(path, pattern))
        ):
            await self.check_version("8.0.4")

    async def test_missing_desktop_group(self):
        self.desktops = {"package.desktop": "[Other]\nName=Package\n"}
        await self.check_version(None)

    async def test_no_desktop_file(self):
        self.desktops = {}
        await self.check_version(None)

    async def test_normal_version(self):
        self.desktops = {
            "package.desktop": "[Desktop Entry]\nX-AppImage-Version=8.0.4\n"
        }
        await self.check_version("8.0.4")
        self.elf.assert_called_once()
        self.command.assert_called_once()

    async def test_opt_out(self):
        await self.check_version(None, opt_out=True)
        self.elf.assert_not_called()
        self.command.assert_not_called()

    async def test_invalid_desktop_file(self):
        self.desktops = {"package.desktop": "not a key file\n"}
        with self.assertRaises(GLib.Error) as caught:
            await URLChecker(self.http).check(self.make_source())
        self.assertTrue(
            caught.exception.matches(
                GLib.key_file_error_quark(), GLib.KeyFileError.PARSE
            )
        )

    async def test_invalid_version_value(self):
        self.desktops = {
            "package.desktop": "[Desktop Entry]\nX-AppImage-Version=bad\\q\n"
        }
        with self.assertRaises(GLib.Error) as caught:
            await URLChecker(self.http).check(self.make_source())
        self.assertTrue(
            caught.exception.matches(
                GLib.key_file_error_quark(), GLib.KeyFileError.INVALID_VALUE
            )
        )

    async def test_extraction_failure(self):
        self.command.side_effect = None
        self.command.return_value.run = mock.AsyncMock(
            side_effect=subprocess.CalledProcessError(1, "unsquashfs")
        )
        with self.assertRaises(subprocess.CalledProcessError):
            await URLChecker(self.http).check(self.make_source())
