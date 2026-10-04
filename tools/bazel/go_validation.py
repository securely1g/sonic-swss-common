#!/usr/bin/env python3
"""Record maintained registry inputs and verify resolved Go build evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REPO = "https://github.com/securely1g/sonic-bazel-registry.git"
MAIN = "https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main"
VERSION = "0.64.1-sonic.1"
CGO_SHA256 = "5f7e9f6788a1ba8d57aa4752212d0fad390657b3cb3d40c2962886ae8fe28bd9"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def record(artifacts):
    configuration = (ROOT / ".bazelrc").read_text()
    endpoints = [line for line in configuration.splitlines()
                 if "--registry=" in line and "securely1g/sonic-bazel-registry/" in line]
    if endpoints != ["common --registry=" + MAIN]:
        raise ValueError("Expected exactly the canonical SONiC main registry endpoint")
    revision = subprocess.check_output(
        ["git", "-c", "safe.directory=" + str(ROOT), "ls-remote", REGISTRY_REPO,
         "refs/heads/main"], cwd=ROOT, text=True).split()
    if len(revision) != 2 or revision[1] != "refs/heads/main":
        raise ValueError("Could not determine the maintained registry revision")
    (artifacts / "effective.bazelrc").write_text(configuration)
    write_json(artifacts / "registry-inputs.json", {
        "registry": MAIN, "observed_revision": revision[0],
        "module": "rules_go@" + VERSION,
    })


def collect(artifacts, config, output_base_file=None):
    errors = []
    receipts = {}
    try:
        bazel = ["bazel"]
        if output_base_file:
            bazel.append("--output_base=" + output_base_file.read_text().strip())
        commands = {
            "bazel-version.txt": [*bazel, "version"],
            "module-graph.txt": [*bazel, "mod", "graph", "--lockfile_mode=update"],
            "rules-go-repository.txt": [*bazel, "mod", "show_repo", "@io_bazel_rules_go"],
            "go-version.txt": [*bazel, "run", *config, "@io_bazel_rules_go//go", "--", "version"],
            "output-base.txt": [*bazel, "info", "--announce_rc", "output_base"],
        }
        for name, command in commands.items():
            with (artifacts / name).open("w") as output:
                result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            receipts[name] = result.returncode
            if result.returncode:
                errors.append(name + " failed")
        output_lines = (artifacts / "output-base.txt").read_text().splitlines()
        registry_options = re.findall(r"--registry=(\S+)", "\n".join(output_lines))
        sonic_options = [url for url in registry_options if "securely1g/sonic-bazel-registry/" in url]
        if sonic_options != [MAIN]:
            errors.append("The effective Bazel options do not select exactly the maintained main registry endpoint")
        bases = [Path(line) for line in output_lines if line.startswith("/") and Path(line).is_dir()]
        if not bases:
            raise ValueError("Bazel output base was not recorded")
        repository = bases[-1] / "external/rules_go+"
        module = (repository / "MODULE.bazel").read_text()
        cgo = (repository / "go/private/rules/cgo.bzl").read_bytes()
        actual_hash = hashlib.sha256(cgo).hexdigest()
        (artifacts / "rules-go-MODULE.bazel").write_text(module)
        (artifacts / "rules-go-cgo.bzl").write_bytes(cgo)
        receipts["cgo_sha256"] = actual_hash
        if 'version = "' + VERSION + '"' not in module or actual_hash != CGO_SHA256:
            errors.append("The resolved rules_go version or cgo repair differs from the reviewed entry")
        if "Build label: 8.5.1" not in (artifacts / "bazel-version.txt").read_text().splitlines():
            errors.append("Bazel is not 8.5.1")
        goarch = "arm64" if config else "amd64"
        if "go version go1.25.0 linux/" + goarch not in (artifacts / "go-version.txt").read_text().splitlines():
            errors.append("The Go SDK is not the expected native 1.25.0")
        if output_base_file:
            with (artifacts / "bazel-shutdown.txt").open("w") as output:
                subprocess.run([*bazel, "shutdown"], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    except Exception as error:
        errors.append(str(error))
        raise
    finally:
        shutil.copyfile(ROOT / ".bazelrc", artifacts / "final.bazelrc")
        if (ROOT / "MODULE.bazel.lock").is_file():
            shutil.copyfile(ROOT / "MODULE.bazel.lock", artifacts / "MODULE.bazel.lock")
        write_json(artifacts / "go-input-checks.json", {"receipts": receipts, "errors": errors})
        with (artifacts / "tracked-files.diff").open("w") as output:
            subprocess.run(["git", "-c", "safe.directory=" + str(ROOT), "diff", "--exit-code"],
                           cwd=ROOT, stdout=output, check=True)
    if errors:
        raise ValueError("; ".join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["record", "collect"])
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--config", choices=["aarch64"])
    parser.add_argument("--output-base-file", type=Path)
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    if args.action == "record":
        record(artifacts)
    else:
        collect(artifacts, ["--config=aarch64"] if args.config else [], args.output_base_file)


if __name__ == "__main__":
    main()
