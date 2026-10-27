#!/usr/bin/env python3
"""Reject false positives in the Common-to-buildimage remote-cache proof."""

from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cross_repo


def action(mnemonic, target, output, digest, *, remote=False):
    return {
        "mnemonic": mnemonic,
        "targetLabel": "@@sonic-swss-common+//:" + target,
        "listedOutputs": ["bazel-out/k8-fastbuild/bin/external/sonic-swss-common+/" + output],
        "digest": {"hash": digest * 64, "sizeBytes": "123"},
        "runner": "remote cache hit" if remote else "linux-sandbox",
        "cacheHit": remote,
        "exitCode": 0,
    }


def common_actions(*, remote=False):
    return [
        action("CppCompile", "common", "_objs/common/dbconnector.pic.o", "a", remote=remote),
        action("CppCompile", "common", "_objs/common/logger.pic.o", "b", remote=remote),
        action("CppLink", "libswsscommon_consolidated.so", "libswsscommon_consolidated.so", "c", remote=remote),
        action("CppLink", "sonic-db-cli", "sonic-db-cli", "d", remote=remote),
    ]


class ActionEvidenceTest(TestCase):
    def summarize(self, records):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution.json"
            # Bazel's JSON execution log is a sequence of objects, not an array.
            path.write_text("\n".join(json.dumps(record, indent=2) for record in records))
            return cross_repo.summarize_actions(path)

    def phase(self, *, remote=False):
        summary = self.summarize(common_actions(remote=remote))
        summary["artifacts"] = {
            cross_repo.TARGETS[0]: {"sha256": "e" * 64, "bytes": 123, "library_sha256": "f" * 64},
            cross_repo.TARGETS[1]: {"sha256": "1" * 64, "bytes": 456},
        }
        return summary

    def test_accepts_complete_remote_reuse_even_when_log_order_changes(self):
        seed = self.phase()
        read = self.phase(remote=True)
        read["common_actions"].reverse()
        cross_repo.compare_phases(seed, read)

    def test_only_canonical_common_cpp_actions_are_evidence(self):
        records = common_actions(remote=True)
        for label in (
            "//:common",
            "@sonic_swss_common//:common",
            "@@sonic-swss-common-extra+//:common",
            "@@other-module+//:common",
        ):
            unrelated = deepcopy(records[0])
            unrelated["targetLabel"] = label
            unrelated["runner"] = "linux-sandbox"
            unrelated["cacheHit"] = False
            records.append(unrelated)
        unrelated = deepcopy(records[0])
        unrelated["mnemonic"] = "Genrule"
        records.append(unrelated)
        summary = self.summarize(records)
        self.assertEqual(len(summary["common_actions"]), 4)
        cross_repo.check_actions(summary, remote=True)
        self.assertEqual(sum(row["count"] for row in summary["action_counts"]), len(records))

    def test_canonical_module_version_suffix_is_recognized(self):
        records = common_actions(remote=True)
        for record in records:
            record["targetLabel"] = record["targetLabel"].replace("common+//", "common+1.0.0//")
        summary = self.summarize(records)
        self.assertEqual(len(summary["common_actions"]), 4)
        cross_repo.check_actions(summary, remote=True)

    def test_export_omits_commands_environment_and_credentials(self):
        secret = "test-only-credential-never-upload"
        records = common_actions(remote=True)
        for record in records:
            record.update({
                "commandArgs": ["compiler", "--remote_header=Authorization=" + secret],
                "environmentVariables": [
                    {"name": "BAZEL_REMOTE_AUTH", "value": secret},
                    {"name": "UNEXPECTED_TOKEN", "value": secret},
                    {"name": "PATH", "value": "/bin:/usr/bin"},
                    {"name": "LD_LIBRARY_PATH", "value": "/opt/toolchain/lib"},
                    {"name": "PWD", "value": "/proc/self/cwd"},
                ],
                "inputs": [{"path": "private/" + secret, "digest": {"hash": "e" * 64}}],
                "unknownFutureField": {"password": secret},
            })
        summary = self.summarize(records)
        encoded = json.dumps(summary)
        for forbidden in (secret, "commandArgs", "environmentVariables", "unknownFutureField", "--remote_header"):
            self.assertNotIn(forbidden, encoded)
        for record in summary["common_actions"]:
            self.assertEqual(set(record), {"mnemonic", "target", "outputs", "digest", "runner", "cache_hit", "diagnostics"})
            diagnostics = record["diagnostics"]
            self.assertEqual(diagnostics["environment"], {
                "PATH": "/bin:/usr/bin", "LD_LIBRARY_PATH": "/opt/toolchain/lib", "PWD": "/proc/self/cwd"})
            self.assertEqual(diagnostics["environment_names"], [
                "BAZEL_REMOTE_AUTH", "LD_LIBRARY_PATH", "PATH", "PWD", "UNEXPECTED_TOKEN"])
            self.assertRegex(diagnostics["command_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(diagnostics["inputs_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(diagnostics["input_count"], 1)

    def test_diagnostic_fingerprints_are_stable_and_detect_changes(self):
        record = common_actions()[0]
        record.update(commandArgs=["compiler", "-c", "file.cc"], inputs=[
            {"path": "file.cc", "digest": {"hash": "e" * 64, "sizeBytes": "20"}},
            {"path": "compiler", "isTool": True, "digest": {"hash": "f" * 64}},
        ])
        original = self.summarize([record])["common_actions"][0]["diagnostics"]
        reordered = deepcopy(record)
        reordered["inputs"].reverse()
        self.assertEqual(original, self.summarize([reordered])["common_actions"][0]["diagnostics"])
        for field, mutate in (
            ("command_sha256", lambda item: item["commandArgs"].reverse()),
            ("inputs_sha256", lambda item: item["inputs"][0]["digest"].update(hash="a" * 64)),
            ("inputs_sha256", lambda item: item["inputs"][0].update(path="other.cc")),
            ("inputs_sha256", lambda item: item["inputs"][1].update(isTool=False)),
        ):
            with self.subTest(field=field, mutate=mutate):
                changed = deepcopy(record)
                mutate(changed)
                actual = self.summarize([changed])["common_actions"][0]["diagnostics"]
                self.assertNotEqual(original[field], actual[field])
                other = "inputs_sha256" if field == "command_sha256" else "command_sha256"
                self.assertEqual(original[other], actual[other])

    def test_missing_diagnostics_are_explicit_and_do_not_change_action_identity(self):
        seed, read = self.phase(), self.phase(remote=True)
        diagnostics = seed["common_actions"][0]["diagnostics"]
        self.assertEqual(diagnostics, {"environment": {}, "environment_names": [],
                                      "command_sha256": None, "inputs_sha256": None, "input_count": None})
        read["common_actions"][0]["diagnostics"] = {"command_sha256": "f" * 64}
        cross_repo.compare_phases(seed, read)

    def test_rejects_failed_or_unidentified_actions(self):
        invalid_fields = (
            {"digest": {}},
            {"digest": {"hash": "a" * 63}},
            {"digest": {"hash": "g" * 64}},
            {"listedOutputs": []},
            {"exitCode": 1},
            {"status": "EXECUTION_FAILED"},
        )
        for replacement in invalid_fields:
            with self.subTest(replacement=replacement):
                records = common_actions()
                records[0].update(replacement)
                with self.assertRaises(ValueError):
                    self.summarize(records)

    def test_one_cache_hit_cannot_hide_a_local_compile_or_link(self):
        for index in range(4):
            with self.subTest(action=index):
                records = common_actions(remote=True)
                records[index].update(runner="linux-sandbox", cacheHit=False)
                with self.assertRaisesRegex(ValueError, "readback rebuilt"):
                    cross_repo.check_actions(self.summarize(records), remote=True)

    def test_disk_cache_or_runner_name_alone_is_not_remote_reuse(self):
        for runner, hit in (("disk cache hit", True), ("linux-sandbox", True), ("remote cache hit", False)):
            with self.subTest(runner=runner, hit=hit):
                records = common_actions(remote=True)
                records[0].update(runner=runner, cacheHit=hit)
                with self.assertRaisesRegex(ValueError, "readback rebuilt"):
                    cross_repo.check_actions(self.summarize(records), remote=True)

    def test_requires_compilation_and_the_actual_library_link(self):
        scenarios = {
            "no actions": [],
            "no compile": common_actions(remote=True)[2:],
            "no link": common_actions(remote=True)[:2],
            "only cli link": [row for row in common_actions(remote=True)
                              if not row["targetLabel"].endswith("//:libswsscommon_consolidated.so")],
        }
        false_link = common_actions(remote=True)
        false_link[2]["mnemonic"] = "CppCompile"
        scenarios["library target only compiled"] = false_link
        for name, records in scenarios.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    cross_repo.check_actions(self.summarize(records), remote=True)

    def test_seed_must_really_compile_every_common_action(self):
        for runner, hit in (("remote cache hit", True), ("linux-sandbox", True), ("local", False)):
            with self.subTest(runner=runner, hit=hit):
                records = common_actions()
                records[1].update(runner=runner, cacheHit=hit)
                with self.assertRaisesRegex(ValueError, "seed must execute"):
                    cross_repo.check_actions(self.summarize(records), remote=False)

    def test_readback_must_match_the_seed_action_inventory(self):
        mutations = {
            "changed digest": lambda rows: rows[0].update(digest="9" * 64),
            "changed target": lambda rows: rows[0].update(target="@@sonic-swss-common+//:other"),
            "changed output": lambda rows: rows[0].update(outputs=["bazel-out/other.o"]),
            "missing compile": lambda rows: rows.pop(0),
            "duplicate compile": lambda rows: rows.append(deepcopy(rows[0])),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                seed, read = self.phase(), self.phase(remote=True)
                mutate(read["common_actions"])
                with self.assertRaisesRegex(ValueError, "different compile/link actions"):
                    cross_repo.compare_phases(seed, read)

    def test_runtime_and_debug_packages_must_both_match(self):
        for target, field in (
            (cross_repo.TARGETS[0], "sha256"),
            (cross_repo.TARGETS[0], "library_sha256"),
            (cross_repo.TARGETS[1], "sha256"),
            (cross_repo.TARGETS[1], "bytes"),
        ):
            with self.subTest(target=target, field=field):
                seed, read = self.phase(), self.phase(remote=True)
                read["artifacts"][target][field] = 999 if field == "bytes" else "9" * 64
                with self.assertRaisesRegex(ValueError, "different runtime/debug packages"):
                    cross_repo.compare_phases(seed, read)


class PackageEvidenceTest(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "common.tar"

    @staticmethod
    def elf(machine):
        contents = bytearray(64)
        contents[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", contents, 18, machine)
        return bytes(contents)

    def write_library(self, contents, *, architecture="amd64", duplicates=1, symlink=False):
        triplet = "x86_64-linux-gnu" if architecture == "amd64" else "aarch64-linux-gnu"
        with tarfile.open(self.path, "w") as archive:
            for _ in range(duplicates):
                member = tarfile.TarInfo("./usr/lib/" + triplet + "/libswsscommon.so.0.0.0")
                if symlink:
                    member.type, member.linkname = tarfile.SYMTYPE, "elsewhere"
                    archive.addfile(member)
                else:
                    member.size = len(contents)
                    archive.addfile(member, io.BytesIO(contents))

    def test_native_library_receipt_hashes_library_and_entire_archive(self):
        for architecture, machine in (("amd64", 62), ("arm64", 183)):
            with self.subTest(architecture=architecture):
                contents = self.elf(machine)
                self.write_library(contents, architecture=architecture)
                receipt = cross_repo.package_receipt(self.path, architecture, debug=False)
                self.assertEqual(receipt["library_sha256"], hashlib.sha256(contents).hexdigest())
                self.assertEqual(receipt["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
                self.assertEqual(receipt["bytes"], self.path.stat().st_size)

    def test_wrong_architecture_or_invalid_elf_is_rejected(self):
        for contents in (self.elf(183), b"not an ELF", b"\x7fELF\x02\x01"):
            with self.subTest(contents=contents):
                self.write_library(contents)
                with self.assertRaisesRegex(ValueError, "native ELF architecture"):
                    cross_repo.package_receipt(self.path, "amd64", debug=False)

    def test_missing_duplicate_or_symlinked_library_is_not_evidence(self):
        for options in ({"duplicates": 0}, {"duplicates": 2}, {"symlink": True}, {"architecture": "arm64"}):
            with self.subTest(options=options):
                self.write_library(self.elf(62), **options)
                with self.assertRaisesRegex(ValueError, "exactly one native Common library"):
                    cross_repo.package_receipt(self.path, "amd64", debug=False)


class SeedModeTest(TestCase):
    def setUp(self):
        context = patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/master"})
        context.start()
        self.addCleanup(context.stop)

    def test_pr_and_feature_dispatch_cannot_seed_even_when_called_directly(self):
        for event, ref in (("pull_request", "refs/pull/35/merge"),
                           ("workflow_dispatch", "refs/heads/feature")):
            with self.subTest(event=event), patch.dict(os.environ, {
                    "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref}), \
                    patch.object(cross_repo, "run") as run:
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    with self.assertRaisesRegex(ValueError, "master branch CI"):
                        cross_repo.build_phase("seed", root, root, root, "amd64")
                run.assert_not_called()

    def test_readback_does_not_select_credentials_even_in_a_writer_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cross_repo, "run", side_effect=RuntimeError("captured")) as run:
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    cross_repo.build_phase("read", root, root, root, "amd64")
            options = run.call_args.args[0]
            self.assertIn("--remote_upload_local_results=false", options)
            self.assertFalse(any(arg.startswith("--credential_helper=") for arg in options))

    def invoke(self, *, seed_for_make=False, invalid_seed=False):
        calls = []
        sources = []

        def export(source, destination):
            destination.mkdir()
            sources.append(destination)

        def build(name, workspace, common_source, temporary, architecture, **options):
            calls.append((name, options))
            self.assertTrue(common_source.is_dir())
            self.assertFalse((workspace / "MODULE.bazel.lock").exists())
            summary = ActionEvidenceTest().phase(remote=name == "read" or invalid_seed)
            summary["phase"] = name
            summary["artifacts"] = {
                target: {"sha256": "e" * 64, "bytes": 123}
                for target in options.get("queries", cross_repo.TARGETS)
            }
            (workspace / "MODULE.bazel.lock").write_text('{"lockFileVersion": 26}\n')
            return summary

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifacts/cross-repo.json"
            argv = ["cross_repo.py", "--buildimage", directory,
                    "--buildimage-revision", "1" * 40, "--architecture", "amd64",
                    "--output", str(output)]
            if seed_for_make:
                argv.append("--seed-for-make")
            stdout = io.StringIO()
            error = None
            with patch.object(sys, "argv", argv), \
                    patch.object(cross_repo.platform, "machine", return_value="x86_64"), \
                    patch.object(cross_repo.credentials, "authorization", return_value="unused-test-auth"), \
                    patch.object(cross_repo.subprocess, "check_output", side_effect=["1" * 40, "2" * 40]), \
                    patch.object(cross_repo, "export_checkout", side_effect=export), \
                    patch.object(cross_repo, "build_phase", side_effect=build), redirect_stdout(stdout):
                try:
                    cross_repo.main()
                except ValueError as failure:
                    error = str(failure)
            receipt = json.loads(output.read_text())
            locks = sorted(path.name for path in output.parent.glob("*-MODULE.bazel.lock"))
            self.assertTrue(all(not path.exists() for path in sources))
        return receipt, calls, locks, stdout.getvalue(), error

    def test_default_still_runs_original_two_target_seed_and_read_proof(self):
        receipt, calls, locks, stdout, error = self.invoke()
        self.assertIsNone(error)
        self.assertTrue(receipt["passed"])
        self.assertNotIn("mode", receipt)
        self.assertEqual(receipt["targets"], cross_repo.TARGETS)
        self.assertEqual(calls, [("seed", {}), ("read", {})])
        self.assertEqual(locks, ["read-MODULE.bazel.lock", "seed-MODULE.bazel.lock"])
        self.assertIn("Verified Common seed -> read-only buildimage cache reuse", stdout)

    def test_make_seed_keeps_six_hashes_without_claiming_readback(self):
        receipt, calls, locks, stdout, error = self.invoke(seed_for_make=True)
        self.assertIsNone(error)
        self.assertTrue(receipt["passed"])
        self.assertEqual(receipt["mode"], "make_seed")
        self.assertEqual(receipt["common_revision"], "2" * 40)
        self.assertEqual(calls, [("seed", {"targets": cross_repo.MAKE_TARGETS,
                                           "queries": cross_repo.MAKE_PACKAGES})])
        self.assertEqual([phase["phase"] for phase in receipt["phases"]], ["seed"])
        self.assertEqual(set(receipt["phases"][0]["artifacts"]), set(cross_repo.MAKE_PACKAGES))
        self.assertEqual(len(receipt["phases"][0]["artifacts"]), 6)
        self.assertEqual(locks, ["seed-MODULE.bazel.lock"])
        self.assertIn("for a later Make cache proof", stdout)
        self.assertNotIn("Verified Common seed -> read-only", stdout)

    def test_make_seed_rejects_cached_compilation_and_retains_failure_evidence(self):
        receipt, calls, locks, stdout, error = self.invoke(seed_for_make=True, invalid_seed=True)
        self.assertIn("seed must execute every compile/link", error)
        self.assertFalse(receipt["passed"])
        self.assertEqual(receipt["mode"], "make_seed")
        self.assertEqual(receipt["error"], error)
        self.assertEqual(len(calls), 1)
        self.assertEqual(locks, ["seed-MODULE.bazel.lock"])
        self.assertNotIn("Seeded Common image inputs", stdout)

    def test_make_seed_builds_execution_inputs_but_hashes_only_six_packages(self):
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            output_base = root / "seed-output"

            def run(argv, cwd, log):
                commands.append(argv)
                if "build" in argv:
                    output_base.mkdir()
                    records = common_actions()
                    (root / "seed-actions.json").write_text("\n".join(json.dumps(record) for record in records))
                    for index, target in enumerate(cross_repo.MAKE_PACKAGES):
                        path = output_base / (str(index) + ".tar")
                        if target == cross_repo.TARGETS[0]:
                            with tarfile.open(path, "w") as archive:
                                data = PackageEvidenceTest.elf(62)
                                info = tarfile.TarInfo("usr/lib/x86_64-linux-gnu/libswsscommon.so.0.0.0")
                                info.size = len(data)
                                archive.addfile(info, io.BytesIO(data))
                        else:
                            path.write_bytes(target.encode())
                    return ""
                return str(output_base / (str(cross_repo.MAKE_PACKAGES.index(argv[-1])) + ".tar")) + "\n"

            with patch.object(cross_repo, "run", side_effect=run), redirect_stdout(io.StringIO()):
                summary = cross_repo.build_phase("seed", workspace, root / "common", root, "amd64",
                                                 targets=cross_repo.MAKE_TARGETS,
                                                 queries=cross_repo.MAKE_PACKAGES)
            self.assertEqual(commands[0][-7:], cross_repo.MAKE_TARGETS)
            self.assertEqual([command[-1] for command in commands[1:]], cross_repo.MAKE_PACKAGES)
            for command in commands:
                for flag in ("--remote_accept_cached=false", "--remote_upload_local_results=true",
                             "--execution_log_sort=false",
                             "--disk_cache=", "--remote_executor=", "--spawn_strategy=linux-sandbox",
                             "--noincompatible_legacy_local_fallback"):
                    self.assertIn(flag, command)
            self.assertFalse(summary["remote_reads"])
            self.assertTrue(summary["remote_writes"])
            self.assertTrue(summary["fresh_output_base"])
            for target, receipt in summary["artifacts"].items():
                index = cross_repo.MAKE_PACKAGES.index(target)
                self.assertEqual(receipt["sha256"], hashlib.sha256((output_base / (str(index) + ".tar")).read_bytes()).hexdigest())
                self.assertEqual("library_sha256" in receipt, target == cross_repo.TARGETS[0])
            cross_repo.check_actions(summary, remote=False)


if __name__ == "__main__":
    main()
