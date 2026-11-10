import unittest

from src.lib.errors import CheckerFetchError, CheckerQueryError


class TestErrorFormatting(unittest.TestCase):
    def test_error_without_cause(self):
        self.assertEqual(str(CheckerFetchError()), "Error downloading upstream source")
        self.assertEqual(str(CheckerFetchError("Custom message")), "Custom message")

    def test_nonempty_cause_is_preserved(self):
        error = CheckerFetchError()
        error.__cause__ = OSError("Connection lost")
        self.assertEqual(
            str(error), "Error downloading upstream source: Connection lost"
        )

    def test_empty_cause_does_not_expose_arguments(self):
        class EmptyError(Exception):
            def __str__(self):
                return ""

        error = CheckerFetchError()
        error.__cause__ = EmptyError("https://example.com/download?token=sensitive")
        self.assertEqual(str(error), "Error downloading upstream source: EmptyError")

    def test_nested_cause_retains_context(self):
        inner = CheckerQueryError()
        inner.__cause__ = TimeoutError()
        outer = CheckerFetchError()
        outer.__cause__ = inner
        self.assertEqual(
            str(outer),
            "Error downloading upstream source: Error querying for new versions: "
            "TimeoutError",
        )
