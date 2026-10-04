#!/usr/bin/env bash
set -euo pipefail

# Inspect archive headers directly so this check also works for cross builds.
python3 - "$1" "$2" <<'PY'
import re
import sys
import tarfile

with tarfile.open(sys.argv[1]) as archive:
    members = archive.getmembers()
    libraries = [member for member in members if re.fullmatch(
        r"usr/lib/(?:x86_64|aarch64)-linux-gnu/libswsscommon\.so(?:\.0(?:\.0\.0)?)?",
        member.name,
    )]
    required = [archive.getmember(name) for name in (
        "usr/bin/swssloglevel",
        "var/run/redis/sonic-db/database_config.json",
    )]
    if len(libraries) != 3:
        raise SystemExit(f"Expected three library entries, found {len(libraries)}")
    required.extend(libraries)

with tarfile.open(sys.argv[2]) as archive:
    required.append(archive.getmember("usr/bin/sonic-db-cli"))

for member in required:
    expected = 0 if member.issym() else 1672560000
    if member.mtime != expected:
        raise SystemExit(
            f"{member.name}: expected fixed timestamp {expected}, found {member.mtime}"
        )
print("Explicit runtime package timestamps are deterministic")
PY
