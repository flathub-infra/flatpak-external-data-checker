import unittest
from unittest.mock import ANY, MagicMock, patch

from src.lib.runtime import (
    Flatpak,
    RuntimeEolChecker,
    RuntimeStatus,
    fetch_runtime_statuses,
)


class TestRuntimeEol(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def remote_ref(kind, name, branch, arch="x86_64", reason=None, rebase=None):
        ref = MagicMock()
        ref.get_kind.return_value = kind
        ref.get_name.return_value = name
        ref.get_branch.return_value = branch
        namespace = "app" if kind == Flatpak.RefKind.APP else "runtime"
        ref.format_ref.return_value = f"{namespace}/{name}/{arch}/{branch}"
        ref.get_eol.return_value = reason
        ref.get_eol_rebase.return_value = rebase
        return ref

    async def test_published_parents_through_fetch_and_check(self):
        for kind in (Flatpak.RefKind.APP, Flatpak.RefKind.RUNTIME):
            for branch in ("stable", "21.08"):
                for reason, rebase, retired in (
                    (None, None, False),
                    ("Retired", None, True),
                    ("", None, True),
                    (None, "new-ref", True),
                ):
                    with self.subTest(
                        kind=kind, branch=branch, reason=reason, rebase=rebase
                    ):
                        refs = [
                            self.remote_ref(
                                kind, "org.example.Parent", branch, arch, reason, rebase
                            )
                            for arch in ("x86_64", "aarch64")
                        ]
                        manifest = {
                            "build-extension": True,
                            "runtime": "org.example.Parent",
                            "runtime-version": branch,
                        }
                        with patch(
                            "src.lib.runtime.Flatpak.Installation.new_for_path"
                        ) as new:
                            listing = new.return_value.list_remote_refs_sync_full
                            listing.return_value = refs
                            checker = RuntimeEolChecker()
                            result = await checker.check(manifest)
                            if retired:
                                self.assertEqual(
                                    result,
                                    [
                                        {
                                            "ref": ref.format_ref(),
                                            "end_of_life": reason,
                                            "end_of_life_rebase": rebase,
                                        }
                                        for ref in refs
                                    ],
                                )
                            else:
                                self.assertIsNone(result)
                            self.assertEqual(await checker.check(manifest), result)
                            listing.assert_called_once_with(
                                "flathub", Flatpak.QueryFlags.ALL_ARCHES, ANY
                            )

                            # An active architecture must prevent retirement.
                            refs[1].get_eol.return_value = None
                            refs[1].get_eol_rebase.return_value = None
                            self.assertIsNone(await RuntimeEolChecker().check(manifest))

    async def test_missing_and_ambiguous_parents_through_fetch(self):
        manifest = {
            "build-extension": True,
            "runtime": "org.example.Parent",
            "runtime-version": "stable",
        }
        runtime = self.remote_ref(
            Flatpak.RefKind.RUNTIME, manifest["runtime"], "stable", reason="Retired"
        )
        app = self.remote_ref(Flatpak.RefKind.APP, manifest["runtime"], "stable")
        for refs, message in (
            ([], "not found on Flathub"),
            (
                [self.remote_ref(Flatpak.RefKind.APP, manifest["runtime"], "other")],
                "not found on Flathub",
            ),
            (
                [self.remote_ref(Flatpak.RefKind.APP, "org.example.Other", "stable")],
                "not found on Flathub",
            ),
            ([runtime, app], "ambiguous"),
        ):
            with self.subTest(message=message, refs=refs):
                with patch("src.lib.runtime.Flatpak.Installation.new_for_path") as new:
                    new.return_value.list_remote_refs_sync_full.return_value = refs
                    with self.assertRaisesRegex(ValueError, message):
                        await RuntimeEolChecker().check(manifest)

    async def test_fetch_failure_is_not_cached(self):
        manifest = {
            "build-extension": True,
            "runtime": "org.example.Parent",
            "runtime-version": "stable",
        }
        app = self.remote_ref(Flatpak.RefKind.APP, manifest["runtime"], "stable")
        with patch("src.lib.runtime.Flatpak.Installation.new_for_path") as new:
            listing = new.return_value.list_remote_refs_sync_full
            listing.side_effect = [OSError("offline"), [app]]
            checker = RuntimeEolChecker()
            with self.assertRaisesRegex(OSError, "offline"):
                await checker.check(manifest)
            self.assertIsNone(await checker.check(manifest))
            self.assertIsNone(await checker.check(manifest))
            self.assertEqual(listing.call_count, 2)

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
        app.get_name.return_value = "org.example.App"
        app.get_branch.return_value = "stable"
        app.format_ref.return_value = "app/org.example.App/x86_64/stable"
        app.get_eol.return_value = None
        app.get_eol_rebase.return_value = None
        with patch("src.lib.runtime.Flatpak.Installation.new_for_path") as new:
            new.return_value.list_remote_refs_sync_full.return_value = [runtime, app]
            self.assertEqual(
                fetch_runtime_statuses(),
                {
                    ("org.example.Sdk", "1"): [
                        RuntimeStatus(runtime.format_ref(), "Retired", None)
                    ],
                    ("org.example.App", "stable"): [
                        RuntimeStatus(app.format_ref(), None, None)
                    ],
                },
            )
            new.return_value.list_remote_refs_sync_full.assert_called_once_with(
                "flathub", Flatpak.QueryFlags.ALL_ARCHES, ANY
            )
