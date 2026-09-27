"""Verify libyang's native libraries resolve to declared Bazel runfiles."""

import os
from pathlib import Path

import libyang


def declared_runfiles():
    root = Path(os.environ["TEST_SRCDIR"])
    declared = set()
    visited = set()
    for directory, subdirs, files in os.walk(root, followlinks=True):
        resolved_directory = Path(directory).resolve()
        if resolved_directory in visited:
            subdirs.clear()
            continue
        visited.add(resolved_directory)
        for name in files:
            if name.startswith(("libyang.so", "libxxhash.so")):
                declared.add((Path(directory) / name).resolve())
    return declared


def main():
    # Importing libyang loads its CFFI extension and the native dependency tree.
    assert libyang.__file__
    loaded = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            loaded.add(Path(fields[5]).resolve())

    declared = declared_runfiles()
    for prefix in ("libyang.so.", "libxxhash.so."):
        matches = {path for path in loaded if path.name.startswith(prefix)}
        if len(matches) != 1:
            raise AssertionError(f"Expected one loaded {prefix} library, found {sorted(matches)}")
        library = matches.pop()
        if library not in declared:
            raise AssertionError(f"Loaded native library is not a declared runfile: {library}")
        print(f"Loaded declared native library: {library}")


if __name__ == "__main__":
    main()
