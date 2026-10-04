#!/usr/bin/env python3
"""Explicitly select one temporary registry branch while a Draft PR is reviewed."""

from pathlib import Path
import re
import sys


prefix = "https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/"
if len(sys.argv) != 2 or not sys.argv[1].startswith(prefix):
    raise SystemExit("Pass the SONiC registry's maintained branch URL")
registry = sys.argv[1]
revision = registry[len(prefix):].rstrip("/")
if not re.fullmatch(r"[A-Za-z0-9._/-]+", revision) or re.fullmatch(r"[0-9a-fA-F]{40}", revision):
    raise SystemExit("CI requires a registry branch, not a commit snapshot")

config = Path(".bazelrc")
lines = config.read_text().splitlines()
matches = [
    i for i, line in enumerate(lines)
    if line.startswith("common --registry=" + prefix)
]
if len(matches) != 1:
    raise SystemExit("Expected exactly one SONiC registry entry in .bazelrc")
lines[matches[0]] = "common --registry=" + registry
config.write_text("\n".join(lines) + "\n")
print("CI SONiC registry: " + registry)
