#!/usr/bin/env bash
set -euo pipefail

# Keep an optional QEMU wrapper with the binary, separate from fixture paths.
test_command=()
while [[ "$1" != -- ]]; do
    test_command+=("$1")
    shift
done
shift
for index in "${!test_command[@]}"; do
    if [[ "${test_command[${index}]}" != /* ]]; then
        test_command[${index}]="${PWD}/${test_command[${index}]}"
    fi
done

mkdir -p "${TEST_TMPDIR}/tests/yang" "${TEST_TMPDIR}/tests/yang-missing-ref"
for fixture in "$@"; do
    fixture_group="$(basename "$(dirname "${fixture}")")"
    cp --dereference "${fixture}" "${TEST_TMPDIR}/tests/${fixture_group}/"
done
cd "${TEST_TMPDIR}"
exec "${test_command[@]}"
