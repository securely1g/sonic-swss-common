"""Keep native completion independent of the informational security scan."""

import os
from pathlib import Path
import re
import subprocess
import unittest


WORKFLOW = (Path(__file__).parents[1] / "workflows/codeql-analysis.yml").read_text()
CPP, GATE = WORKFLOW.split("  analyze-cpp:\n", 1)[1].split("  build-amd64:\n", 1)


def step(name):
    return CPP.split("    - name: " + name + "\n", 1)[1].split("\n    - ", 1)[0]


class NativeGateTest(unittest.TestCase):
    def gate(self, native, analysis, installed="success"):
        # Resolve the workflow's actual output bindings and execute its shell
        # gate, including empty/skipped results rather than assuming success.
        output = re.search(r"native-result: \$\{\{ steps\.([\w-]+)\.outcome \}\}", CPP)
        self.assertIsNotNone(output)
        outcomes = {"native-validation": native, "analyze": analysis}
        result = outcomes[output.group(1)]
        self.assertIn("RESULT: ${{ needs.analyze-cpp.outputs.native-result }}", GATE)
        self.assertNotIn("needs.analyze-cpp.result", GATE)
        command = re.search(r"^        run: (.+)$", GATE, re.M).group(1)
        return subprocess.run(["sh", "-c", command], env={**os.environ, "RESULT": result, "INSTALLED_RESULT": installed}).returncode

    def test_security_query_or_upload_failure_does_not_fail_completed_native_work(self):
        for result in ("success", "failure", "cancelled", "skipped", ""):
            with self.subTest(analysis=result):
                self.assertEqual(self.gate("success", result), 0)

    def test_incomplete_native_work_cannot_pass_even_if_analysis_passes(self):
        for result in ("failure", "cancelled", "skipped", ""):
            with self.subTest(native=result):
                self.assertNotEqual(self.gate(result, "success"), 0)

    def test_installed_runtime_failure_cannot_pass_the_native_gate(self):
        self.assertIn("needs: [analyze-cpp, installed-amd64]", GATE)
        for result in ("failure", "cancelled", "skipped", ""):
            with self.subTest(installed=result):
                self.assertNotEqual(self.gate("success", "success", result), 0)

    def test_native_completion_requires_both_modes_and_every_artifact_upload(self):
        marker = step("Record completed native validation")
        self.assertIn("id: native-validation", marker)
        # The default success() condition prevents any preceding failed native
        # build, test, package collection or artifact upload from passing.
        self.assertNotRegex(marker, r"(?m)^\s+if:")
        self.assertNotIn("continue-on-error", marker)
        completion = CPP.index("    - name: Record completed native validation\n")
        for name in ("Build YANG under CodeQL and reuse outputs for tests and packages",
                     "Build and test no-YANG using the same private outputs"):
            self.assertLess(CPP.index("    - name: " + name + "\n"), completion)
            self.assertNotIn("continue-on-error", step(name))
        uploads = re.findall(r"^    - name: (Upload .+)$", CPP, re.M)
        self.assertTrue(uploads)
        for name in uploads:
            with self.subTest(upload=name):
                self.assertLess(CPP.index("    - name: " + name + "\n"), completion)
                self.assertNotIn("continue-on-error", step(name))
        self.assertLess(completion, CPP.index("    - name: Perform CodeQL Analysis\n"))

    def test_yang_archive_and_tracing_boundary_precede_feature_switch(self):
        finalize = step("Finalize the YANG CodeQL database")
        for setting in ("skip-queries: true", "cleanup-level: none", "upload: false"):
            self.assertIn(setting, finalize)
        ordered = ["Build YANG under CodeQL and reuse outputs for tests and packages",
                   "Finalize the YANG CodeQL database",
                   "Restore the environment before running further build steps",
                   "Verify C++ source extraction",
                   "Build and test no-YANG using the same private outputs"]
        positions = [CPP.index("    - name: " + name + "\n") for name in ordered]
        self.assertEqual(positions, sorted(positions))
        for name in ordered[2:4]:
            self.assertIn("steps.finalize.outputs.db-locations", step(name))
        self.assertIn('database bundle "$codeql_database"', step(ordered[3]))

    def test_scan_still_runs_after_later_native_failure(self):
        analyze = step("Perform CodeQL Analysis")
        self.assertIn("if: ${{ !cancelled() && steps.finalize.outcome == 'success' }}", analyze)
        self.assertNotIn("continue-on-error", analyze)
        self.assertNotIn("skip-queries", analyze)
        self.assertIn('category: "/language:cpp"', analyze)
        action = "github/codeql-action/analyze@ec3cf9c605b848da5f1e41e8452719eb1ccfb9a6"
        self.assertIn(action, analyze)
        self.assertIn(action, step("Finalize the YANG CodeQL database"))


if __name__ == "__main__":
    unittest.main()
