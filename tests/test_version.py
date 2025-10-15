#!/usr/bin/env python3

import unittest

from src.lib.version import LooseVersion


class TestLooseVersion(unittest.TestCase):
    def test_cmp(self):
        test_cases = [
            ("1.5.1", "1.5.2b2", -1),
            ("161", "3.10a", 1),
            ("8.02", "8.02", 0),
            ("3.4j", "1996.07.12", -1),
            ("3.2.pl0", "3.1.1.6", 1),
            ("2g6", "11g", -1),
            ("0.960923", "2.2beta29", -1),
            ("1.13++", "5.5.kw", -1),
        ]

        for v1, v2, result in test_cases:
            with self.subTest(v1=v1, v2=v2, result=result):
                loosev1 = LooseVersion(v1)
                loosev2 = LooseVersion(v2)

                self.assertEqual(loosev1._compare_to(loosev2), result)
                self.assertEqual(loosev1._compare_to(v2), result)
                self.assertEqual(loosev2._compare_to(loosev1), -result)
                self.assertEqual(loosev2._compare_to(v1), -result)

                self.assertEqual(loosev1._compare_to(object()), NotImplemented)
                self.assertEqual(loosev2._compare_to(object()), NotImplemented)

    def test_split(self):
        test_cases = [
            ("1.5.1", [1, 5, 1]),
            ("1.5.2b2", [1, 5, 2, "b", 2]),
            ("161", [161]),
            ("3.10a", [3, 10, "a"]),
            ("1.13++", [1, 13, "++"]),
        ]

        for vstring, expected_version in test_cases:
            with self.subTest(vstring=vstring):
                v = LooseVersion(vstring)
                self.assertEqual(v.vstring, vstring)
                self.assertEqual(v.version, expected_version)

    def test_py3_rules(self):
        test_cases = [
            ("0.3@v0.3", "0.3.1@v0.3.1", 1),
            ("0.3.1@v0.3.1", "0.3@v0.3", -1),
            ("13.0-beta3", "13.0.1", 1),
            ("13.0.1", "13.0-beta3", -1),
        ]

        for v1, v2, result in test_cases:
            with self.subTest(v1=v1, v2=v2, result=result):
                loosev1 = LooseVersion(v1)
                loosev2 = LooseVersion(v2)

                self.assertEqual(loosev1._compare_to(loosev2), result)
                self.assertEqual(loosev1._compare_to(v2), result)
                self.assertEqual(loosev2._compare_to(loosev1), -result)
                self.assertEqual(loosev2._compare_to(v1), -result)

    def test_invalid_comparison(self):
        v1 = LooseVersion("1")

        self.assertEqual(v1._compare_to(1), NotImplemented)
        self.assertEqual(v1._compare_to([1, 2, 3]), NotImplemented)
        self.assertEqual(v1._compare_to(None), NotImplemented)

    def test_invalid_ordering_comparison(self):
        v1 = LooseVersion("1")

        with self.assertRaises(TypeError):
            v1 < 1
        with self.assertRaises(TypeError):
            v1 <= 1
        with self.assertRaises(TypeError):
            v1 > 1
        with self.assertRaises(TypeError):
            v1 >= 1

    def test_equality_comparison(self):
        self.assertFalse(LooseVersion("1") == 1)
        self.assertTrue(LooseVersion("1") != 1)

    def test_different_length_versions(self):
        self.assertEqual(LooseVersion("1.0")._compare_to(LooseVersion("1.0.1")), -1)
        self.assertEqual(LooseVersion("1.0.1")._compare_to(LooseVersion("1.0")), 1)

        self.assertEqual(LooseVersion("1.0a")._compare_to(LooseVersion("1.0a.1")), -1)
        self.assertEqual(LooseVersion("1.0a.1")._compare_to(LooseVersion("1.0a")), 1)

    def test_string_comparison_different_strings(self):
        self.assertEqual(LooseVersion("1.0a")._compare_to(LooseVersion("1.0b")), -1)
        self.assertEqual(LooseVersion("1.0b")._compare_to(LooseVersion("1.0a")), 1)

        self.assertEqual(
            LooseVersion("1.0alpha")._compare_to(LooseVersion("1.0beta")), -1
        )
        self.assertEqual(
            LooseVersion("1.0beta")._compare_to(LooseVersion("1.0alpha")), 1
        )

    def test_equal_strings(self):
        self.assertEqual(LooseVersion("1.0a")._compare_to(LooseVersion("1.0a")), 0)
        self.assertEqual(
            LooseVersion("1.0beta")._compare_to(LooseVersion("1.0beta")), 0
        )

    def test_complex_mixed_versions(self):
        self.assertEqual(LooseVersion("1.0a.1")._compare_to(LooseVersion("1.0a.2")), -1)
        self.assertEqual(LooseVersion("1.0a.2")._compare_to(LooseVersion("1.0a.1")), 1)

        self.assertEqual(
            LooseVersion("1.0a.1.x")._compare_to(LooseVersion("1.0a.1.x")), 0
        )


if __name__ == "__main__":
    unittest.main()
