#!/usr/bin/env bash
set -euo pipefail

package_archive="$1"
symbols_archive="$2"
expected_multiarch="$3"
objcopy="$4"
gdb="$5"
package_root="${TEST_TMPDIR}/libswsscommon-package"
symbols_root="${TEST_TMPDIR}/libswsscommon-symbols"

assert_equal() {
    local description="$1" expected="$2" actual="$3"
    if [[ "${actual}" != "${expected}" ]]; then
        echo "${description}: expected '${expected}', found '${actual}'" >&2
        exit 1
    fi
}

soname_from() {
    LC_ALL=C readelf --dynamic "$1" |
        sed -n 's/.*(SONAME).*Library soname: \[\([^]]*\)\].*/\1/p'
}

build_id_from() {
    LC_ALL=C readelf --notes "$1" | awk '/Build ID:/ { print $3; exit }'
}

validate_debug_pair() {
    local runtime_file="$1"
    local build_id debug_file debug_build_id section_file debuglink_file
    build_id="$(build_id_from "${runtime_file}")"
    if [[ ! "${build_id}" =~ ^[0-9a-f]{4,}$ ]]; then
        echo "Missing or invalid build ID in ${runtime_file}: ${build_id}" >&2
        exit 1
    fi
    debug_file="${symbols_root}/usr/lib/debug/.build-id/${build_id:0:2}/${build_id:2}.debug"
    if [[ ! -f "${debug_file}" ]]; then
        echo "Missing detached debug file for ${runtime_file}: ${debug_file}" >&2
        exit 1
    fi
    debug_build_id="$(build_id_from "${debug_file}")"
    if [[ "${debug_build_id}" != "${build_id}" ]]; then
        echo "Detached debug file build ID ${debug_build_id} does not match ${build_id}" >&2
        exit 1
    fi

    section_file="${TEST_TMPDIR}/${build_id}.sections"
    LC_ALL=C readelf --section-headers --wide "${runtime_file}" > "${section_file}"
    if grep -Eq '\.(debug_|zdebug_)' "${section_file}"; then
        echo "Runtime file still contains DWARF debug sections: ${runtime_file}" >&2
        exit 1
    fi
    LC_ALL=C readelf --section-headers --wide "${debug_file}" > "${section_file}"
    if ! grep -Eq '\.debug_info[[:space:]]' "${section_file}" ||
        ! grep -Eq '\.debug_line[[:space:]]' "${section_file}"; then
        echo "Detached file lacks source-level debug sections: ${debug_file}" >&2
        exit 1
    fi

    debuglink_file="${TEST_TMPDIR}/${build_id}.debuglink"
    "${objcopy}" --dump-section ".gnu_debuglink=${debuglink_file}" \
        "${runtime_file}" "${TEST_TMPDIR}/${build_id}.elf"
    python3 - "${debuglink_file}" "${debug_file}" <<'PY'
from pathlib import Path
import struct
import sys
import zlib

debuglink = Path(sys.argv[1]).read_bytes()
debug_file = Path(sys.argv[2])
name_end = debuglink.index(b"\0")
name = debuglink[:name_end].decode()
crc_offset = (name_end + 4) & ~3
expected_crc = struct.unpack_from("<I", debuglink, crc_offset)[0]
actual_crc = zlib.crc32(debug_file.read_bytes())
if name != debug_file.name or expected_crc != actual_crc:
    raise SystemExit(f"Invalid debug link for {debug_file}")
PY
    echo "Validated $(basename "${runtime_file}") build ID ${build_id}"
}

soname="libswsscommon.so.0"
mkdir -p "${package_root}" "${symbols_root}"
tar --extract --file="${package_archive}" --directory="${package_root}"
tar --extract --file="${symbols_archive}" --directory="${symbols_root}"

if [[ -e "${package_root}/usr/lib/debug" ]]; then
    echo "Runtime package contains detached debug files" >&2
    exit 1
fi

mapfile -t runtime_libraries < <(find "${package_root}/usr/lib" -name "${soname}" -print)
if [[ "${#runtime_libraries[@]}" -ne 1 ]]; then
    echo "Expected one packaged ${soname}, found ${#runtime_libraries[@]}" >&2
    exit 1
fi

expected_runtime_library="${package_root}/usr/lib/${expected_multiarch}/${soname}"
if [[ "${runtime_libraries[0]}" != "${expected_runtime_library}" ]]; then
    echo "Expected ${soname} in usr/lib/${expected_multiarch}; found ${runtime_libraries[0]}" >&2
    exit 1
fi

if ! resolved_library="$(realpath --canonicalize-existing "${runtime_libraries[0]}")"; then
    echo "Packaged ${soname} does not resolve to a file" >&2
    exit 1
fi
case "${resolved_library}" in
    "${package_root}"/*) ;;
    *)
        echo "Packaged ${soname} resolves outside the package: ${resolved_library}" >&2
        exit 1
        ;;
esac

packaged_soname="$(soname_from "${resolved_library}")"
if [[ "${packaged_soname}" != "${soname}" ]]; then
    echo "Packaged library SONAME '${packaged_soname}' does not match '${soname}'" >&2
    exit 1
fi

if [[ "${expected_multiarch}" == "arm-linux-gnueabihf" ]]; then
    # AAELF32 records the floating-point procedure-call standard in e_flags for
    # linked ET_EXEC/ET_DYN files. Build attributes are optional after linking.
    for binary in "${resolved_library}" "${package_root}/usr/bin/swssloglevel"; do
        header="$(LC_ALL=C readelf --file-header "${binary}")"
        if ! grep -q 'Class:.*ELF32' <<<"${header}" ||
            ! grep -q 'Machine:.*ARM' <<<"${header}" ||
            ! grep -q 'Flags:.*hard-float ABI' <<<"${header}"; then
            echo "Expected an ARM ELF32 hard-float binary: ${binary}" >&2
            exit 1
        fi
    done
    if ! LC_ALL=C readelf --program-headers "${package_root}/usr/bin/swssloglevel" |
        grep -q 'Requesting program interpreter: /lib/ld-linux-armhf.so.3'; then
        echo "The packaged ARMHF tool must use /lib/ld-linux-armhf.so.3" >&2
        exit 1
    fi
fi

library_dir="$(dirname "${resolved_library}")"
assert_equal "Runtime library filename" "libswsscommon.so.0.0.0" "$(basename "${resolved_library}")"
assert_equal "SONAME symlink" "libswsscommon.so.0.0.0" "$(readlink "${library_dir}/${soname}")"
assert_equal "Development symlink" "${soname}" "$(readlink "${library_dir}/libswsscommon.so")"
assert_equal "Runtime library mode" "555" "$(stat --format=%a "${resolved_library}")"
assert_equal "swssloglevel mode" "755" "$(stat --format=%a "${package_root}/usr/bin/swssloglevel")"

validate_debug_pair "${resolved_library}"
validate_debug_pair "${package_root}/usr/bin/swssloglevel"

gdb_output="${TEST_TMPDIR}/libswsscommon-gdb.txt"
"${gdb}" --batch --nx --quiet \
    -ex "set debuginfod enabled off" \
    -ex "set debug-file-directory ${symbols_root}/usr/lib/debug" \
    -ex "file ${resolved_library}" \
    -ex "info line swss::RedisReply::checkReply()" > "${gdb_output}" 2>&1
if ! grep -Eq 'Line [0-9]+ of ".*common/redisreply\.cpp"' "${gdb_output}"; then
    cat "${gdb_output}" >&2
    echo "GDB did not resolve RedisReply::checkReply() to its source line" >&2
    exit 1
fi

echo "Runtime package layout and detached debug information are valid for ${expected_multiarch}"
