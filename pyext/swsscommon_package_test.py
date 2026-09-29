#!/usr/bin/python3
"""Check the installed Python archive exposes the selected native API."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile


_CHECK_IMPORT = r"""
import os
from pathlib import Path

import swsscommon
from swsscommon import _swsscommon as native
from swsscommon import swsscommon as binding

package_root = Path(os.environ["SWSS_PACKAGE_ROOT"]).resolve()
for module in (swsscommon, binding, native):
    assert Path(module.__file__).resolve().is_relative_to(package_root), module.__file__

runtime_root = Path(os.environ["SWSS_RUNTIME_ROOT"]).resolve()
loaded = set()
for line in Path("/proc/self/maps").read_text().splitlines():
    fields = line.split(maxsplit=5)
    if len(fields) == 6 and fields[5].startswith("/"):
        loaded.add(Path(fields[5]).resolve())

def check_runtime_library(prefix, required):
    matches = {path for path in loaded if path.name.startswith(prefix)}
    if not required:
        assert not matches, matches
        return
    assert len(matches) == 1, matches
    library = matches.pop()
    assert library.is_relative_to(runtime_root), library

check_runtime_library("libhiredis.so.", True)
check_runtime_library("libyang.so.", os.environ["SWSS_YANG_MODE"] == "enabled")

yang_classes = (
    "DefaultValueProvider",
    "DecoratorTable",
    "DecoratorSubscriberStateTable",
)
yang_constants = {
    "CFG_ACL_TABLE_TABLE_NAME": "ACL_TABLE",
    "CFG_PORT_TABLE_NAME": "PORT",
}
if os.environ["SWSS_YANG_MODE"] == "enabled":
    for name in yang_classes:
        assert hasattr(binding, name), name
    for name, value in yang_constants.items():
        assert getattr(binding, name) == value, name
    # Construction crosses the generated SWIG wrapper into the enabled native
    # class. The C++ fixture test covers its model evaluation behavior.
    provider = binding.DefaultValueProvider()
    assert provider.thisown
else:
    for name in (*yang_classes, *yang_constants):
        assert not hasattr(binding, name), name

print(f"Imported packaged Python bindings with YANG {os.environ['SWSS_YANG_MODE']}")
Path(os.environ["SWSS_RESULT_FILE"]).write_text("passed\n")
"""


def main():
    runner, runtime_archive, archive, yang_mode = sys.argv[1:]
    if yang_mode not in {"enabled", "disabled"}:
        raise ValueError(f"Unknown YANG mode: {yang_mode}")

    install_root = Path(os.environ["TEST_TMPDIR"]) / "swsscommon-package"
    with tarfile.open(archive) as package:
        package.extractall(install_root, filter="data")

    package_root = install_root / "usr/lib/python3/dist-packages"
    result_file = install_root / "import-check-passed"
    result_file.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment.update(
        PYTHONNOUSERSITE="1",
        PYTHONPATH=str(package_root),
        RUNFILES_DIR="",
        RUNFILES_MANIFEST_FILE="",
        SWSS_PACKAGE_ROOT=str(package_root),
        SWSS_RESULT_FILE=str(result_file),
        SWSS_YANG_MODE=yang_mode,
    )
    subprocess.run(
        [runner, runtime_archive, "-", yang_mode, sys.executable, "-S", "-c", _CHECK_IMPORT],
        env=environment,
        check=True,
    )
    # The generated package can register an atexit hook that exits with zero
    # after loading runfiles libraries. Require proof that every check completed.
    if not result_file.is_file() or result_file.read_text() != "passed\n":
        raise AssertionError("Packaged Python import checks did not complete")


if __name__ == "__main__":
    main()
