#!/usr/bin/env python3
"""Regression tests for evidence that runtime tests reuse traced build outputs."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "codeql_build_once", Path(__file__).with_name("codeql_build_once.py")
)
BUILD_ONCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_ONCE)


class ReuseEvidenceTest(unittest.TestCase):
    LABEL = "//tests:shared_library_runtime_test"
    SECOND_LABEL = "//goext:swsscommon_runtime_test"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.execution_log = self.root / "spawns.json"
        self.events_log = self.root / "events.json"

    def spawn(self, **changes):
        result = {
            "mnemonic": "TestRunner",
            "targetLabel": self.LABEL,
            "runner": "local",
            "cacheHit": False,
            "exitCode": 0,
        }
        result.update(changes)
        return result

    def events(self, label=None):
        label = label or self.LABEL
        return [
            {
                "id": {"testResult": {"label": label, "run": 1, "shard": 0, "attempt": 1}},
                "testResult": {
                    "status": "PASSED",
                    "cachedLocally": False,
                    "executionInfo": {"cachedRemotely": False, "exitCode": 0},
                },
            },
            {
                "id": {"testSummary": {"label": label}},
                "testSummary": {
                    "overallStatus": "PASSED",
                    "totalRunCount": 1,
                    "totalNumCached": 0,
                },
            },
        ]

    def verify(self, spawns=None, events=None, labels=None):
        if spawns is None:
            spawns = [self.spawn()]
        if events is None:
            events = self.events()
        if labels is None:
            labels = [self.LABEL]
        # Execution-log JSON is a stream of pretty-printed objects, not JSONL.
        self.execution_log.write_text("\n\n".join(json.dumps(s, indent=2) for s in spawns))
        self.events_log.write_text("".join(json.dumps(e) + "\n" for e in events))
        return BUILD_ONCE.verify_reuse(self.execution_log, self.events_log, labels)

    def test_success_requires_fresh_tests_but_allows_locally_cached_build_outputs(self):
        # Persistent local action-cache hits produce no execution-log entry.
        result = self.verify(
            spawns=[self.spawn(), self.spawn(targetLabel=self.SECOND_LABEL)],
            events=self.events() + self.events(self.SECOND_LABEL),
            labels=[self.LABEL, self.SECOND_LABEL],
        )
        self.assertEqual(result["executed_non_test_spawns"], 0)
        self.assertEqual(result["passed_tests"], sorted([self.LABEL, self.SECOND_LABEL]))

    def test_json_execution_log_accepts_multiple_objects_and_whitespace(self):
        expected = [self.spawn(), self.spawn(targetLabel=self.SECOND_LABEL)]
        self.execution_log.write_text(" \n" + " \n\t".join(json.dumps(s, indent=2) for s in expected) + "\n")
        self.assertEqual(list(BUILD_ONCE.read_spawns(self.execution_log)), expected)

    def test_json_execution_log_rejects_truncated_record(self):
        self.execution_log.write_text(json.dumps(self.spawn()) + '\n{"mnemonic":')
        with self.assertRaises(json.JSONDecodeError):
            list(BUILD_ONCE.read_spawns(self.execution_log))

    def test_rejects_new_build_spawns_including_nested_cgo_compilation(self):
        for mnemonic in ("CppCompile", "CppLink", "CppLinkstampCompile", "GoCompilePkg", "Genrule"):
            with self.subTest(mnemonic=mnemonic), self.assertRaises(RuntimeError):
                self.verify(spawns=[self.spawn(), self.spawn(mnemonic=mnemonic)])

    def test_rejects_disk_or_remote_build_cache_hits(self):
        # Bazel's cacheHit field does not represent the allowed local action cache.
        with self.assertRaises(RuntimeError):
            self.verify(spawns=[self.spawn(), self.spawn(mnemonic="CppCompile", cacheHit=True)])

    def test_rejects_missing_expected_test_summary(self):
        with self.assertRaises(RuntimeError):
            self.verify(events=self.events()[:1])

    def test_rejects_summary_without_an_executed_attempt(self):
        with self.assertRaises(RuntimeError):
            self.verify(events=self.events()[1:])

    def test_rejects_missing_one_of_multiple_expected_tests(self):
        with self.assertRaises(RuntimeError):
            self.verify(labels=[self.LABEL, self.SECOND_LABEL])

    def test_rejects_failed_or_skipped_summary(self):
        for status in ("FAILED", "NO_STATUS", "TIMEOUT", "FLAKY"):
            events = self.events()
            events[1]["testSummary"]["overallStatus"] = status
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.verify(events=events)

    def test_rejects_locally_cached_test_result(self):
        events = self.events()
        events[0]["testResult"]["cachedLocally"] = True
        with self.assertRaises(RuntimeError):
            self.verify(events=events)

    def test_rejects_remotely_cached_test_result(self):
        events = self.events()
        events[0]["testResult"]["executionInfo"]["cachedRemotely"] = True
        with self.assertRaises(RuntimeError):
            self.verify(events=events)

    def test_rejects_failed_attempt_despite_passed_summary(self):
        events = self.events()
        events[0]["testResult"]["status"] = "FAILED"
        with self.assertRaises(RuntimeError):
            self.verify(events=events)

    def test_rejects_zero_run_summary(self):
        events = self.events()
        events[1]["testSummary"]["totalRunCount"] = 0
        with self.assertRaises(RuntimeError):
            self.verify(events=events)

    def test_rejects_summary_with_any_cached_tests(self):
        events = self.events()
        events[1]["testSummary"]["totalNumCached"] = 1
        with self.assertRaises(RuntimeError):
            self.verify(events=events)

    def test_rejects_empty_execution_log(self):
        with self.assertRaises(RuntimeError):
            self.verify(spawns=[])

    def test_rejects_missing_expected_test_spawn(self):
        with self.assertRaises(RuntimeError):
            self.verify(spawns=[self.spawn(targetLabel=self.SECOND_LABEL)])

    def test_rejects_cached_test_spawn_despite_fresh_bep_result(self):
        with self.assertRaises(RuntimeError):
            self.verify(spawns=[self.spawn(cacheHit=True)])

    def test_rejects_failed_test_spawn_despite_passed_bep_result(self):
        with self.assertRaises(RuntimeError):
            self.verify(spawns=[self.spawn(exitCode=1, status="NON_ZERO_EXIT")])


if __name__ == "__main__":
    unittest.main()
