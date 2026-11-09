import unittest
from unittest.mock import ANY, MagicMock, patch

from src.lib.runtime import (
    Flatpak,
    RuntimeEolChecker,
    RuntimeStatus,
    fetch_runtime_statuses,
)


class TestRuntimeEol(unittest.IsolatedAsyncioTestCase):
    async def test_eol_metadata_and_cache(self):
        manifest = {
            "build-extension": True,
            "runtime": "org.freedesktop.Sdk",
            "runtime-version": "23.08",
        }
        for reason, rebase in (("Retired", None), ("", None), (None, "new-ref")):
            with self.subTest(reason=reason, rebase=rebase):
                parent = RuntimeStatus(
                    "runtime/org.freedesktop.Sdk/x86_64/23.08", reason, rebase
                )
                with patch(
                    "src.lib.runtime.fetch_runtime_statuses",
                    return_value={(manifest["runtime"], "23.08"): [parent]},
                ) as fetch:
                    checker = RuntimeEolChecker()
                    result = await checker.check(manifest)
                    self.assertEqual(
                        result,
                        [
                            {
                                "ref": parent.ref,
                                "end_of_life": reason,
                                "end_of_life_rebase": rebase,
                            }
                        ],
                    )
                    self.assertEqual(await checker.check(manifest), result)
                    fetch.assert_called_once()

    async def test_active_architecture_prevents_skip(self):
        with patch(
            "src.lib.runtime.fetch_runtime_statuses",
            return_value={
                ("org.example.Sdk", "1"): [
                    RuntimeStatus("runtime/org.example.Sdk/x86_64/1", "Retired", None),
                    RuntimeStatus("runtime/org.example.Sdk/aarch64/1", None, None),
                ]
            },
        ):
            self.assertIsNone(
                await RuntimeEolChecker().check(
                    {
                        "build-extension": True,
                        "runtime": "org.example.Sdk",
                        "runtime-version": "1",
                    }
                )
            )

    async def test_invalid_and_unavailable_parents_are_errors(self):
        manifest = {
            "build-extension": True,
            "runtime": "org.example.Sdk",
            "runtime-version": "1",
        }
        with patch("src.lib.runtime.fetch_runtime_statuses", return_value={}) as fetch:
            for key in ("runtime", "runtime-version"):
                for invalid in (None, "", 1):
                    with self.subTest(key=key, invalid=invalid):
                        with self.assertRaises(ValueError):
                            await RuntimeEolChecker().check({**manifest, key: invalid})
            fetch.assert_not_called()
            with self.assertRaisesRegex(ValueError, "not found on Flathub"):
                await RuntimeEolChecker().check(manifest)
        with patch(
            "src.lib.runtime.fetch_runtime_statuses", side_effect=OSError("offline")
        ):
            with self.assertRaisesRegex(OSError, "offline"):
                await RuntimeEolChecker().check(manifest)

    async def test_apps_do_not_query_runtime(self):
        with patch("src.lib.runtime.fetch_runtime_statuses") as fetch:
            self.assertIsNone(
                await RuntimeEolChecker().check({"runtime": "org.example.Platform"})
            )
            fetch.assert_not_called()

    def test_flatpak_metadata(self):
        runtime = MagicMock()
        runtime.get_kind.return_value = Flatpak.RefKind.RUNTIME
        runtime.get_name.return_value = "org.example.Sdk"
        runtime.get_branch.return_value = "1"
        runtime.format_ref.return_value = "runtime/org.example.Sdk/x86_64/1"
        runtime.get_eol.return_value = "Retired"
        runtime.get_eol_rebase.return_value = None
        app = MagicMock()
        app.get_kind.return_value = Flatpak.RefKind.APP
        with patch("src.lib.runtime.Flatpak.Installation.new_for_path") as new:
            new.return_value.list_remote_refs_sync_full.return_value = [runtime, app]
            self.assertEqual(
                fetch_runtime_statuses(),
                {
                    ("org.example.Sdk", "1"): [
                        RuntimeStatus(runtime.format_ref(), "Retired", None)
                    ]
                },
            )
            new.return_value.list_remote_refs_sync_full.assert_called_once_with(
                "flathub", Flatpak.QueryFlags.ALL_ARCHES, ANY
            )
