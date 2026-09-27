#!/usr/bin/env bash
set -euo pipefail

package_archive="$1"
built_library="$2"
package_root="${TEST_TMPDIR}/libswsscommon-package"

soname_from() {
    LC_ALL=C readelf --dynamic "$1" |
        sed -n 's/.*(SONAME).*Library soname: \[\([^]]*\)\].*/\1/p'
}

soname="$(soname_from "${built_library}")"
if [[ -z "${soname}" || "${soname}" == */* ]]; then
    echo "The built library must declare a valid SONAME; found '${soname}'" >&2
    exit 1
fi

mkdir -p "${package_root}"
tar --extract --file="${package_archive}" --directory="${package_root}"

mapfile -t runtime_libraries < <(find "${package_root}/usr/lib" -name "${soname}" -print)
if [[ "${#runtime_libraries[@]}" -ne 1 ]]; then
    echo "Expected one packaged ${soname}, found ${#runtime_libraries[@]}" >&2
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

echo "Packaged ${soname} resolves to an ELF with the expected SONAME"
