#!/usr/bin/env python3
"""Execute the built Common/CLI libraries from their installed container paths."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from prepare import require_container, require_no_installed_common, sha256


CHECK_LOADED = r"""
import ctypes, json
from pathlib import Path
library = ctypes.CDLL('libsonicdbcli.so.0')
loaded = {}
for line in Path('/proc/self/maps').read_text().splitlines():
    fields = line.split(maxsplit=5)
    if len(fields) != 6 or not fields[5].startswith('/'):
        continue
    path = Path(fields[5]).resolve()
    for name in ('libswsscommon.so.', 'libsonicdbcli.so.'):
        if path.name.startswith(name):
            loaded.setdefault(name, set()).add(str(path))
assert all(len(loaded.get(name, ())) == 1 for name in ('libswsscommon.so.', 'libsonicdbcli.so.')), loaded
print(json.dumps({name: paths.pop() for name, paths in loaded.items()}, sort_keys=True))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-package", type=Path, required=True)
    parser.add_argument("--cli-package", type=Path, required=True)
    parser.add_argument("--libyang-package", type=Path)
    parser.add_argument("--common-probe", type=Path, required=True)
    parser.add_argument("--cli-probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require_container()
    require_no_installed_common()
    runtime_id = os.environ["SONIC_TEST_RUNTIME_ID"]
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    multiarch = {"amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}[architecture]
    expected = {name: f"usr/lib/{multiarch}/{name}0.0.0"
                for name in ("libswsscommon.so.", "libsonicdbcli.so.")}
    package_hashes = {}
    with tarfile.open(args.common_package) as archive:
        members = {member.name.removeprefix("./"): member for member in archive}
        for name, path in expected.items():
            data = archive.extractfile(members[path]).read()
            package_hashes[name] = hashlib.sha256(data).hexdigest()

    archives = [args.common_package, args.cli_package]
    if args.libyang_package:
        archives.insert(0, args.libyang_package)
    for archive in archives:
        # These source-built tars already passed the package layout tests.
        # This modifies only the disposable container, after raw Bazel tests.
        subprocess.run(["tar", "--extract", "--file", archive.resolve(), "--directory", "/"], check=True)
    subprocess.run(["ldconfig"], check=True)

    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("LD_") and not key.startswith("RUNFILES_")}
    with tempfile.TemporaryDirectory(prefix="sonic-installed-runtime-") as temporary:
        root = Path(temporary)
        loaded = json.loads(subprocess.check_output(
            ["/usr/bin/python3", "-I", "-c", CHECK_LOADED], cwd=root, env=environment, text=True))
        evidence = {}
        for name, relative in expected.items():
            path = Path("/" + relative).resolve()
            if loaded[name] != str(path) or sha256(path) != package_hashes[name]:
                raise AssertionError(f"Loaded {name} does not match the current runtime archive")
            notes = subprocess.check_output(["readelf", "--notes", path], text=True)
            build_id = re.search(r"Build ID: ([0-9a-f]+)", notes)
            if not build_id:
                raise AssertionError(f"No build ID in installed {name}")
            evidence[name] = {"loaded_path": str(path), "sha256": sha256(path), "build_id": build_id[1]}
        for name, original, needed in (
            ("common-probe", args.common_probe, "libswsscommon.so.0"),
            ("cli-probe", args.cli_probe, "libsonicdbcli.so.0"),
        ):
            probe = root / name
            shutil.copy2(original.resolve(), probe)
            dynamic = subprocess.check_output(["readelf", "--dynamic", probe], text=True)
            if f"Shared library: [{needed}]" not in dynamic:
                raise AssertionError(f"{name} must exercise the shared library")
            # Check the copied consumer's resolution too: an absolute build-tree
            # path must not silently bypass the installed-library check above.
            resolution = subprocess.check_output(["ldd", probe], cwd=root, env=environment, text=True)
            for library in expected:
                matches = re.findall(re.escape(library + "0") + r"\s+=>\s+(\S+)", resolution)
                required = library == "libswsscommon.so." or library + "0" == needed
                if required and len(matches) != 1:
                    raise AssertionError(f"{name} did not resolve its required {library}")
                if matches and Path(matches[0]).resolve() != Path(loaded[library]):
                    raise AssertionError(f"{name} resolves {library} outside the installed archive")
            subprocess.run([str(probe)], cwd=root, env=environment, check=True)

    receipt = {"generated": "Generated by tools/bazel/runtime/installed_check.py; do not edit manually",
               "architecture": architecture, "runtime_id": runtime_id,
               "archives": {archive.name: sha256(archive) for archive in archives},
               "libraries": evidence, "dynamic_consumers": "passed", "library_path_overrides": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print("Installed Common and CLI dynamic consumers passed")


if __name__ == "__main__":
    main()
