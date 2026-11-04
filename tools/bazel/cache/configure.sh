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

probe_status=0
python3 -B tools/bazel/cache/credentials.py probe "${probe_args[@]}" \
    --output artifacts/validation/remote-cache.json || probe_status=$?

if [[ "$probe_status" -ne 0 ]]; then
    # Uploads stay fail-closed. A read-only run may continue without the cache
    # only when the endpoint gave no HTTP response at all (DNS, connection or
    # timeout failure). Any HTTP error status still fails the run.
    if [[ "$upload" == true ]] || ! python3 - <<'PY'
import json
from pathlib import Path
import sys

try:
    receipt = json.loads(Path("artifacts/validation/remote-cache.json").read_text())
except (OSError, ValueError):
    sys.exit(1)
unreachable = {"URLError", "TimeoutError", "OSError", "gaierror", "ConnectionError",
               "ConnectionRefusedError", "ConnectionResetError"}
sys.exit(0 if "status" not in receipt and receipt.get("error_type") in unreachable else 1)
PY
    then
        exit "$probe_status"
    fi
    python3 - <<'PY'
import json
import os
from pathlib import Path

receipt = Path("artifacts/validation/remote-cache.json")
value = json.loads(receipt.read_text())
value.update(remote_cache_enabled=False, upload_local_results=False,
             github_event_name=os.environ.get("GITHUB_EVENT_NAME"),
             github_ref=os.environ.get("GITHUB_REF"))
receipt.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
    printf 'Remote cache unreachable; continuing this read-only run without it.\n'
    exit 0
fi

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
