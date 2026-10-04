"""Keep combined AMD64 coverage and evidence aligned with the native workflow."""

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import codeql_build_once as build_once


class NativeCoverageTest(unittest.TestCase):
    def test_combined_job_keeps_the_native_targets_in_both_modes(self):
        workflow = (Path(__file__).parents[1] / "workflows/bazel.yml").read_text()
        for name, yang in (("with YANG", True), ("without YANG", False)):
            command = workflow.split("- name: Build and test " + name + "\n", 1)[1]
            command = command.split("- name:", 1)[0]
            native = set(re.findall(r"^\s+(//\S+)\s*$", command, re.M))
            self.assertTrue(native)
            combined = set(build_once.BUILD_TARGETS + build_once.TEST_TARGETS)
            if yang:
                combined.update(build_once.ANALYSIS_TARGETS)
                combined.update(("//common:cfg_schema_generated", "//tests:defaultvalueprovider_ut"))
            with self.subTest(yang=yang):
                self.assertFalse(native - combined, "Combined AMD64 job drops native targets")


class NativeEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original = Path.cwd()
        self.addCleanup(os.chdir, self.original)
        self.root = Path(self.directory.name)
        os.chdir(self.root)
        subprocess.run(["git", "init", "-q"], check=True)
        Path("Cargo.lock").write_text("version = 4\n")
        subprocess.run(["git", "add", "Cargo.lock"], check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "Record Cargo inputs"], check=True)
        Path("MODULE.bazel.lock").write_text('{"lockFileVersion":24}\n')
        Path(".bazelrc").write_text("common --registry=https://bcr.bazel.build\n")
        self.output = self.root / "private-output"
        self.logs = self.output / "execroot/common/bazel-out/k8-fastbuild/testlogs"
        for target in ("crates/swss-common/swss_common_test", "goext/swsscommon_runtime_test"):
            path = self.logs / target
            path.mkdir(parents=True)
            for name in ("test.xml", "test.log"):
                (path / name).write_text(target + "/" + name)
        Path("bazel-testlogs").symlink_to(self.logs, target_is_directory=True)

    def test_retains_exact_rust_go_and_lock_bytes_for_each_mode(self):
        for mode in ("yang", "no-yang"):
            destination = self.root / "artifacts/validation" / mode
            hashes = build_once.collect_native_evidence(self.output, destination)
            self.assertEqual(set(hashes), {"Cargo.lock", "MODULE.bazel.lock", "effective.bazelrc",
                                          "rust/test.xml", "rust/test.log", "go/test.xml", "go/test.log"})
            for name, digest in hashes.items():
                self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)
            self.assertEqual((destination / "go/test.log").read_text(),
                             "goext/swsscommon_runtime_test/test.log")

    def test_rejects_results_from_another_output_base(self):
        with self.assertRaisesRegex(RuntimeError, "private build"):
            build_once.collect_native_evidence(self.root / "unrelated-output", self.root / "evidence")

    def test_rejects_modified_cargo_input(self):
        Path("Cargo.lock").write_text("version = 3\n")
        with self.assertRaises(subprocess.CalledProcessError):
            build_once.collect_native_evidence(self.output, self.root / "evidence")

    def test_rejects_generated_cargo_lock(self):
        Path("Cargo.Bazel.lock").write_text("unexpected lock")
        with self.assertRaisesRegex(RuntimeError, "tracked Cargo.lock"):
            build_once.collect_native_evidence(self.output, self.root / "evidence")

    def test_missing_rust_result_cannot_claim_native_coverage(self):
        (self.logs / "crates/swss-common/swss_common_test/test.xml").unlink()
        with self.assertRaises(FileNotFoundError):
            build_once.collect_native_evidence(self.output, self.root / "evidence")


if __name__ == "__main__":
    unittest.main()
