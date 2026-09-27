#!/usr/bin/env bash
set -euo pipefail

runtime_archive="$1"
package_archive="$2"
yang_mode="$3"
runtime_root="${TEST_TMPDIR}/armhf-python-runtime"
completion_sentinel="${TEST_TMPDIR}/armhf-python-complete"
mkdir -p "${runtime_root}"
tar --extract --file="${runtime_archive}" --directory="${runtime_root}"
tar --extract --file="${package_archive}" --directory="${runtime_root}"

# Debian Trixie is usrmerged; base-files normally supplies these aliases.
for directory in bin lib sbin; do
    if [[ ! -e "${runtime_root}/${directory}" && ! -L "${runtime_root}/${directory}" ]]; then
        ln -s "usr/${directory}" "${runtime_root}/${directory}"
    fi
done

python="${runtime_root}/usr/bin/python3.13"
extension="${runtime_root}/usr/lib/python3/dist-packages/swsscommon/lib_swsscommon.so"
for binary in "${python}" "${extension}"; do
    header="$(LC_ALL=C readelf --file-header "${binary}")"
    if ! grep -q 'Class:.*ELF32' <<<"${header}" ||
        ! grep -q 'Machine:.*ARM' <<<"${header}" ||
        ! grep -q 'Flags:.*hard-float ABI' <<<"${header}"; then
        echo "Expected an ARM ELF32 hard-float binary: ${binary}" >&2
        exit 1
    fi
done

qemu="$(command -v qemu-arm || command -v qemu-arm-static || true)"
if [[ -z "${qemu}" ]]; then
    echo "Install qemu-user to execute the ARMHF Python runtime test" >&2
    exit 1
fi
echo "ARMHF Python execution on $(uname -m) with ${qemu}"
PYTHONHOME="${runtime_root}/usr" \
PYTHONPATH="${runtime_root}/usr/lib/python3/dist-packages" \
PYTHONNOUSERSITE=1 \
ARMHF_RUNTIME_ROOT="${runtime_root}" \
ARMHF_COMPLETION_SENTINEL="${completion_sentinel}" \
ARMHF_YANG_MODE="${yang_mode}" \
RUNFILES_DIR= \
RUNFILES_MANIFEST_FILE= \
    "${qemu}" -L "${runtime_root}" "${python}" -S -c '
import os
from pathlib import Path
import struct
import sysconfig
import swsscommon as package
from swsscommon import _swsscommon, swsscommon

assert struct.calcsize("P") == 4
assert sysconfig.get_config_var("MULTIARCH") == "arm-linux-gnueabihf"
runtime_root = Path(os.environ["ARMHF_RUNTIME_ROOT"]).resolve()
for path in [*package.__path__, swsscommon.__file__, _swsscommon.__file__]:
    assert Path(path).resolve().is_relative_to(runtime_root), path
pair = swsscommon.FieldValuePair("field", "value")
assert pair.first == "field" and pair.second == "value"
values = swsscommon.FieldValuePairs()
values.append(pair)
assert len(values) == 1 and values[0] == ("field", "value")
yang_mode = os.environ["ARMHF_YANG_MODE"]
assert yang_mode in {"enabled", "disabled"}, yang_mode
yang_classes = (
    "DefaultValueProvider",
    "DecoratorTable",
    "DecoratorSubscriberStateTable",
)
yang_constants = {
    "CFG_ACL_TABLE_TABLE_NAME": "ACL_TABLE",
    "CFG_PORT_TABLE_NAME": "PORT",
}
if yang_mode == "enabled":
    for name in yang_classes:
        assert hasattr(swsscommon, name), name
    for name, value in yang_constants.items():
        assert getattr(swsscommon, name) == value, name
else:
    for name in (*yang_classes, *yang_constants):
        assert not hasattr(swsscommon, name), name
print("Python ABI:", sysconfig.get_config_var("SOABI"), "wrapped values and YANG", yang_mode, "passed")
Path(os.environ["ARMHF_COMPLETION_SENTINEL"]).write_text("passed\n")
'

if [[ ! -f "${completion_sentinel}" ]] || [[ "$(cat "${completion_sentinel}")" != "passed" ]]; then
    echo "The ARMHF Python checks did not complete" >&2
    exit 1
fi
