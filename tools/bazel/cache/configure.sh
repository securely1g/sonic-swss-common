#!/bin/bash
set -euo pipefail

# Only public options go into this runner's user RC. BAZEL_REMOTE_AUTH remains
# in the client environment and is not forwarded into build or test actions.
mkdir -p artifacts/validation
upload=false
probe_args=(--anonymous)
if [[ "$(python3 -B tools/bazel/cache/policy.py)" == true && -n "${BAZEL_REMOTE_AUTH:-}" ]]; then
    upload=true
    probe_args=()
fi

python3 -B tools/bazel/cache/credentials.py probe "${probe_args[@]}" \
    --output artifacts/validation/remote-cache.json

printf '\nbuild --config=ci-remote-cache\n' >> "$HOME/.bazelrc"
if [[ "$upload" == true ]]; then
    printf 'build --config=ci-remote-cache-write\n' >> "$HOME/.bazelrc"
fi
printf 'build --remote_upload_local_results=%s\n' "$upload" >> "$HOME/.bazelrc"
python3 - "$upload" <<'PY'
import json
import os
from pathlib import Path
import sys

receipt = Path("artifacts/validation/remote-cache.json")
value = json.loads(receipt.read_text())
value.update(upload_local_results=sys.argv[1] == "true",
             github_event_name=os.environ.get("GITHUB_EVENT_NAME"),
             github_ref=os.environ.get("GITHUB_REF"))
receipt.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
printf 'Remote cache enabled; upload local results: %s\n' "$upload"
