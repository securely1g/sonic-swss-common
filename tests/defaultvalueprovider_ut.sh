#!/usr/bin/env bash
set -euo pipefail

test_binary="$1"
if [[ "${test_binary}" != /* ]]; then
    test_binary="${PWD}/${test_binary}"
fi
fixture_root="${TEST_SRCDIR}/${TEST_WORKSPACE}/tests"

mkdir -p "${TEST_TMPDIR}/tests/yang" "${TEST_TMPDIR}/tests/yang-missing-ref"
cp --dereference "${fixture_root}/yang/"*.yang "${TEST_TMPDIR}/tests/yang/"
cp --dereference "${fixture_root}/yang-missing-ref/"*.yang "${TEST_TMPDIR}/tests/yang-missing-ref/"
cd "${TEST_TMPDIR}"
exec "${test_binary}"
