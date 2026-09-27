#!/usr/bin/env bash
set -euo pipefail

# Use this runner only for ARMHF ELF tests. Package sh_test targets run on the host.
runfiles_root="${RUNFILES_DIR:-${TEST_SRCDIR:-}}"
if [[ -z "${runfiles_root}" ]]; then
    echo "The ARMHF test runner requires Bazel runfiles" >&2
    exit 1
fi
source "${runfiles_root}/bazel_tools/tools/bash/runfiles/runfiles.bash"
runtime_archive="$(rlocation "${TEST_WORKSPACE:-_main}/tools/bazel/armhf/test_runtime.tar")"
if [[ ! -f "${runtime_archive}" ]]; then
    echo "The ARMHF runtime archive is missing from the test runfiles" >&2
    exit 1
fi
runtime_root="${TEST_TMPDIR}/armhf-runtime"
mkdir -p "${runtime_root}"
tar --extract --file="${runtime_archive}" --directory="${runtime_root}"

# Debian Trixie is usrmerged; base-files normally supplies these aliases.
for directory in bin lib sbin; do
    if [[ ! -e "${runtime_root}/${directory}" && ! -L "${runtime_root}/${directory}" ]]; then
        ln -s "usr/${directory}" "${runtime_root}/${directory}"
    fi
done

binary="$1"
shift
header="$(LC_ALL=C readelf --file-header "${binary}")"
if ! grep -q 'Class:.*ELF32' <<<"${header}" ||
    ! grep -q 'Machine:.*ARM' <<<"${header}" ||
    ! grep -q 'Flags:.*hard-float ABI' <<<"${header}"; then
    echo "The ARMHF test runner requires an ARM ELF32 hard-float binary: ${binary}" >&2
    exit 1
fi

qemu="$(command -v qemu-arm || command -v qemu-arm-static || true)"
if [[ -z "${qemu}" ]]; then
    echo "Install qemu-user to execute ARMHF tests" >&2
    exit 1
fi
echo "ARMHF ELF32 hard-float execution on $(uname -m) with ${qemu}: ${binary}"
exec "${qemu}" -L "${runtime_root}" "${binary}" "$@"
