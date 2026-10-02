"""Regression cases for extraction omissions that a successful build can hide."""

import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("coverage_check", Path(__file__).with_name("check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ExtractionCoverageTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"common/table.cpp", "tests/shared_library_runtime_test.c"}
        self.rows = [
            {"path": "/src/common/table.cpp", "termination": "normal"},
            {"path": "/src/tests/shared_library_runtime_test.c", "termination": "normal"},
            {
                "path": "/build/execroot/_main/bazel-out/k8/bin/pyext/py3/swsscommon_wrap.cpp",
                "termination": "normal",
            },
        ]

    def receipt(self, rows=None, expected=None):
        return CHECK.check_rows(
            self.expected if expected is None else expected,
            self.rows if rows is None else rows, "/src",
        )

    def test_external_generated_source_is_required_but_reporting_is_not_claimed(self):
        receipt = self.receipt()
        self.assertTrue(receipt["passed"])
        self.assertEqual(receipt["generated_wrapper"]["paths_under_source_root"], [])

    def test_cache_hit_that_skips_tracked_compilation_fails(self):
        receipt = self.receipt(self.rows[1:])
        self.assertFalse(receipt["passed"])
        self.assertEqual(receipt["missing_tracked_sources"], ["common/table.cpp"])

    def test_missing_generated_wrapper_fails(self):
        self.assertFalse(self.receipt(self.rows[:2])["passed"])

    def test_dependency_wrapper_cannot_satisfy_generated_wrapper(self):
        rows = self.rows[:2] + [{
            "path": "/build/bazel-out/k8/bin/external/other/pyext/py3/swsscommon_wrap.cpp",
            "termination": "normal",
        }]
        self.assertFalse(self.receipt(rows)["passed"])

    def test_failed_extraction_is_not_hidden_by_a_successful_duplicate(self):
        rows = self.rows + [{"path": "/src/common/table.cpp", "termination": "abnormal"}]
        self.assertFalse(self.receipt(rows)["passed"])

    def test_dependency_with_same_suffix_cannot_satisfy_tracked_source(self):
        rows = self.rows[1:] + [
            {"path": "/build/external/other/common/table.cpp", "termination": "normal"}
        ]
        self.assertFalse(self.receipt(rows)["passed"])

    def test_new_tracked_source_requires_extraction(self):
        self.assertFalse(self.receipt(expected=self.expected | {"common/new.cpp"})["passed"])

    def test_c_source_is_required_alongside_cpp(self):
        self.assertFalse(self.receipt([self.rows[0], self.rows[2]])["passed"])

    def test_empty_inventory_is_not_a_success(self):
        with self.assertRaises(ValueError):
            self.receipt(expected=set())


if __name__ == "__main__":
    unittest.main()
