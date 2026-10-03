# BuildBuddy cache pilot

This optional benchmark helps SWSS Common maintainers measure whether a hosted
build cache saves time on the ordinary CI workload. Regular PR builds keep their
existing GitHub disk and repository caches, and CodeQL stays unchanged. A baseline
run works without a BuildBuddy account; a cache comparison needs an account and a
key with cache read/write access.

## Run it

In the **Bazel** GitHub Actions workflow, select **Run workflow**, choose the
branch containing this runner, and set `cache_benchmark` to `baseline` or
`compare`. Both native AMD64 and ARM64 jobs use the same Debian Trixie setup as
ordinary CI. The default `off` runs the ordinary workflow.

Before using `compare`, create a key on your
[BuildBuddy Quickstart page](https://www.buildbuddy.io/docs/guide-auth/) and save
it as the repository Actions secret `BUILDBUDDY_API_KEY`. Do not commit the key.
The comparison writes build outputs to your BuildBuddy organization; it does not
use remote execution or upload a build-event stream to BuildBuddy. The secret is
available only to manually selected `compare` and `remote` jobs, not PR jobs.
Missing credentials stop these modes before any build starts.

To run on a native Trixie host with the ordinary CI prerequisites installed:

```sh
python3 tools/bazel/cache_benchmark.py \
  --mode baseline --arch AMD64 --output-dir artifacts/cache-baseline \
  --disk-cache "$HOME/.cache/bazel-disk" \
  --repository-cache "$HOME/.cache/bazel-repo"
```

For comparison, supply `BUILDBUDDY_API_KEY` through your secret manager or
environment, then use `--mode compare` and a new output directory. Use
`--arch ARM64` on a native ARM64 host. Optional `--bazel`, `--repository-cache`
and `--remote-cache` arguments select the executable, shared download cache and
TLS cache endpoint. The default endpoint is `grpcs://remote.buildbuddy.io`.
`--disk-cache` must point to an existing directory; an empty directory represents
a GitHub cache miss. Omit it only for a deliberately cold local-cache experiment.

## What the numbers mean

### Inspect which repositories supply cached actions

Select `cache_benchmark: remote` to inspect an existing populated BuildBuddy cache.
Copy each architecture's `remote_instance_name` from a successful comparison's
`summary.json` into the corresponding `remote_instance_amd64` and
`remote_instance_arm64` workflow inputs. This mode uses remote reads only and
skips the GitHub build-output cache. It retains the dependency download cache,
runs the same YANG-enabled then disabled targets, and reruns tests.

The runner records individual execution results and classifies each remote hit
by its owning target label: targets in the main repository belong to
`sonic-swss-common`; external repository targets belong to dependencies or build
tools. Missing ownership is reported separately. The per-action count must match
Bazel's aggregate remote-hit count. Internal actions and results reused directly
from the output base are outside that remote-hit count.

The published action records contain selected metadata only; commands,
environments and input contents are omitted. Raw execution logs remain private
and are deleted after processing. The breakdown counts action results rather
than unique source files or objects stored by BuildBuddy. Extra execution logging
has overhead, so this diagnostic is not a replacement for the original timing
comparison. Cache eviction or changed inputs can also change its hit count.

On a native host, use `--mode remote --remote-instance-name <existing-instance>`
with the same architecture, repository cache and credentials as above. This mode
automatically enables execution logging. Optional `--execution-log` also collects
the breakdown during other modes.

### Compare build times

Each run first fetches dependencies for both YANG settings into a shared
repository cache. Preparation is timed separately. It then runs one pass through
these cases, in order:

| Case | Disk cache | Remote reads | Remote writes |
| --- | --- | --- | --- |
| `baseline` | Private copy of restored GitHub cache | Disabled | Disabled |
| `populate` (compare only) | Disabled | Disabled | Enabled |
| `remote` (compare or remote-only mode) | Disabled | Enabled | Disabled |
| `combined` (compare with `--disk-cache`) | Separate copy of the same GitHub cache | Enabled | Disabled |

The comparison uses a new remote instance name for that run and architecture.
Population finishes its uploads before the remote case starts. Every case uses
one fresh output base, first with YANG enabled and then disabled, matching the
order and local reuse within ordinary CI. The previous case's output base is
removed after recording evidence to limit disk usage; the download cache stays.
The original restored disk cache is never written: baseline changes cannot warm
the combined case's copy. Copies are removed with their case, and their creation
time is reported separately. Test result caching is disabled, so tests run again
while their compiled inputs can benefit from the build cache. All build outputs
are downloaded when a remote cache is used.

The explicit target list matches ordinary CI: the library and programs, generated
schema, Rust bindings, Go and Python bindings, runtime and debug tar packages, and
their selected tests. It does not generate Debian packages. Formatting, package
installation, dependency preparation, artifact hashing/upload and cleanup are
outside the measured time. Bazel startup, loading, analysis, compilation, package
creation, tests and remote transfers are inside it. Copying the restored disk
cache is outside the measurement. Fresh output bases still
unpack repositories and may run repository rules; preparation is not a guarantee
that every later network request disappears.

In CI, the primary comparison is `baseline` versus `combined`: what BuildBuddy
adds to the restored GitHub disk cache. The `remote` case separately shows remote
caching without a disk cache. Without `--disk-cache`, baseline is cold and the
comparison is baseline versus remote only; it cannot establish an improvement
over existing GitHub caching. A baseline-only run is not a cache comparison.
Results from different runners or dates should not be treated as a controlled
comparison. One pass is a pilot measurement, not a stable performance estimate.
The `populate` cost is reported separately. Each invocation restarts Bazel in
batch mode, so absolute times also differ from ordinary CI's persistent server.

## Evidence and credentials

The `cache-benchmark-AMD64` and `cache-benchmark-ARM64` artifacts contain
`summary.json`, invocation logs, build-event JSON, JSON profiles and generated
module lockfiles. The summary records the restored cache's file count and size,
each invocation's duration, exit status, targets and package SHA-256 hashes.
It reports remote hits from Bazel 8's
`actionSummary.runnerCount` entry named `remote cache hit`; absent or incomplete
evidence fails the measurement instead of becoming zero hits. These counts are
not a cache-hit percentage. The remote-only case must verify cache hits, and
package hashes must match across all cases for each YANG setting. Otherwise the
comparison reports an unverified cache or differing packages for investigation.
The combined case can legitimately have zero remote hits when the disk cache
already serves everything. In that case, timing differences are still reported,
but `remote_reuse_observed` is false and no cache speedup is attributed to
BuildBuddy.

Output checks distinguish the primary pair (`primary_output_match`), populate
versus remote (`remote_roundtrip_match`), and restored-cache baseline versus fresh
population (`cross_cache_output_match`). Observed primary timings remain available
when the two pairs each match but their package hashes differ from each other.
The overall status stays `package_outputs_differ` with a failing exit code, and
`package_mismatches` records the cases, YANG setting, package and both hashes.
Matching pairs do not resolve that broader reproducibility concern. A failed
preparation/build stops later cases and keeps the evidence already collected.

The runner puts the BuildBuddy key in a private temporary rc file. It removes
credential variables such as `BAZELISK_GITHUB_TOKEN`, `GITHUB_TOKEN` and
`BUILDBUDDY_API_KEY` from Bazel's environment and redacts their values from retained
evidence. Published build events also omit every `client_env` option, regardless
of its name. Raw logs, build events and profiles remain outside the artifact
directory until sanitized, then are deleted with the temporary workspace. The
runner preserves generated `MODULE.bazel.lock` files as evidence without
committing them.

Run the harness tests without building SONiC:

```sh
python3 -m unittest discover -s tools/bazel -p test_cache_benchmark.py
```
