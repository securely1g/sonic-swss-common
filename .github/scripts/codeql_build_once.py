#!/usr/bin/env python3
"""Build under CodeQL once per configuration, then test the resulting outputs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


# Keep the original CodeQL targets, including compile-only legacy tests.
ANALYSIS_TARGETS = [
    "//:libswsscommon", "//:swssloglevel", "//sonic-db-cli:sonic-db-cli",
    "//pyext:_swsscommon", "//tests:codeql_test_sources",
    "//tests:shared_library_runtime_test",
]
BUILD_TARGETS = [
    "//:libswsscommon", "//:libswsscommon_shared",
    "//:libswsscommon_consolidated.so", "//:swssloglevel",
    "//crates/swss-common:bindings_dir", "//dist:libswsscommon_pkg",
    "//dist:libswsscommon_pkg.debug_symbols", "//dist:sonic-db-cli_pkg",
    "//pyext:swsscommon_pkg", "//goext:swsscommon",
]
TEST_TARGETS = [
    "//goext:swsscommon_runtime_test", "//tests:status_code_util_test",
    "//tests:saiaclschema_ut", "//tests:notification_queue_ut",
    "//tests:interface_ut", "//tests:vrf_ut",
    "//tests:shared_library_runtime_test", "//dist:libswsscommon_package_test",
    "//pyext:swsscommon_package_test",
]
PACKAGES = [
    "dist/libswsscommon_pkg.tar", "dist/libswsscommon_pkg.debug_symbols.tar",
    "dist/sonic-db-cli_pkg.tar", "pyext/swsscommon_pkg.tar.gz",
]


def read_spawns(path):
    """Bazel's JSON execution log is a sequence of JSON objects."""
    remaining = path.read_text().lstrip()
    decoder = json.JSONDecoder()
    while remaining:
        value, end = decoder.raw_decode(remaining)
        yield value
        remaining = remaining[end:].lstrip()


def verify_reuse(execution_log, events, expected_tests):
    spawns = list(read_spawns(execution_log))
    # Local action-cache hits produce no spawn records. A recorded cacheHit is
    # an external (disk/remote) hit and is not permitted in this experiment.
    unexpected = [s for s in spawns if s.get("cacheHit", False)
                  or s.get("mnemonic") != "TestRunner"]
    if unexpected:
        raise RuntimeError("Test phase rebuilt outputs: " + ", ".join(
            s.get("mnemonic", "unknown") for s in unexpected))
    executed_tests = set()
    for spawn in spawns:
        if spawn.get("exitCode", 0) != 0 or spawn.get("status", ""):
            raise RuntimeError("Unsuccessful test spawn in execution log")
        label = spawn.get("targetLabel", "")
        if label.startswith(("@@//", "@//")):
            label = label[label.index("//"):]
        executed_tests.add(label)
    missing_spawns = set(expected_tests) - executed_tests
    if missing_spawns:
        raise RuntimeError("Required tests have no execution log entry: " +
                           ", ".join(sorted(missing_spawns)))
    summaries = {}
    attempts = set()
    for line in events.read_text().splitlines():
        event = json.loads(line)
        if "testSummary" in event:
            summaries[event["id"]["testSummary"]["label"]] = event["testSummary"]
        if "testResult" in event:
            result = event["testResult"]
            if (result.get("status") == "PASSED"
                    and not result.get("cachedLocally", False)
                    and not result.get("executionInfo", {}).get("cachedRemotely", False)):
                attempts.add(event["id"]["testResult"]["label"])
    for label in expected_tests:
        result = summaries.get(label, {})
        if (result.get("overallStatus") != "PASSED" or label not in attempts
                or result.get("totalRunCount", 0) < 1
                or result.get("totalNumCached", 0) != 0):
            raise RuntimeError(f"Required test did not execute and pass: {label}")
    return {"executed_non_test_spawns": 0, "passed_tests": sorted(expected_tests)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["yang", "no-yang"])
    args = parser.parse_args()
    traced = args.mode == "yang"
    artifacts = Path("artifacts").resolve()
    evidence = artifacts / "codeql"
    evidence.mkdir(parents=True, exist_ok=True)
    # Never accept an existing output base. Only this traced invocation may
    # populate its action cache; downloaded/object caches cannot supply outputs.
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if traced:
        output_base = tempfile.mkdtemp(prefix="codeql-bazel-", dir=os.environ["RUNNER_TEMP"])
        receipt = {"revision": revision, "output_base": output_base, "configurations": {}}
    else:
        # The workflow restores the tracing environment after analysis.
        # Start a new Bazel server only after that boundary. The YANG source
        # archive/database has already been finalized and bundled at this point.
        if any(os.environ.get(key) for key in (
                "CODEQL_RUNNER", "CODEQL_TRACER_CONFIGURATION", "ODASA_TRACER_CONFIGURATION",
                "SEMMLE_PRELOAD_libtrace", "SEMMLE_PRELOAD_libtrace32",
                "SEMMLE_PRELOAD_libtrace64")) or "libtrace" in os.environ.get("LD_PRELOAD", ""):
            raise RuntimeError("CodeQL tracing is still active for the no-YANG phase")
        receipt = json.loads((evidence / "build-once.json").read_text())
        if receipt["revision"] != revision or "yang" not in receipt["configurations"]:
            raise RuntimeError("Missing traced build receipt for this revision")
        output_base = receipt["output_base"]
    bazel = ["bazel", f"--output_base={output_base}"]
    flags = [
        "--lockfile_mode=update", "--spawn_strategy=local", "--use_action_cache",
        "--noremote_accept_cached", "--noremote_upload_local_results",
        "--disk_cache=", "--remote_cache=", "--remote_executor=",
        "--cxxopt=-nostdinc", "--jobs=4",
    ]

    def run(command, phase, extra=()):
        start = time.monotonic()
        with (evidence / f"{phase}.log").open("w") as log:
            process = subprocess.Popen(command + list(extra), stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
            if process.wait():
                raise RuntimeError(f"{phase} failed: exit {process.returncode}")
        return round(time.monotonic() - start, 3)

    try:
        if traced:
            run(bazel + ["run"] + flags, "format",
                ["//tools/bazel/buildifier:buildifier.format.check"])
        for yang in (traced,):
            mode = "yang" if yang else "no-yang"
            mode_flags = flags + [f"--//tools/bazel:yang_modules={yang}"]
            tests = TEST_TARGETS + (["//tests:defaultvalueprovider_ut"] if yang else [])
            targets = BUILD_TARGETS + tests
            if yang:
                targets += ANALYSIS_TARGETS + ["//common:cfg_schema_generated"]
            targets = list(dict.fromkeys(targets))
            result = {"targets": targets, "flags": mode_flags, "codeql_traced": traced}
            result["build_seconds"] = run(bazel + ["build"] + mode_flags,
                f"{mode}-build", [
                    f"--profile={evidence / (mode + '-build.profile.gz')}",
                    # Compact logs avoid materializing/sorting the full input
                    # inventory in memory after this large traced build.
                    f"--execution_log_compact_file={evidence / (mode + '-build-spawns.pb.zst')}",
                ] + targets)
            # Keep the server, output base, compiler options and targets. All
            # native compilation and package actions already ran in this mode.
            execution_log = evidence / f"{mode}-test-spawns.json"
            events = evidence / f"{mode}-test-events.json"
            result["test_seconds"] = run(bazel + ["test"] + mode_flags,
                f"{mode}-test", [
                    "--nocache_test_results", "--test_output=errors",
                    f"--execution_log_json_file={execution_log}",
                    f"--build_event_json_file={events}",
                ] + targets)
            result.update(verify_reuse(execution_log, events, tests))
            # The successful build/test selects this configuration's output
            # link, as in the native workflow. `bazel info` can fail resolving
            # the apparent platform repository without analyzing targets.
            binary_dir = Path("bazel-bin").resolve(strict=True)
            if not binary_dir.is_relative_to(Path(output_base).resolve()):
                raise RuntimeError("bazel-bin does not belong to this private build")
            package_dir = artifacts / mode
            package_dir.mkdir(exist_ok=True)
            result["packages"] = {}
            for name in PACKAGES:
                source = binary_dir / name
                destination = package_dir / source.name
                shutil.copyfile(source, destination)
                result["packages"][destination.name] = hashlib.sha256(
                    destination.read_bytes()).hexdigest()
            receipt["configurations"][mode] = result
            (evidence / "build-once.json").write_text(json.dumps(receipt, indent=2) + "\n")
        shutil.copyfile("MODULE.bazel.lock", evidence / "MODULE.bazel.lock")
    finally:
        subprocess.run(bazel + ["shutdown"], check=False)


if __name__ == "__main__":
    main()
