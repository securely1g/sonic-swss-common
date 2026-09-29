#!/usr/bin/env bash
set -euo pipefail

test_command=("$@")
for index in "${!test_command[@]}"; do
    if [[ "${test_command[${index}]}" != /* ]]; then
        test_command[${index}]="${PWD}/${test_command[${index}]}"
    fi
done
fixture_root="${TEST_SRCDIR}/${TEST_WORKSPACE}/tests"

mkdir -p "${TEST_TMPDIR}/tests/yang" "${TEST_TMPDIR}/tests/yang-missing-ref"
cp --dereference "${fixture_root}/yang/"*.yang "${TEST_TMPDIR}/tests/yang/"
cp --dereference "${fixture_root}/yang-missing-ref/"*.yang "${TEST_TMPDIR}/tests/yang-missing-ref/"
cd "${TEST_TMPDIR}"
exec "${test_command[@]}"
