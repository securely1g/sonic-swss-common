#!/bin/bash
set -euo pipefail

# Only public options go into this runner's user RC. BAZEL_REMOTE_AUTH remains
# in the client environment and is not forwarded into build or test actions.
mkdir -p artifacts/validation
if [[ -z "${BAZEL_REMOTE_AUTH:-}" ]]; then
    printf '%s\n' '{"enabled": false, "reason": "repository secret unavailable"}' \
        > artifacts/validation/remote-cache.json
    echo 'Remote cache disabled: repository secret unavailable.'
    exit 0
fi

python3 -B tools/bazel/cache/credentials.py probe \
    --output artifacts/validation/remote-cache.json

upload=false
case "${GITHUB_EVENT_NAME:-}" in
    push|workflow_dispatch) upload=true ;;
esac
printf '\nbuild --config=ci-remote-cache\nbuild --remote_upload_local_results=%s\n' \
    "$upload" >> "$HOME/.bazelrc"
printf 'Remote cache enabled; upload local results: %s\n' "$upload"
