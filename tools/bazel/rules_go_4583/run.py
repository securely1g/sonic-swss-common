#!/usr/bin/env python3
"""Run the original #4583 fixture against the unmodified BCR release."""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


REGISTRY_COMMIT = "3fc93604f17c358630171af3094f375b6c88ed3b"
PATCH_NAME = "0003_add_execroot_relative_includes_to_cgo_actions_pr_4583.patch"
PATCH_URL = (
    "https://github.com/securely1g/sonic-bazel-registry/blob/"
    + REGISTRY_COMMIT
    + "/modules/rules_go/0.60.0.sonic-patched/patches/"
    + PATCH_NAME
)
PATCH_SHA256 = "e63b32e838307fdcc0ccff46fc134c0fecc0f404d7a96c3c85a08987d717a9b8"
ORIGINAL_SHA256 = "38fcad078f0cc587e0c45a0338d11d50e3b8e70bba5cfdd1693ed13b00bb03c8"
CGO_SHA256 = "361023a9ac9509963e4a761b609b105f7d95848c8052230ccbbd8a7f505c04d1"

MODULE = '''module(name = "rules_go_4583_regression")
bazel_dep(name = "rules_go", version = "0.64.1", repo_name = "io_bazel_rules_go")
go_sdk = use_extension("@io_bazel_rules_go//go:extensions.bzl", "go_sdk")
go_sdk.download(version = "1.25.0")
'''
BUILD = '''load("@io_bazel_rules_go//go/tools/bazel_testing:def.bzl", "go_bazel_test")
go_bazel_test(
    name = "cc_header_inputs_test",
    srcs = ["cc_header_inputs_test.go"],
    timeout = "long",
)
'''


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_command(args, cwd, env, log):
    result = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    log.write_bytes(result.stdout)
    return result.returncode, result.stdout.decode(errors="replace")


def snapshot_fixture(workspace, destination):
    """Keep fixture inputs, not Bazel output symlinks or the wrapped SDK."""
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("BUILD.bazel", "MODULE.bazel", "MODULE.bazel.lock", ".bazelrc",
                 "use_greeting.go", "greeting_repo/BUILD.bazel",
                 "greeting_repo/WORKSPACE", "greeting_repo/greeting.c",
                 "greeting_repo/include/greeting.h"):
        source = workspace / name
        if source.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def nested_bazel(args):
    """Record the harness's nested invocation without changing its fixture."""
    env = dict(os.environ, USE_BAZEL_VERSION="8.5.1")
    real_bazel = env["RULES_GO_4583_REAL_BAZEL"]
    if "build" not in args:
        return subprocess.call([real_bazel, *args], env=env)

    evidence = Path(env["RULES_GO_4583_CASE_ARTIFACTS"])
    evidence.mkdir(parents=True, exist_ok=True)
    workspace = Path.cwd()
    effective_args = [
        *args,
        "--spawn_strategy=processwrapper-sandbox",
        "--lockfile_mode=update",
        "--announce_rc",
        "--build_event_json_file=" + str(evidence / "nested-events.json"),
        "--execution_log_compact_file=" + str(evidence / "nested-actions.binpb.zst"),
    ]
    write_json(evidence / "nested-command.json", {
        "original_args": args, "effective_args": effective_args,
        "working_directory": str(workspace), "real_bazel": real_bazel,
        "USE_BAZEL_VERSION": env["USE_BAZEL_VERSION"],
    })
    version_rc, version = run_command([real_bazel, "--version"], workspace, env,
                                      evidence / "nested-bazel-version.txt")
    if version_rc or version.strip() != "bazel 8.5.1":
        print("Nested Bazel version is not exactly 8.5.1", file=sys.stderr)
        return 2

    snapshot_fixture(workspace, evidence / "fixture-before")
    tested_repo = workspace.parent / "tested_repo"
    tested_cgo = tested_repo / "go/private/rules/cgo.bzl"
    cgo_bytes = tested_cgo.read_bytes()
    (evidence / "tested-cgo.bzl").write_bytes(cgo_bytes)
    shutil.copyfile(tested_repo / "MODULE.bazel", evidence / "tested-rules-go-MODULE.bazel")
    write_json(evidence / "tested-repository-hashes.json", {
        str(path.relative_to(tested_repo)): sha256(path.read_bytes())
        for path in sorted(tested_repo.rglob("*")) if path.is_file()
    })
    if sha256(cgo_bytes) != CGO_SHA256:
        print("The nested rules_go cgo implementation differs from v0.64.1", file=sys.stderr)
        return 2

    build_rc, output = run_command([real_bazel, *effective_args], workspace, env,
                                   evidence / "nested-build.log")
    sys.stdout.write(output)
    snapshot_fixture(workspace, evidence / "fixture-after")
    metadata = {}
    commands = {
        "nested-rules-go-repository.txt": ["mod", "show_repo", "@io_bazel_rules_go"],
        "nested-rules-cc-repository.txt": ["mod", "show_repo", "@rules_cc"],
        "nested-go-version.txt": ["run", "@io_bazel_rules_go//go", "--", "version"],
        "nested-output-base.txt": ["info", "output_base"],
    }
    for name, command in commands.items():
        metadata[name], output = run_command([real_bazel, "--nohome_rc", *command],
                                             workspace, env, evidence / name)
        if name == "nested-output-base.txt" and metadata[name] == 0:
            # Optional producer evidence; absence does not change the test result.
            try:
                bases = [Path(line.strip()) for line in output.splitlines()
                         if line.startswith("/") and Path(line.strip()).is_dir()]
                if bases:
                    for proxy in (bases[-1] / "external").glob("*cc_compatibility_proxy*/proxy.bzl"):
                        destination = evidence / "cc-compatibility-proxies" / proxy.parent.name
                        destination.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(proxy, destination / "proxy.bzl")
            except OSError as error:
                (evidence / "optional-proxy-copy-error.txt").write_text(str(error) + "\n")
    snapshot_fixture(workspace, evidence / "fixture-after")
    write_json(evidence / "nested-result.json", {
        "build_exit_code": build_rc,
        "metadata_exit_codes": metadata,
        "tested_cgo_sha256": sha256(cgo_bytes),
    })
    return build_rc


def adapt_original(original):
    old = b'\t\tWorkspaceSuffix: `\nlocal_repository('
    new = (b'\t\tModuleFileSuffix: `\n'
           b'local_repository = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")\n'
           b'local_repository(')
    if original.count(old) != 1:
        raise ValueError("Original test no longer has the expected harness suffix")
    return original.replace(old, new, 1)


def verify_case(evidence, expected_goarch):
    errors = []
    receipt = evidence / "nested-result.json"
    if not receipt.is_file():
        return ["nested build receipt missing"]
    result = json.loads(receipt.read_text())
    if result["metadata_exit_codes"].get("nested-go-version.txt") != 0:
        errors.append("nested SDK version command failed")
    sdk = (evidence / "nested-go-version.txt").read_text()
    if "go version go1.25.0 linux/" + expected_goarch not in sdk.splitlines():
        errors.append("nested SDK/architecture receipt does not match Go 1.25.0")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    arguments = parser.parse_args()
    artifacts = arguments.artifacts
    if not artifacts.is_absolute():
        parser.error("--artifacts must be an absolute path")
    artifacts.mkdir(parents=True, exist_ok=True)
    source_dir = Path(__file__).resolve().parent
    original = (source_dir / "original_cc_header_inputs_test.go.txt").read_bytes()
    if sha256(original) != ORIGINAL_SHA256:
        raise ValueError("Original test source checksum differs from the registry patch")
    adapted = adapt_original(original)
    (artifacts / "original_cc_header_inputs_test.go").write_bytes(original)
    (artifacts / "adapted_cc_header_inputs_test.go").write_bytes(adapted)
    (artifacts / "test-only-adaptation.diff").write_text("".join(difflib.unified_diff(
        original.decode().splitlines(keepends=True), adapted.decode().splitlines(keepends=True),
        fromfile="original_cc_header_inputs_test.go", tofile="adapted_cc_header_inputs_test.go")))
    write_json(artifacts / "source-provenance.json", {
        "registry_commit": REGISTRY_COMMIT, "patch_url": PATCH_URL,
        "patch_sha256": PATCH_SHA256, "original_patch_commit": "a038f38af7a82a1e395b4f1e966b597ae104ea2f",
        "original_test_sha256": sha256(original), "adapted_test_sha256": sha256(adapted),
        "rules_go_version": "0.64.1", "upstream_cgo_sha256": CGO_SHA256,
        "driver_sha256": sha256(Path(__file__).read_bytes()),
        "nested_harness_rules_cc": "0.2.18", "prior_common_rules_cc": "0.2.16",
        "coverage": "Original build-only tests; no generated binary execution",
    })
    architecture = platform.machine()
    goarch = {"x86_64": "amd64", "aarch64": "arm64"}.get(architecture)
    if not goarch:
        raise ValueError("Only native Linux AMD64 and ARM64 are supported")
    real_bazel = str(Path(shutil.which("bazel")).resolve())
    env = dict(os.environ, USE_BAZEL_VERSION="8.5.1")
    write_json(artifacts / "environment.json", {
        "uname_machine": architecture, "platform": platform.platform(),
        "expected_goarch": goarch, "RUNNER_ARCH": env.get("RUNNER_ARCH"),
        "real_bazel": real_bazel, "USE_BAZEL_VERSION": "8.5.1",
        "system_bazelrc_exists": Path("/etc/bazel.bazelrc").exists(),
    })
    summary = {"cases": {}}
    with tempfile.TemporaryDirectory(prefix="rules-go-4583-") as temporary:
        workspace = Path(temporary) / "workspace"
        workspace.mkdir()
        (workspace / "MODULE.bazel").write_text(MODULE)
        (workspace / "BUILD.bazel").write_text(BUILD)
        test_source = workspace / "cc_header_inputs_test.go"
        test_source.write_bytes(original)
        wrapper_dir = Path(temporary) / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "bazel"
        wrapper.write_text("#!" + sys.executable + "\nimport os, sys\nos.execv(" +
                           repr(sys.executable) + ", [" + repr(sys.executable) + ", " +
                           repr(str(Path(__file__).resolve())) + ", '--nested-bazel', *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        env["PATH"] = str(wrapper_dir) + os.pathsep + env["PATH"]
        env["RULES_GO_4583_REAL_BAZEL"] = real_bazel
        bazel = [real_bazel, "--nosystem_rc", "--nohome_rc"]
        version_rc, version = run_command([real_bazel, "--version"], workspace, env,
                                          artifacts / "outer-bazel-version.txt")
        if version_rc or version.strip() != "bazel 8.5.1":
            raise ValueError("Outer Bazel version is not exactly 8.5.1")
        cases = [
            ("original", None),
            ("default", "^TestTransitiveCcHeaders$"),
            ("external-include-paths", "^TestTransitiveCcHeadersExternalIncludePaths$"),
        ]
        for name, test_filter in cases:
            evidence = artifacts / name
            evidence.mkdir()
            if test_filter:
                test_source.write_bytes(adapted)
            command = [
                *bazel, "test", "//:cc_header_inputs_test",
                "--lockfile_mode=update", "--nocache_test_results", "--test_output=all",
                "--test_arg=-test.v", "--test_env=PATH",
                "--test_env=USE_BAZEL_VERSION=8.5.1", "--test_env=GO_BAZEL_TEST_BAZELFLAGS=",
                "--test_env=RULES_GO_4583_REAL_BAZEL=" + real_bazel,
                "--test_env=RULES_GO_4583_CASE_ARTIFACTS=" + str(evidence),
                "--build_event_json_file=" + str(evidence / "outer-events.json"),
            ]
            if test_filter:
                command.append("--test_arg=-test.run=" + test_filter)
            write_json(evidence / "outer-command.json", command)
            print("Running " + name, flush=True)
            exit_code, output = run_command(command, workspace, env, evidence / "outer-test.log")
            result = {"outer_exit_code": exit_code}
            if name == "original":
                result["expected_harness_failure"] = exit_code != 0 and "unknown field WorkspaceSuffix" in output
            else:
                result["evidence_errors"] = verify_case(evidence, goarch)
            summary["cases"][name] = result
            print(json.dumps({name: result}), flush=True)
            for file_name in ("test.log", "test.xml"):
                source = workspace / "bazel-testlogs/cc_header_inputs_test" / file_name
                if source.is_file():
                    shutil.copyfile(source, evidence / file_name)

        for name, command in {
            "outer-rules-go-repository.txt": ["mod", "show_repo", "@io_bazel_rules_go"],
            "outer-go-version.txt": ["run", "@io_bazel_rules_go//go", "--", "version"],
        }.items():
            exit_code, _ = run_command([*bazel, *command], workspace, env, artifacts / name)
            summary.setdefault("metadata_exit_codes", {})[name] = exit_code
        for name in ("MODULE.bazel", "MODULE.bazel.lock", "BUILD.bazel"):
            if (workspace / name).is_file():
                shutil.copyfile(workspace / name, artifacts / name)
        run_command([*bazel, "shutdown"], workspace, env, artifacts / "outer-shutdown.log")

    summary["passed"] = (
        summary["cases"]["original"]["expected_harness_failure"]
        and all(result["outer_exit_code"] == 0 and not result["evidence_errors"]
                for name, result in summary["cases"].items() if name != "original")
    )
    write_json(artifacts / "summary.json", summary)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--nested-bazel":
        sys.exit(nested_bazel(sys.argv[2:]))
    sys.exit(main())
