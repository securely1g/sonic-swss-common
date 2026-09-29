#!/usr/bin/env bash
set -euo pipefail

test_binary="$1"
if [[ "${test_binary}" != /* ]]; then
    test_binary="${PWD}/${test_binary}"
fi
# The declared binary and fixtures share a package in the runfiles tree. Keep
# that path (without resolving the binary symlink) when this is an external repo.
fixture_root="$(dirname "${test_binary}")"

mkdir -p "${TEST_TMPDIR}/tests/yang" "${TEST_TMPDIR}/tests/yang-missing-ref"
cp --dereference "${fixture_root}/yang/"*.yang "${TEST_TMPDIR}/tests/yang/"
cp --dereference "${fixture_root}/yang-missing-ref/"*.yang "${TEST_TMPDIR}/tests/yang-missing-ref/"
cd "${TEST_TMPDIR}"
exec "${test_binary}"
