import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.checkers import Checker
from src.lib.errors import CheckerError
from src.manifest import ManifestChecker


class TestManifestCompletion(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "sources.json")

    def make_checker(self, parent_type="archive", parent_metadata=None):
        parent = {
            "type": parent_type,
            "url": "https://example.org/parent.tar.gz",
            "sha256": "0" * 64,
        }
        if parent_metadata is not None:
            parent["x-checker-data"] = parent_metadata
        parent_index = 1 if parent_type == "file" else 0
        child = {
            "type": "file",
            "url": "https://example.org/child.txt",
            "sha256": "0" * 64,
            "x-checker-data": {
                "type": "completion-test",
                "parent-id": f"test-{parent_type}-{parent_index}",
            },
        }
        # Schedule the child first to exercise dependency ordering, not list order.
        with open(self.path, "w") as stream:
            json.dump({"name": "test", "sources": [child, parent]}, stream)
        checker = ManifestChecker(self.path)
        child_data, parent_data = checker.get_external_data()
        self.assertIs(child_data.parent, parent_data)
        return checker, child_data, parent_data

    def observing_checker(self, visits, fail_parent=False):
        test = self

        class OfflineChecker(Checker, register=False):
            CHECKER_DATA_TYPE = "completion-test"
            CHECKER_DATA_SCHEMA = {}

            async def check(self, data):
                if data.parent is not None:
                    test.assertTrue(data.parent.checked.is_set())
                    visits.append(
                        data.parent.new_version or data.parent.current_version
                    )
                elif fail_parent:
                    raise CheckerError("offline parent failure")
                else:
                    # Yield so a child with a stale completion event would run early.
                    await asyncio.sleep(0)
                    data.new_version = data.current_version._replace(version="2.0")

        return OfflineChecker

    async def test_no_applicable_checkers_signals_completion(self):
        for parent_type in ("archive", "file"):
            with self.subTest(parent_type=parent_type):
                checker, child, parent = self.make_checker(parent_type)
                self.assertFalse(any(c.should_check(parent) for c in checker._checkers))
                visits = []
                checker._checkers.append(self.observing_checker(visits))
                counter = checker.TasksCounter(total=2)
                with patch.object(checker, "TasksCounter", return_value=counter):
                    result = await asyncio.wait_for(checker.check(), timeout=1)
                self.assertEqual(result, [child, parent])
                self.assertTrue(parent.checked.is_set())
                self.assertTrue(child.checked.is_set())
                self.assertEqual(visits, [parent.current_version])
                self.assertEqual(parent.state, parent.State.UNKNOWN)
                self.assertIsNone(parent.new_version)
                self.assertEqual(checker.get_errors(), [])
                self.assertEqual(counter.started, 2)
                self.assertEqual(counter.finished, 2)
                self.assertEqual(counter.failed, 0)

    async def test_empty_checker_list_completes_parent_and_child(self):
        checker, child, parent = self.make_checker()
        checker._checkers = []
        result = await asyncio.wait_for(checker.check(), timeout=1)
        self.assertEqual(result, [child, parent])
        self.assertTrue(parent.checked.is_set())
        self.assertTrue(child.checked.is_set())
        self.assertEqual(checker.get_errors(), [])

    async def test_filtered_parent_is_not_checked(self):
        checker, child, parent = self.make_checker(
            parent_metadata={"type": "completion-test"}
        )
        visits = []
        checker._checkers = [self.observing_checker(visits)]
        result = await asyncio.wait_for(checker.check(filter_type="file"), timeout=1)
        self.assertEqual(result, [child])
        self.assertTrue(child.checked.is_set())
        self.assertEqual(visits, [parent.current_version])
        self.assertEqual(parent.state, parent.State.UNKNOWN)
        self.assertIsNone(parent.new_version)

    async def test_known_parent_is_not_checked(self):
        checker, child, parent = self.make_checker(
            parent_metadata={"type": "completion-test"}
        )
        parent.state = parent.State.VALID
        visits = []
        checker._checkers = [self.observing_checker(visits)]
        result = await asyncio.wait_for(checker.check(), timeout=1)
        self.assertEqual(result, [child])
        self.assertEqual(visits, [parent.current_version])
        self.assertIsNone(parent.new_version)

    async def test_parent_success_and_checker_error_signal_completion(self):
        for fail_parent in (False, True):
            with self.subTest(fail_parent=fail_parent):
                checker, child, parent = self.make_checker(
                    parent_metadata={"type": "completion-test"}
                )
                visits = []
                checker._checkers = [self.observing_checker(visits, fail_parent)]
                counter = checker.TasksCounter(total=2)
                with patch.object(checker, "TasksCounter", return_value=counter):
                    result = await asyncio.wait_for(checker.check(), timeout=1)
                self.assertEqual(result, [child, parent])
                self.assertTrue(parent.checked.is_set())
                self.assertTrue(child.checked.is_set())
                self.assertEqual(visits, [parent.new_version or parent.current_version])
                self.assertEqual(counter.started, 2)
                self.assertEqual(counter.finished, 1 if fail_parent else 2)
                self.assertEqual(counter.failed, int(fail_parent))
                self.assertEqual(len(checker.get_errors()), int(fail_parent))

    async def test_recheck_waits_for_parent_again(self):
        checker, child, parent = self.make_checker(
            parent_metadata={"type": "completion-test"}
        )
        visits = []
        checker._checkers = [self.observing_checker(visits)]
        # First exclude the parent, then schedule it on the same objects.
        await asyncio.wait_for(checker.check(filter_type="file"), timeout=1)
        await asyncio.wait_for(checker.check(), timeout=1)
        self.assertEqual([state.version for state in visits], [None, "2.0"])
        parent.new_version = None
        await asyncio.wait_for(checker.check(), timeout=1)
        self.assertEqual([state.version for state in visits], [None, "2.0", "2.0"])
