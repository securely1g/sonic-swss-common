#!/usr/bin/python3
"""Run a test with the selected, declared Trixie runtime dependencies."""

import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
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
    arguments = sys.argv[1:]
    qemu_arm = arguments[:1] == ["--qemu-arm"]
    if qemu_arm:
        arguments = arguments[1:]
    archive, expected_library, yang_mode, *command = arguments
    if not command:
        raise ValueError("A test command is required")
    if yang_mode not in {"enabled", "disabled"}:
        raise ValueError(f"Unknown YANG mode: {yang_mode}")

    runtime_root = Path(os.environ["TEST_TMPDIR"]) / "swsscommon-runtime"
    runtime_root.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(runtime_root, filter=runtime_member_filter)

    runtime_root = runtime_root.resolve()
    if qemu_arm:
        # Debian Trixie is usrmerged; base-files normally supplies these aliases.
        for directory in ["bin", "lib", "sbin"]:
            alias = runtime_root / directory
            if not alias.exists() and not alias.is_symlink():
                alias.symlink_to("usr/" + directory)

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
    if expected_library != "-":
        environment["SWSS_EXPECTED_LIBRARY"] = str(Path(expected_library).resolve())

    if qemu_arm:
        header = subprocess.run(
            ["readelf", "--file-header", command[0]],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
        fields = {}
        for line in header.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key.strip()] = value.strip()
        if (
            fields.get("Class") != "ELF32"
            or fields.get("Machine") != "ARM"
            or "hard-float ABI" not in fields.get("Flags", "")
        ):
            raise AssertionError(f"Expected an ARM ELF32 hard-float test binary: {command[0]}")
        qemu = shutil.which("qemu-arm") or shutil.which("qemu-arm-static")
        if qemu is None:
            raise RuntimeError("Install qemu-user to execute ARMHF tests")
        environment["GO_TEST_WRAP"] = "0"
        # Keep the target library path out of the host QEMU loader environment.
        target_library_path = environment.pop("LD_LIBRARY_PATH")
        command = [
            qemu,
            "-L",
            str(runtime_root),
            "-E",
            "LD_LIBRARY_PATH=" + target_library_path,
            *command,
        ]
        print(f"ARMHF ELF32 hard-float execution with {qemu}", flush=True)

    result = subprocess.run(command, env=environment)
    if result.returncode < 0:
        child_signal = -result.returncode
        if child_signal not in {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(child_signal, signal.SIG_DFL)
        os.kill(os.getpid(), child_signal)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
