"""Exercise benchmark orchestration with a fake executable, never real Bazel."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import TestCase, main, mock


spec = importlib.util.spec_from_file_location(
    "cache_benchmark", Path(__file__).with_name("cache_benchmark.py"))
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
FIXTURE_MARKER = 'fixture-"secret\\DO-NOT-PUBLISH'
# A public marker shared with the fake process; no real credential enters the fixture.
FAKE = 'FIXTURE_MARKER = ' + repr(FIXTURE_MARKER) + '\n' + r'''
import gzip, json, os, pathlib, shlex, stat, sys
root = pathlib.Path.cwd()
scenario = json.loads((root / "scenario.json").read_text())
secret = FIXTURE_MARKER
credential_values = scenario.get("credential_values", [])
diagnostic = " | ".join(credential_values) if credential_values else secret
args = sys.argv[1:]
def option(name):
    return next((a.split("=", 1)[1] for a in args if a.startswith(name + "=")), None)
base = pathlib.Path(option("--output_base"))
yang = option("--//tools/bazel:yang_modules") == "True"
stem = base.name + ("-yang" if yang else "-no-yang")
rc = pathlib.Path(option("--bazelrc")) if option("--bazelrc") else None
disk = pathlib.Path(option("--disk_cache")) if option("--disk_cache") else None
record = {"argv": args, "base": str(base), "fresh": not base.exists(),
          "remaining_cases": [p.name for p in base.parent.iterdir() if p.name in
                              ("preparation", "baseline", "populate", "remote", "combined")],
          "disk": str(disk) if disk else None,
          "disk_entries": sorted(p.name for p in disk.iterdir()) if disk else [],
          "secret_in_argv": any(secret in a for a in args),
          "secret_in_env": any(secret in value for value in os.environ.values()),
          "key_env_present": "BUILDBUDDY_API_KEY" in os.environ,
          "credential_names_present": [name for name in scenario.get("credential_names", []) if name in os.environ],
          "credential_values_present": [index for index, value in enumerate(credential_values)
                                        if any(value in item for item in os.environ.values())],
          "ordinary_env": os.environ.get("BENCHMARK_FIXTURE_VISIBLE"),
          "rc": str(rc) if rc else None,
          "rc_mode": stat.S_IMODE(rc.stat().st_mode) if rc else None,
          "rc_valid": bool(rc and shlex.split(rc.read_text()) ==
                           ["common", "--remote_header=x-buildbuddy-api-key=" + secret])}
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(record) + "\n")
base.mkdir(exist_ok=True)
if disk:
    (disk / (base.name + "-write")).write_text("new cache entry")
print("diagnostic " + diagnostic)
(root / "MODULE.bazel.lock").write_text(json.dumps({"diagnostic": diagnostic}))
if "test" in args:
    client_env = "BENCHMARK_FIXTURE_VISIBLE=" + os.environ.get("BENCHMARK_FIXTURE_VISIBLE", "")
    events = [{"unstructuredCommandLine": {"args": ["--client_env=" + client_env,
                  "--client_env", "UNCLASSIFIED=split-client-env-canary", "--keep_going"]}},
              {"structuredCommandLine": {"sections": [{"optionList": {"option": [
                  {"optionName": "client_env", "optionValue": client_env,
                   "combinedForm": "--client_env=" + client_env},
                  {"optionName": "jobs", "optionValue": "2"}]}}]}},
              {"progress": {"stdout": diagnostic}}, {"finished": {"exitCode": {}}},
              {"buildMetrics": {"actionSummary": {"runnerCount": [
                  {"name": "total", "count": 30},
                  {"name": "remote cache hit", "count": 5 if base.name == "remote" and not scenario.get("zero_hits") else 0}]}}}]
    pathlib.Path(option("--build_event_json_file")).write_text(
        "\n".join(json.dumps(event) for event in events))
    pathlib.Path(option("--profile")).write_bytes(gzip.compress(
        json.dumps({"traceEvents": [], "diagnostic": diagnostic}).encode()))
    execution_log = option("--execution_log_json_file")
    if execution_log and scenario.get("execution_scenario") != "missing":
        remote_hit = base.name == "remote" and not scenario.get("zero_hits")
        records = []
        for index, label in enumerate(("//common:shared", "@//common:shared",
                                       "@@//tools/bazel:tool", "@@rules_cc+//cc:compiler", "")):
            records.append({"targetLabel": label, "mnemonic": "CppCompile",
                "runner": "remote cache hit" if remote_hit else "processwrapper-sandbox",
                "cacheHit": remote_hit, "commandArgs": [secret, "unclassified-command-canary"],
                "environmentVariables": [{"name": "PRIVATE", "value": "unclassified-env-canary"}],
                "inputs": [{"path": "unclassified-input-canary"}],
                "actualOutputs": [{"path": f"out/{index}.o" if index in (0, 3) else f"out/{index}.a"}],
                "digest": {"hash": "abc", "sizeBytes": "3", "untrusted": secret}})
        if scenario.get("execution_scenario") == "mismatch":
            records.pop()
        text = "".join(json.dumps(record, indent=2) for record in records)
        if scenario.get("execution_scenario") == "truncated":
            text += '{"targetLabel":"unclassified-truncated-canary'
        pathlib.Path(execution_log).write_text(text)
    for name in ("dist/libswsscommon_pkg.tar", "dist/libswsscommon_pkg.debug_symbols.tar",
                 "dist/sonic-db-cli_pkg.tar", "pyext/swsscommon_pkg.tar.gz"):
        path = root / "bazel-bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = name.encode()
        if (scenario.get("cross_cache_mismatch") and base.name in ("populate", "remote")
                and name in ("dist/libswsscommon_pkg.tar", "dist/sonic-db-cli_pkg.tar")):
            content += b":independent-cache"
        path.write_bytes(content)
sys.exit(9 if scenario.get("fail") == stem else 0)
'''


class BenchmarkTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "tools/bazel").mkdir(parents=True)
        (self.root / ".bazelversion").write_text("8.5.1\n")
        executable = self.root / "fake-bazel"
        executable.write_text(f"#!{sys.executable}\n" + FAKE)
        executable.chmod(0o700)
        self.args = argparse.Namespace(mode="compare", arch="AMD64",
            output_dir=self.root / "artifacts", repository_cache=self.root / "repos",
            disk_cache=None, remote_instance_name=None, execution_log=False,
            bazel=str(executable), remote_cache="grpcs://remote.buildbuddy.io",
            timeout_seconds=10)

    def run_fixture(self, fail=None, zero_hits=False, credentials=None, cross_cache_mismatch=False,
                    execution_scenario=None):
        credentials = credentials or {}
        (self.root / "scenario.json").write_text(json.dumps({"fail": fail,
            "zero_hits": zero_hits, "cross_cache_mismatch": cross_cache_mismatch,
            "execution_scenario": execution_scenario,
            "credential_names": list(credentials),
            "credential_values": list(credentials.values())}))
        environment = {"PATH": os.defpath, "BENCHMARK_FIXTURE_VISIBLE": "ordinary-value", **credentials}
        if self.args.mode != "baseline":
            environment["BUILDBUDDY_API_KEY"] = FIXTURE_MARKER
        original_execute = benchmark.execute
        def execute(command, *args):
            result = original_execute(command, *args)
            result["wall_seconds"] = 100 if "fetch" in command else 2
            return result
        captured = io.StringIO()
        with mock.patch.object(benchmark, "__file__", str(self.root / "tools/bazel/cache_benchmark.py")), \
             mock.patch.object(benchmark.subprocess, "check_output", return_value="revision\n"), \
             mock.patch.object(benchmark, "execute", side_effect=execute), \
             mock.patch.dict(os.environ, environment, clear=True), redirect_stdout(captured):
            code = benchmark.run(self.args, FIXTURE_MARKER if self.args.mode != "baseline" else "")
        self.assertNotIn(FIXTURE_MARKER, captured.getvalue())
        for value in credentials.values():
            self.assertNotIn(value, captured.getvalue())
        summary = json.loads((self.args.output_dir / "summary.json").read_text())
        calls_file = self.root / "calls.jsonl"
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        return code, summary, calls

    def assert_private_evidence(self, calls):
        for path in self.args.output_dir.iterdir():
            self.assertNotIn(FIXTURE_MARKER.encode(), path.read_bytes(), str(path))
            self.assertNotIn(json.dumps(FIXTURE_MARKER)[1:-1].encode(), path.read_bytes(), str(path))
        for call in calls:
            self.assertFalse(call["secret_in_argv"] or call["secret_in_env"] or call["key_env_present"])
            self.assertFalse(Path(call["base"]).exists())
            if call["rc"]:
                self.assertEqual(call["rc_mode"], 0o600)
                self.assertTrue(call["rc_valid"])
                self.assertFalse(Path(call["rc"]).exists())
                self.assertNotIn(str(self.args.output_dir), call["rc"])

    def test_compare_isolation_timing_credentials_and_hashes(self):
        code, summary, calls = self.run_fixture()
        self.assertEqual((code, summary["status"], len(calls)), (0, "complete", 8))
        self.assertEqual([c["fresh"] for c in calls], [True, False] * 4)
        self.assertEqual(len({c["base"] for c in calls}), 4)
        for first, second in zip(calls[::2], calls[1::2]):
            self.assertEqual(first["base"], second["base"])
            self.assertEqual(first["remaining_cases"], [])
        for call in calls[2:]:
            case = Path(call["base"]).name
            for flag in ("--disk_cache=", "--remote_executor=", "--remote_download_outputs=all", "--nocache_test_results"):
                self.assertIn(flag, call["argv"])
            self.assertIn("--remote_accept_cached=" + str(case == "remote").lower(), call["argv"])
            self.assertIn("--remote_upload_local_results=" + str(case == "populate").lower(), call["argv"])
        self.assertEqual([case["wall_seconds"] for case in summary["cases"]], [4, 4, 4])
        self.assertEqual(summary["comparison"]["remote_cache_hits"], 10)
        for case in summary["cases"]:
            for call in case["invocations"]:
                hashes = call["package_sha256"]
                self.assertEqual(len(hashes), 4)
                self.assertTrue(all(value == hashlib.sha256(name.encode()).hexdigest() for name, value in hashes.items()))
        self.assert_private_evidence(calls)

    def test_populate_failure_keeps_sanitized_evidence_and_stops(self):
        code, summary, calls = self.run_fixture("populate-yang")
        self.assertEqual((code, summary["status"], len(calls)), (1, "build_failed", 5))
        self.assertEqual([case["name"] for case in summary["cases"]], ["baseline", "populate"])
        self.assertIsNone(summary["comparison"])
        self.assertIn("[REDACTED]", (self.args.output_dir / "populate-yang.log").read_text())
        self.assert_private_evidence(calls)

    def test_baseline_filters_inherited_credentials_and_scrubs_every_artifact(self):
        self.args.mode = "baseline"
        names = ("BAZELISK_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                 "fixture_password", "FIXTURE_SECRET", "FIXTURE_API_KEY", "FIXTURE_ACCESS_KEY",
                 "FIXTURE_PRIVATE_KEY", "FIXTURE_CREDENTIALS")
        credentials = {name: f'dummy-{name.lower()}-"\\-private' for name in names}
        code, summary, calls = self.run_fixture(credentials=credentials)
        self.assertEqual((code, summary["status"], len(calls)), (0, "complete", 4))
        self.assertIsNone(summary["remote_cache"])
        for call in calls:
            self.assertEqual(call["credential_names_present"], [])
            self.assertEqual(call["credential_values_present"], [])
            self.assertEqual(call["ordinary_env"], "ordinary-value")
            self.assertFalse(call["key_env_present"])
            self.assertIsNone(call["rc"])
        for path in self.args.output_dir.iterdir():
            for value in credentials.values():
                self.assertNotIn(value.encode(), path.read_bytes(), str(path))
                self.assertNotIn(json.dumps(value)[1:-1].encode(), path.read_bytes(), str(path))
        for suffix in (".log", ".bep.jsonl", ".profile.json", ".MODULE.bazel.lock"):
            self.assertIn("[REDACTED]", (self.args.output_dir / ("baseline-yang" + suffix)).read_text())
        bep = (self.args.output_dir / "baseline-yang.bep.jsonl").read_text()
        for value in ("ordinary-value", "split-client-env-canary", "client_env"):
            self.assertNotIn(value, bep)
        self.assertIn("--keep_going", bep)
        self.assertIn('"jobs"', bep)
        self.assert_private_evidence(calls)

    def test_zero_remote_hits_cannot_claim_successful_comparison(self):
        code, summary, calls = self.run_fixture(zero_hits=True)
        self.assertEqual((code, summary["status"], len(calls)), (1, "remote_cache_not_verified", 8))
        self.assertIsNone(summary["comparison"])
        self.assert_private_evidence(calls)

    def test_restored_disk_cache_is_copied_independently_and_never_modified(self):
        self.args.disk_cache = self.root / "restored-cache"
        self.args.disk_cache.mkdir()
        (self.args.disk_cache / "seed").write_bytes(b"original cache entry")
        code, summary, calls = self.run_fixture()
        self.assertEqual((code, summary["status"], len(calls)), (0, "complete", 10))
        disk_calls = [call for call in calls if call["disk"]]
        self.assertEqual(len(disk_calls), 4)
        self.assertEqual(len({call["disk"] for call in disk_calls}), 2)
        self.assertEqual([call["disk_entries"] for call in disk_calls[::2]], [["seed"], ["seed"]])
        for call in disk_calls:
            self.assertFalse(Path(call["disk"]).exists())
            self.assertNotEqual(Path(call["disk"]), self.args.disk_cache)
        self.assertEqual(list(self.args.disk_cache.iterdir()), [self.args.disk_cache / "seed"])
        self.assertEqual((self.args.disk_cache / "seed").read_bytes(), b"original cache entry")
        self.assertEqual([case["wall_seconds"] for case in summary["cases"]], [4, 4, 4, 4])
        self.assertEqual(summary["comparison"]["remote_case"], "combined")
        self.assertEqual(summary["disk_cache_snapshot"], {"files": 1, "bytes": len(b"original cache entry")})
        self.assertFalse(summary["remote_reuse_observed"])
        self.assertEqual(summary["comparison"]["remote_cache_hits"], 0)
        self.assertEqual(summary["comparison"]["remote_only_cache_hits"], 10)
        self.assertIsNone(summary["comparison"]["speedup"])
        self.assertEqual(summary["comparison"]["saved_seconds"], 0)
        self.assert_private_evidence(calls)

    def test_missing_executable_records_preparation_failure(self):
        self.args.bazel = str(self.root / "missing")
        code, summary, calls = self.run_fixture()
        self.assertEqual((code, summary["status"], calls), (1, "preparation_failed", []))
        self.assertEqual(summary["preparation"][0]["exit_code"], 127)
        self.assertEqual(summary["cases"], [])

    def test_cross_cache_mismatch_preserves_primary_comparison_and_failure(self):
        self.args.disk_cache = self.root / "restored-cache"
        self.args.disk_cache.mkdir()
        code, summary, calls = self.run_fixture(cross_cache_mismatch=True)
        self.assertEqual((code, summary["status"], len(calls)), (1, "package_outputs_differ", 10))
        self.assertTrue(summary["primary_output_match"])
        self.assertTrue(summary["remote_roundtrip_match"])
        self.assertFalse(summary["cross_cache_output_match"])
        self.assertFalse(summary["package_hashes_match"])
        self.assertCountEqual(summary["package_mismatches"], [
            {"check": "cross_cache", "reference_case": "baseline", "candidate_case": "populate",
             "yang": yang, "package": package,
             "reference_sha256": hashlib.sha256(package.encode()).hexdigest(),
             "candidate_sha256": hashlib.sha256(package.encode() + b":independent-cache").hexdigest()}
            for yang in (True, False)
            for package in ("dist/libswsscommon_pkg.tar", "dist/sonic-db-cli_pkg.tar")])
        comparison = summary["comparison"]
        self.assertEqual((comparison["baseline_case"], comparison["remote_case"]), ("baseline", "combined"))
        self.assertEqual((comparison["baseline_seconds"], comparison["remote_seconds"]), (4, 4))
        self.assertEqual(comparison["saved_seconds"], 0)
        self.assertEqual(comparison["remote_cache_hits"], 0)
        self.assertEqual(comparison["remote_only_cache_hits"], 10)
        self.assertIsNone(comparison["speedup"])
        self.assert_private_evidence(calls)

    def test_cli_rejects_missing_compare_credential_before_run(self):
        argv = ["benchmark", "--mode", "compare", "--arch", "AMD64", "--output-dir", str(self.args.output_dir)]
        with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, {"BUILDBUDDY_API_KEY": ""}), \
             mock.patch.object(benchmark, "run") as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                benchmark.main()
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_metrics_distinguish_incomplete_zero_and_remote_hits(self):
        path = self.root / "events.jsonl"
        self.assertIsNone(benchmark.bep_metrics(path)["remote_cache_hits"])
        finished = {"finished": {"exitCode": {}}}
        metric = lambda runners: {"buildMetrics": {"actionSummary": {"runnerCount": runners}}}
        cases = [([finished], None), ([metric([])], None), ([finished, metric([{"name": "total"}])], 0),
                 ([finished, metric([{"name": name, "count": count} for name, count in
                    [("total", 100), ("internal", 80), ("action cache hit", 60), ("disk cache hit", 9), ("remote cache hit", "7")]])], 7)]
        for events, expected in cases:
            with self.subTest(expected=expected):
                path.write_text("\n".join(json.dumps(event) for event in events))
                result = benchmark.bep_metrics(path)
                self.assertEqual(result["remote_cache_hits"], expected)
                self.assertEqual(result["complete"], expected is not None)
        path.write_text(path.read_text() + "\n{truncated")
        self.assertFalse(benchmark.bep_metrics(path)["complete"])

    def test_remote_diagnostic_reuses_namespace_and_publishes_only_safe_metadata(self):
        self.args.mode = "remote"
        self.args.arch = "ARM64"
        self.args.remote_instance_name = "sonic-swss-common/cache-pilot/arm64/seed"
        code, summary, calls = self.run_fixture()
        self.assertEqual((code, summary["status"], len(calls)), (0, "complete", 4))
        self.assertEqual([Path(call["base"]).name for call in calls],
                         ["preparation", "preparation", "remote", "remote"])
        self.assertEqual([call["fresh"] for call in calls], [True, False, True, False])
        self.assertEqual(summary["remote_instance_name"], self.args.remote_instance_name)
        self.assertEqual(summary["purpose"], "cache_action_attribution")
        self.assertIsNone(summary["comparison"])
        self.assertTrue(summary["remote_reuse_observed"])
        for call in calls[2:]:
            for flag in ("--config=aarch64", "--disk_cache=", "--remote_accept_cached=true",
                         "--remote_upload_local_results=false", "--remote_executor=",
                         "--remote_download_outputs=all", "--execution_log_sort=false",
                         "--remote_instance_name=" + self.args.remote_instance_name):
                self.assertIn(flag, call["argv"])
            execution_path = next(a.split("=", 1)[1] for a in call["argv"]
                                  if a.startswith("--execution_log_json_file="))
            self.assertNotIn(str(self.args.output_dir), execution_path)
            self.assertFalse(Path(execution_path).exists())
        for invocation in summary["cases"][0]["invocations"]:
            metrics = invocation["action_attribution"]
            self.assertTrue(metrics["complete"])
            self.assertEqual(metrics["remote_cache_hits"], 5)
            self.assertEqual(metrics["buckets"], {"root": 3, "dependencies": 1, "unknown": 1})
            self.assertEqual(metrics["by_repository"]["rules_cc+"]["remote_cache_hits"], 1)
            self.assertEqual(metrics["by_repository"]["<unknown>"]["remote_cache_hits"], 1)
            self.assertEqual(metrics["remote_cached_object_output_count"], 2)
            self.assertEqual(metrics["remote_cached_spawns_with_object_outputs"], 2)
            metadata = (self.args.output_dir / invocation["artifacts"]["actions"]).read_text()
            for omitted in ("commandArgs", "environmentVariables", "inputs", "unclassified-", "untrusted"):
                self.assertNotIn(omitted, metadata)
            records = [json.loads(line) for line in metadata.splitlines()]
            self.assertEqual(len(records), 5)
            self.assertEqual(records[0]["digest"], {"hash": "abc", "sizeBytes": "3"})
        self.assert_private_evidence(calls)

    def test_execution_log_missing_truncated_and_mismatched_counts_fail_closed(self):
        self.args.mode = "remote"
        self.args.remote_instance_name = "seed"
        for scenario in ("missing", "truncated", "mismatch"):
            with self.subTest(scenario=scenario):
                self.args.output_dir = self.root / ("artifacts-" + scenario)
                calls_file = self.root / "calls.jsonl"
                calls_file.unlink(missing_ok=True)
                code, summary, calls = self.run_fixture(execution_scenario=scenario)
                self.assertEqual((code, summary["status"], len(calls)),
                                 (1, "execution_log_incomplete", 3))
                invocation = summary["cases"][0]["invocations"][0]
                self.assertFalse(invocation["action_attribution"]["complete"])
                self.assertNotIn("actions", invocation["artifacts"])
                self.assertEqual(list(self.args.output_dir.glob("*.actions.jsonl")), [])
                self.assertIsNone(summary["comparison"])
                self.assert_private_evidence(calls)

    def test_remote_diagnostic_requires_observed_hits(self):
        self.args.mode = "remote"
        self.args.remote_instance_name = "seed"
        code, summary, calls = self.run_fixture(zero_hits=True)
        self.assertEqual((code, summary["status"], len(calls)),
                         (1, "remote_cache_not_verified", 4))
        self.assertFalse(summary["remote_reuse_observed"])
        self.assertIsNone(summary["comparison"])

    def test_optional_execution_log_preserves_compare_mode(self):
        self.args.execution_log = True
        code, summary, calls = self.run_fixture()
        self.assertEqual((code, summary["status"], len(calls)), (0, "complete", 8))
        self.assertEqual([case["name"] for case in summary["cases"]],
                         ["baseline", "populate", "remote"])
        self.assertEqual(summary["comparison"]["remote_cache_hits"], 10)
        self.assertTrue(all(invocation["action_attribution"]["complete"]
                            for case in summary["cases"] for invocation in case["invocations"]))
        self.assert_private_evidence(calls)

    def test_execution_parser_streams_pretty_concatenated_json_and_rejects_bad_records(self):
        records = [{"targetLabel": "//:a", "value": "brace } escaped \\\" snowman \u2603"},
                   {"targetLabel": "@@rules_cc+//:b", "nested": {"list": [1, 2]}}]
        text = " \n" + "".join(json.dumps(record, indent=2) for record in records) + "\n"
        self.assertEqual(list(benchmark.execution_records(io.StringIO(text), chunk_size=7)), records)
        for invalid in ('{"runner":', '{bad}', '{} trailing', '[]', '42'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                list(benchmark.execution_records(io.StringIO(invalid), chunk_size=3))

    def test_owner_classification_does_not_attribute_external_tools_to_root(self):
        for label in ("//:x", "@//tools/bazel:compiler", "@@//pkg:x"):
            self.assertEqual(benchmark.owning_repository(label), ("sonic-swss-common", "root"))
        for label, repository in (("@@bazel_tools//tools/cpp:tool", "bazel_tools"),
                                  ("@rules_cc+//cc:tool", "rules_cc+"),
                                  ("@@sonic-build-infra++sysroots+sysroot//:lib", "sonic-build-infra++sysroots+sysroot")):
            self.assertEqual(benchmark.owning_repository(label), (repository, "dependencies"))
        for label in ("", "not-a-label", "@bad", "//without-target"):
            self.assertEqual(benchmark.owning_repository(label), ("<unknown>", "unknown"))

    def test_cli_keeps_remote_instance_inputs_out_of_other_modes(self):
        for options in (("--mode", "remote"),
                        ("--mode", "compare", "--remote-instance-name", "seed"),
                        ("--mode", "baseline", "--remote-instance-name", "seed"),
                        ("--mode", "remote", "--remote-instance-name", "seed", "--disk-cache", str(self.root))):
            argv = ["benchmark", *options, "--arch", "AMD64", "--output-dir", str(self.args.output_dir)]
            with self.subTest(options=options), mock.patch.object(sys, "argv", argv), \
                 mock.patch.dict(os.environ, {"BUILDBUDDY_API_KEY": "dummy-test-key"}), \
                 mock.patch.object(benchmark, "run") as run, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    benchmark.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()


if __name__ == "__main__":
    main()
