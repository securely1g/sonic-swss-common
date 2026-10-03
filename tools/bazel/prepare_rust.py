#!/usr/bin/env python3
"""Run the pinned shared Rust metadata preparation before Bazel resolution."""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request


# This bootstrap pin is separate from the Bazel module version: preparation runs
# before Bazel can resolve the module graph. Keep the source and hash together.
PREPARATION_REVISION = "ebe63820a323412eb93733dc8a0e9854e2b1d8fc"
PREPARATION_SHA256 = "8bedc0cc85e56fe548100b5246f8f52d5c198a60ed8c54f37c06e3421692fbd3"
PREPARATION_URL = (
    "https://raw.githubusercontent.com/securely1g/sonic-build-infra/"
    f"{PREPARATION_REVISION}/tools/rust/prepare.py"
)


def main():
    with urllib.request.urlopen(PREPARATION_URL, timeout=60) as response:
        source = response.read()
    if hashlib.sha256(source).hexdigest() != PREPARATION_SHA256:
        raise SystemExit("Shared Rust preparation checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="sonic-rust-preparation-") as temporary:
        helper = Path(temporary) / "prepare.py"
        helper.write_bytes(source)
        return subprocess.call([
            sys.executable,
            str(helper),
            "--workspace", str(Path(__file__).resolve().parents[2]),
            "--repository", "crates",
            *sys.argv[1:],
        ])


if __name__ == "__main__":
    sys.exit(main())
