#!/usr/bin/env python3
"""Seed Common's image package, then require read-only reuse through buildimage."""

import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time

import credentials
import managed
import policy
import validate


ROOT = Path(__file__).resolve().parents[3]
TARGETS = [
    "@sonic_swss_common//dist:libswsscommon_pkg",
    "@sonic_swss_common//dist:libswsscommon_pkg.debug_symbols",
]
MAKE_PACKAGES = TARGETS + [
    "@sonic_swss_common//dist:sonic-db-cli_pkg",
    "@sonic_swss_common//dist:sonic-db-cli_pkg.debug_symbols",
    "@sonic_swss_common//pyext:swsscommon_pkg",
    "@sonic_swss_common//pyext:swsscommon_pkg.debug_symbols",
]
MAKE_TARGETS = MAKE_PACKAGES + ["//:image_common_execution_inputs"]
COMMON_LABEL = re.compile(r"@@sonic-swss-common\+[^/]*//")
MACHINES = {"amd64": ("x86_64", "x86_64_trixie", 62),
            "arm64": ("aarch64", "aarch64_trixie", 183)}
SEED_BRANCH = "refs/heads/codex/buildimage-managed-cache-seed-192"
SEED_INPUTS = (
    ".bazelrc", ".bazelversion", "MODULE.bazel", "tools/bazel/common.bazelrc",
    "src/sonic-supervisord-utilities-rs/deps.MODULE.bazel",
    "src/sonic-supervisord-utilities-rs/bazel/release.bzl",
)
SHARED_OVERRIDES = {"rules_cc", "rules_go", "tar.bzl", "libnl3", "re2",
                    "sonic-build-infra", "sonic-swss-common", "sonic-rust-deps"}


def require_seed_allowed():
    if not managed.enabled():
        policy.require_upload_allowed()
        return
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "securely1g/sonic-swss-common",
        "GITHUB_REF": SEED_BRANCH,
        "GITHUB_WORKFLOW_REF": "securely1g/sonic-swss-common/.github/workflows/bazel-remote-cache.yml@" + SEED_BRANCH,
    }
    if any(os.environ.get(key) != value for key, value in required.items()):
        raise ValueError("Managed seed requires the dedicated manual Common workflow")
    managed.selection()


def module_contract(path):
    result = {"dependencies": {}, "overrides": {}, "toolchains": [], "language_toolchains": []}
    for node in ast.parse(path.read_text()).body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        name = call.func.id if isinstance(call.func, ast.Name) else None
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
            name = call.func.value.id + "." + call.func.attr
        if name in ("bazel_dep", "single_version_override"):
            values = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
            key = values["name" if name == "bazel_dep" else "module_name"]
            field = "dependencies" if name == "bazel_dep" else "overrides"
            if key in result[field] or not isinstance(values.get("version"), str):
                raise ValueError("Invalid seed module version declaration")
            result[field][key] = values["version"]
        elif name == "register_toolchains":
            result["toolchains"] += [value for value in map(ast.literal_eval, call.args)
                                     if isinstance(value, str) and value.startswith("@")]
        elif name in ("python.toolchain", "rust.toolchain"):
            result["language_toolchains"].append(ast.dump(call, include_attributes=False))
    result["toolchains"].sort()
    return result


def release_contract(path):
    selected = {}
    names = {"_LTO", "_CODEGEN_UNITS", "_release_impl", "_release"}
    for node in ast.parse(path.read_text()).body:
        name = node.name if isinstance(node, ast.FunctionDef) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name in names:
            selected[name] = ast.dump(node, include_attributes=False)
    if set(selected) != names:
        raise ValueError("Missing release transition contract")
    return selected


def verify_consumer_contract(buildimage, fixture):
    seed = module_contract(fixture / "MODULE.bazel")
    image = module_contract(buildimage / "MODULE.bazel")
    for name, version in seed["dependencies"].items():
        actual = seed["overrides"].get(name, version)
        expected = image["overrides"].get(name, image["dependencies"].get(name))
        if actual != expected:
            raise ValueError("Seed consumer dependency version differs: " + name)
    expected_overrides = {name: value for name, value in image["overrides"].items() if name in SHARED_OVERRIDES}
    if seed["overrides"] != expected_overrides:
        raise ValueError("Seed consumer dependency overrides differ from buildimage")
    if seed["toolchains"] != image["toolchains"] or seed["language_toolchains"] != image["language_toolchains"]:
        raise ValueError("Seed consumer toolchain declarations differ from buildimage")
    if release_contract(fixture / "image_common_inputs.bzl") != release_contract(
            buildimage / "src/sonic-supervisord-utilities-rs/bazel/release.bzl"):
        raise ValueError("Seed release transition differs from buildimage")
    if any(not (buildimage / name).is_file() or (buildimage / name).is_symlink() for name in SEED_INPUTS):
        raise ValueError("Seed configuration inputs must be ordinary files")
    return {name: hashlib.sha256((buildimage / name).read_bytes()).hexdigest() for name in SEED_INPUTS}


def graph_modules(graph):
    referenced, modules = set(), {}
    def visit(node):
        key = node["key"]
        referenced.add(key)
        if not node.get("unexpanded", False):
            modules.setdefault(key, set()).update(item["key"] for item in node.get("dependencies", []))
        for field in ("dependencies", "indirectDependencies", "cycles"):
            for child in node.get(field, []):
                visit(child)
    visit(graph)
    if graph.get("key") != "<root>" or referenced != set(modules):
        raise ValueError("Incomplete seed module resolution")
    return {key: sorted(value) for key, value in modules.items() if key != "<root>"}


class ResolutionMismatch(ValueError):
    def __init__(self, evidence):
        self.evidence = evidence
        changed = [item["seed_module"] for item in evidence["differences"]]
        super().__init__("Seed module resolution differs from buildimage: " + ", ".join(changed[:20]))


def compare_resolution(seed_graph, image_graph):
    seed, image = graph_modules(seed_graph), graph_modules(image_graph)
    changed = sorted(key for key, dependencies in seed.items() if image.get(key) != dependencies)
    common = [key for key in seed if key.startswith("sonic-swss-common@")]
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    evidence = {
        "matched": bool(seed) and len(common) == 1 and not changed,
        "seed_module_count": len(seed), "buildimage_module_count": len(image),
        "shared_graph_sha256": hashlib.sha256(encoded).hexdigest(), "matched_modules": sorted(seed),
        "common_modules": common,
        "differences": [{
            "seed_module": key, "seed_dependencies": seed[key],
            "buildimage_candidates": {candidate: dependencies for candidate, dependencies in image.items()
                                      if candidate.rsplit("@", 1)[0] == key.rsplit("@", 1)[0]},
        } for key in changed],
    }
    if not evidence["matched"]:
        raise ResolutionMismatch(evidence)
    return evidence


def resolved_graph(workspace, common_source, temporary, name, buildimage):
    output_base = temporary / (name + "-graph-output")
    command = [managed.BAZEL, "--batch", "--nosystem_rc", "--nohome_rc", "--output_base=" + str(output_base),
               "mod", "graph", "--output=json", "--extension_info=hidden", "--lockfile_mode=off",
               "--registry=https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main",
               "--registry=https://bcr.bazel.build", "--override_module=sonic-swss-common=" + str(common_source),
               managed.downloader_option(), "--color=no", "--curses=no"]
    result = subprocess.run(command, cwd=workspace, text=True, capture_output=True, timeout=600)
    (temporary / (name + "-graph.json")).write_text(result.stdout)
    (temporary / (name + "-graph.log")).write_text(result.stderr)
    source = buildimage / "tools/bazel/ci/resolution.py"
    spec = importlib.util.spec_from_file_location("seed_resolution", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.inspect_graph(result.stdout, result.stderr, result.returncode)
    except (ValueError, KeyError, json.JSONDecodeError):
        validate.print_safe_diagnostics(result.stderr)
        raise ValueError("Seed module graph inspection failed") from None
    return json.loads(result.stdout)


def run(argv, cwd, log):
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        validate.print_safe_diagnostics(result.stdout + result.stderr)
        raise ValueError("Cross-repository command failed; see the redacted CI diagnostics")
    return result.stdout


def summarize_actions(path):
    """Export action identity and bounded diagnostics, never raw commands."""
    def fingerprint(value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    actions = []
    counts = Counter()
    for record in validate.records(path):
        mnemonic = record.get("mnemonic", "")
        counts[mnemonic, record.get("runner", ""), record.get("cacheHit", False)] += 1
        label = record.get("targetLabel", "")
        if mnemonic not in ("CppCompile", "CppLink") or not COMMON_LABEL.match(label):
            continue
        digest = record.get("digest", {}).get("hash", "")
        if record.get("exitCode", 0) or record.get("status"):
            raise ValueError("A Common compile/link action failed")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("A Common action has no SHA256 cache digest")
        outputs = sorted(record.get("listedOutputs", []))
        if not outputs:
            raise ValueError("A Common action has no declared output")
        # Read only the recorded action environment, never the client's process
        # environment. Other variables expose names only, including auth names.
        environment = {item["name"]: item.get("value", "")
                       for item in record.get("environmentVariables", [])
                       if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item.get("name", ""))}
        inputs = record.get("inputs")
        diagnostics = {
            "environment": {name: environment[name] for name in ("PATH", "LD_LIBRARY_PATH", "PWD")
                            if name in environment},
            "environment_names": sorted(environment),
            "command_sha256": fingerprint(record["commandArgs"]) if "commandArgs" in record else None,
            # Input order is immaterial; preserve every metadata field in the
            # fingerprint so changed file contents, modes or paths remain visible.
            "inputs_sha256": fingerprint(sorted(inputs, key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":")))) if inputs is not None else None,
            "input_count": len(inputs) if inputs is not None else None,
        }
        actions.append({"mnemonic": mnemonic, "target": label, "outputs": outputs,
                        "digest": digest, "runner": record.get("runner", ""),
                        "cache_hit": record.get("cacheHit", False), "diagnostics": diagnostics})
    return {"common_actions": sorted(actions, key=lambda row: (row["target"], row["outputs"])),
            "action_counts": [{"mnemonic": key[0], "runner": key[1], "cache_hit": key[2],
                               "count": count} for key, count in sorted(counts.items())]}


def check_actions(summary, *, remote):
    actions = summary["common_actions"]
    if {row["mnemonic"] for row in actions} != {"CppCompile", "CppLink"}:
        raise ValueError("Expected both Common compilation and linking evidence")
    if not any(row["mnemonic"] == "CppLink" and row["target"].endswith("//:libswsscommon_consolidated.so") for row in actions):
        raise ValueError("Missing the libswsscommon.so.0 link action")
    for row in actions:
        if remote:
            if row["runner"] != "remote cache hit" or not row["cache_hit"]:
                raise ValueError("Common readback rebuilt an action: " + row["target"] +
                                 " " + row["outputs"][0])
        elif row["cache_hit"] or row["runner"] != "linux-sandbox":
            raise ValueError("Common seed must execute every compile/link in linux-sandbox")


def compare_phases(seed, read):
    check_actions(seed, remote=False)
    check_actions(read, remote=True)
    identity = lambda row: (row["mnemonic"], row["target"], tuple(row["outputs"]), row["digest"])
    if Counter(map(identity, seed["common_actions"])) != Counter(map(identity, read["common_actions"])):
        raise ValueError("Common seed and buildimage use different compile/link actions")
    if seed["artifacts"] != read["artifacts"]:
        raise ValueError("Common seed and buildimage produced different runtime/debug packages")


def export_checkout(source, destination, revision):
    """Use tracked source only; never copy a producer's output or disk cache."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Source export requires a full commit revision")
    destination.mkdir()
    archive = destination.parent / (destination.name + ".source.tar")
    with archive.open("wb") as stream:
        subprocess.run(["git", "-C", str(source), "archive", revision], stdout=stream, check=True)
    with tarfile.open(archive) as source_tar:
        source_tar.extractall(destination, filter="data")
    archive.unlink()


def package_receipt(path, architecture, *, debug):
    data = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    if not debug:
        multiarch = "x86_64-linux-gnu" if architecture == "amd64" else "aarch64-linux-gnu"
        name = "usr/lib/" + multiarch + "/libswsscommon.so.0.0.0"
        with tarfile.open(path) as archive:
            matches = [member for member in archive if member.name.removeprefix("./") == name and member.isfile()]
            if len(matches) != 1:
                raise ValueError("Runtime TAR must contain exactly one native Common library")
            with archive.extractfile(matches[0]) as stream:
                contents = stream.read()
        if len(contents) < 20 or contents[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", contents, 18)[0] != MACHINES[architecture][2]:
            raise ValueError("Common library is not the expected native ELF architecture")
        data["library_sha256"] = hashlib.sha256(contents).hexdigest()
    return data


def build_phase(name, workspace, common_source, temporary, architecture, *, targets=None, queries=None):
    targets = TARGETS if targets is None else targets
    queries = TARGETS if queries is None else queries
    seed = name == "seed"
    if seed:
        require_seed_allowed()
    output_base = temporary / (name + "-output")
    if output_base.exists() or (workspace / "MODULE.bazel.lock").exists():
        raise ValueError("Both phases require a fresh workspace, output base and generated lock")
    prefix = [managed.BAZEL if managed.enabled() else "bazel", "--batch", "--nosystem_rc", "--nohome_rc", "--output_base=" + str(output_base)]
    target_platform = "@sonic_build_infra//platforms:" + MACHINES[architecture][1]
    flags = [
        "--registry=https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main",
        "--registry=https://bcr.bazel.build",
        "--platforms=" + target_platform, "--host_platform=" + target_platform,
        "--override_module=sonic-swss-common=" + str(common_source),
        "--@sonic_swss_common//tools/bazel:yang_modules=True",
        "--@rules_rust//rust/settings:experimental_use_allocator_libraries_with_mangled_symbols=True",
        "--noincompatible_disallow_empty_glob", "--check_direct_dependencies=off",
        "--incompatible_strict_action_env",
        "--spawn_strategy=linux-sandbox", "--strategy=Genrule=linux-sandbox",
        "--noincompatible_legacy_local_fallback", "--jobs=4",
        "--local_resources=cpu=4", "--local_resources=memory=10000",
        "--lockfile_mode=update", "--disk_cache=", "--remote_executor=",
        *([] if managed.enabled() else ["--remote_cache=" + credentials.ENDPOINT + "/"]),
        *(["--credential_helper=" + credentials.HOST + "=" + str(ROOT / "tools/bazel/cache/credentials.py")]
          if seed and not managed.enabled() else []),
        "--remote_download_outputs=all", "--remote_cache_async=false",
        "--remote_upload_local_results=" + str(seed).lower(),
        "--remote_accept_cached=" + str(not seed).lower(),
        # Sorting the full image-input log exhausted the native runner's heap
        # after a successful build. Sort only the small sanitized inventory.
        "--execution_log_sort=false",
        "--color=no", "--curses=no", "--noshow_progress",
    ]
    if managed.enabled():
        flags.append(managed.downloader_option())
        managed.check_options(flags)
    log = temporary / (name + "-actions.json")
    started = time.monotonic()
    print("Cross-repository cache proof: " + name + " " + architecture, flush=True)
    run([*prefix, "build", *flags, "--execution_log_json_file=" + str(log), *targets],
        workspace, temporary / (name + "-build.log"))
    summary = summarize_actions(log)
    # Save the evidence even if readback reveals a mismatch.
    summary.update({"phase": name, "fresh_workspace": True, "fresh_output_base": True,
                    "disk_cache": False, "remote_reads": not seed, "remote_writes": seed,
                    "yang": True, "artifacts": {}, "elapsed_seconds": round(time.monotonic() - started, 3)})
    for index, target in enumerate(queries):
        files = run([*prefix, "cquery", *flags, "--output=files", target], workspace,
                    temporary / (name + "-query-" + str(index) + ".log")).splitlines()
        if len(files) != 1:
            raise ValueError("Expected one package output for " + target)
        artifact = (workspace / files[0]).resolve()
        if not artifact.is_file() or not artifact.is_relative_to(output_base):
            raise ValueError("Package did not materialize in its fresh output base")
        # Only the library runtime TAR contains libswsscommon.so.0.0.0. Hash all
        # other runtime/debug packages without applying that library check.
        summary["artifacts"][target] = package_receipt(artifact, architecture, debug=target != TARGETS[0])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buildimage", type=Path, required=True)
    parser.add_argument("--buildimage-revision", required=True)
    parser.add_argument("--architecture", choices=MACHINES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-for-make", action="store_true",
                        help="Seed Common image inputs for a later Make/sonic-slave readback; skip direct readback")
    args = parser.parse_args()
    receipt = {"passed": False, "architecture": args.architecture, "yang": True,
               "targets": MAKE_TARGETS if args.seed_for_make else TARGETS,
               "buildimage_revision": args.buildimage_revision, "phases": []}
    if args.seed_for_make:
        receipt["mode"] = "make_seed"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        require_seed_allowed()
        if managed.enabled():
            if not args.seed_for_make:
                raise ValueError("Managed diagnostic supports only Make seed mode")
            receipt.update(managed.selection())
        if platform.machine() != MACHINES[args.architecture][0]:
            raise ValueError("This proof requires a native architecture runner")
        if not managed.enabled():
            credentials.authorization()
        if not re.fullmatch(r"[0-9a-f]{40}", args.buildimage_revision):
            raise ValueError("Pin buildimage to a full commit SHA")
        actual = subprocess.check_output(["git", "-C", str(args.buildimage), "rev-parse", "HEAD"], text=True).strip()
        if actual != args.buildimage_revision:
            raise ValueError("Buildimage checkout does not match the reviewed revision")
        receipt["common_revision"] = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        with tempfile.TemporaryDirectory(prefix="common-buildimage-cache-", dir=os.environ.get("RUNNER_TEMP")) as directory:
            temporary = Path(directory)
            for name in (("seed",) if args.seed_for_make else ("seed", "read")):
                # Context cleanup also handles read-only extracted toolchains.
                # No seed workspace or action cache survives into readback.
                with tempfile.TemporaryDirectory(prefix=name + "-", dir=temporary) as phase_directory:
                    phase_root = Path(phase_directory)
                    workspace = phase_root / "workspace"
                    common_source = phase_root / "common"
                    export_checkout(ROOT, common_source, receipt["common_revision"])
                    if name == "seed":
                        fixture = common_source / "tools/bazel/cache/consumer"
                        shutil.copytree(fixture, workspace)
                        shutil.copyfile(common_source / ".bazelversion", workspace / ".bazelversion")
                    else:
                        export_checkout(args.buildimage, workspace, args.buildimage_revision)
                    if managed.enabled() and name == "seed":
                        graph_root = phase_root / "resolution"
                        graph_root.mkdir()
                        image_workspace = graph_root / "buildimage"
                        export_checkout(args.buildimage, image_workspace, args.buildimage_revision)
                        receipt["buildimage_inputs"] = verify_consumer_contract(image_workspace, fixture)
                        seed_graph = resolved_graph(workspace, common_source, graph_root, "seed", image_workspace)
                        image_graph = resolved_graph(image_workspace, common_source, graph_root, "buildimage", image_workspace)
                        try:
                            receipt["module_resolution"] = compare_resolution(seed_graph, image_graph)
                        except ResolutionMismatch as error:
                            receipt["module_resolution"] = error.evidence
                            raise
                    options = {"targets": MAKE_TARGETS, "queries": MAKE_PACKAGES} if args.seed_for_make else {}
                    phase = build_phase(name, workspace, common_source, phase_root, args.architecture, **options)
                    receipt["phases"].append(phase)
                    shutil.copyfile(workspace / "MODULE.bazel.lock", args.output.parent / (name + "-MODULE.bazel.lock"))
                    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            if args.seed_for_make:
                check_actions(receipt["phases"][0], remote=False)
            else:
                compare_phases(*receipt["phases"])
            receipt["passed"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        receipt["error"] = str(error)
        raise
    finally:
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    message = ("Seeded Common image inputs for a later Make cache proof: " if args.seed_for_make else
               "Verified Common seed -> read-only buildimage cache reuse: ")
    print(message + str(args.output))


if __name__ == "__main__":
    main()
