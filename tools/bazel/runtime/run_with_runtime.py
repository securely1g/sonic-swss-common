#!/usr/bin/python3
"""Run a test with the selected, declared Trixie runtime dependencies."""

import os
from pathlib import Path, PurePosixPath
import posixpath
import signal
import subprocess
import sys
import tarfile


def runtime_member_filter(member, destination):
    # Debian packages describe links relative to the installed filesystem.
    # Relocate absolute links into this staged root before applying Python's
    # confinement checks, so they cannot resolve against the execution host.
    if member.linkname.startswith("/") and (member.issym() or member.islnk()):
        target = posixpath.normpath("/" + member.linkname.lstrip("/")).lstrip("/")
        if member.issym():
            parent = PurePosixPath(member.name.lstrip("/")).parent
            target = posixpath.relpath(target, str(parent))
        member = member.replace(linkname=target)
    return tarfile.data_filter(member, destination)


def main():
    archive, expected_library, yang_mode, *command = sys.argv[1:]
    if not command:
        raise ValueError("A test command is required")
    if yang_mode not in {"enabled", "disabled"}:
        raise ValueError(f"Unknown YANG mode: {yang_mode}")

    runtime_root = Path(os.environ["TEST_TMPDIR"]) / "swsscommon-runtime"
    runtime_root.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(runtime_root, filter=runtime_member_filter)

    runtime_root = runtime_root.resolve()
    library_dirs = set()
    for library in runtime_root.rglob("lib*.so*"):
        if not library.is_file():
            continue
        if not library.resolve().is_relative_to(runtime_root):
            raise AssertionError(f"Runtime library escapes the staged root: {library}")
        library_dirs.add(str(library.parent.resolve()))
    if not library_dirs:
        raise AssertionError("The staged runtime contains no shared libraries")

    environment = dict(os.environ)
    environment.update(
        LD_LIBRARY_PATH=":".join(sorted(library_dirs)),
        SWSS_RUNTIME_ROOT=str(runtime_root),
        SWSS_YANG_MODE=yang_mode,
    )
    if yang_mode == "enabled":
        # Bazel's source-built libyang introduces DT_RPATH entries, which take
        # precedence over LD_LIBRARY_PATH. Load the exact extracted SONAME so
        # the Go consumer exercises the deployed payload, keeping its loaded
        # path assertion rather than falling back to a build-tree library.
        libyang = {path.resolve() for path in runtime_root.rglob("libyang.so.3")}
        if len(libyang) != 1:
            raise AssertionError(f"Expected one staged libyang, found {sorted(libyang)}")
        environment["LD_PRELOAD"] = str(libyang.pop())
    if expected_library != "-":
        environment["SWSS_EXPECTED_LIBRARY"] = str(Path(expected_library).resolve())

    result = subprocess.run(command, env=environment)
    if result.returncode < 0:
        child_signal = -result.returncode
        if child_signal not in {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(child_signal, signal.SIG_DFL)
        os.kill(os.getpid(), child_signal)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
