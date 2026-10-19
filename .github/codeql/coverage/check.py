#!/usr/bin/env python3
"""Require actual CodeQL extractions for Common sources and its SWIG wrapper.

This is a source-extraction guard, not proof of equivalent compile options,
query results, or generated-source reporting in SARIF.
"""

import argparse
import csv
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
GENERATED_WRAPPER = "pyext/py3/swsscommon_wrap.cpp"
GENERATED_WRAPPER_PATTERN = re.compile(
    r"/bazel-out/[^/]+/bin/" + re.escape(GENERATED_WRAPPER) + r"$"
)


def tracked_sources(repository):
    """The YANG-enabled CodeQL target set covers every tracked C/C++ source.

    There are deliberately no exclusions. A newly tracked source must join the
    build graph before this check can pass; BUILD globs alone can hide omissions.
    """
    paths = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=repository, text=True
    ).split("\0")
    return {path for path in paths if PurePosixPath(path).suffix in SOURCE_SUFFIXES}


def check_rows(expected, rows, source_root):
    if not expected:
        raise ValueError("No tracked C/C++ sources found")
    source_root = str(PurePosixPath(source_root))
    if not PurePosixPath(source_root).is_absolute():
        raise ValueError("The CodeQL source root must be an absolute path")
    prefix = source_root.rstrip("/") + "/"
    observed = set()
    abnormal = set()
    generated = set()
    generated_abnormal = set()
    all_normal = set()
    all_abnormal = set()
    for row in rows:
        path = row["path"]
        termination = row["termination"]
        if not PurePosixPath(path).is_absolute():
            raise ValueError(f"CodeQL reported a non-absolute source path: {path}")
        if termination not in {"normal", "abnormal"}:
            raise ValueError(f"Unexpected compilation termination: {termination}")
        (all_normal if termination == "normal" else all_abnormal).add(path)
        if path.startswith(prefix):
            relative = path[len(prefix):]
            if relative in expected:
                (observed if termination == "normal" else abnormal).add(relative)
        if GENERATED_WRAPPER_PATTERN.search(path):
            (generated if termination == "normal" else generated_abnormal).add(path)

    missing = expected - observed
    failures = sorted(abnormal | generated_abnormal)
    return {
        "source_root": source_root,
        "expected_tracked_sources": sorted(expected),
        "observed_tracked_sources": sorted(observed),
        "missing_tracked_sources": sorted(missing),
        "abnormal_selected_sources": failures,
        "generated_wrapper": {
            "required_suffix": GENERATED_WRAPPER,
            "observed_paths": sorted(generated),
            "paths_under_source_root": sorted(
                path for path in generated if path.startswith(prefix)
            ),
        },
        "all_normal_primary_source_count": len(all_normal),
        "all_abnormal_primary_sources": sorted(all_abnormal),
        "passed": not missing and not failures and bool(generated),
        "scope": (
            "Primary C/C++ extraction only. Compiler configurations, dependency "
            "coverage, security-query parity, and generated-source SARIF reporting "
            "need separate validation."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--source-root", help="CodeQL source root; defaults to repository")
    args = parser.parse_args()
    with args.csv.open(newline="") as stream:
        rows = csv.DictReader(stream)
        if rows.fieldnames != ["path", "termination"]:
            raise ValueError(f"Unexpected CodeQL CSV columns: {rows.fieldnames}")
        receipt = check_rows(
            tracked_sources(args.repository), rows,
            args.source_root or str(args.repository.resolve()),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    if not receipt["passed"]:
        print(
            "CodeQL extraction coverage failed: "
            f"missing={receipt['missing_tracked_sources']}, "
            f"abnormal={receipt['abnormal_selected_sources']}, "
            f"generated wrapper={receipt['generated_wrapper']['observed_paths']}",
            file=sys.stderr,
        )
        return 1
    print(
        f"CodeQL extracted all {len(receipt['expected_tracked_sources'])} tracked "
        "C/C++ sources and the generated SWIG wrapper."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
