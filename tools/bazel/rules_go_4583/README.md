# Original rules_go #4583 regression

Run on native Linux AMD64 or ARM64 with Bazelisk on PATH:

```sh
python3 tools/bazel/rules_go_4583/run.py --artifacts /absolute/fresh/evidence-directory
```

The driver creates a temporary, isolated module selecting BCR `rules_go 0.64.1`
and Go `1.25.0`. It does not alter Common's module or the rules_go implementation.
Both outer and nested invocations explicitly select Bazel `8.5.1`.

`original_cc_header_inputs_test.go.txt` is the exact 3,476-byte test extracted
from registry patch 0003 at commit `3fc93604f17c358630171af3094f375b6c88ed3b`
(patch commit `a038f38af7a82a1e395b4f1e966b597ae104ea2f`). The driver verifies its
SHA256 and records the immutable source URL and hashes.

First, the original test must fail to compile because rules_go 0.64.1's test
harness removed `WorkspaceSuffix`. The driver then applies a test-only API
adaptation: rename it to `ModuleFileSuffix` and declare `local_repository` using
`use_repo_rule`. It preserves the fixture, imported header, transitive wrapper,
and original Bazel commands. The resulting diff is retained.

The adapted `TestTransitiveCcHeaders` and
`TestTransitiveCcHeadersExternalIncludePaths` run separately, even when the
first fails. They are **build-only** tests: they compile/link a generated Go
binary but do not execute it. The first case uses the default feature setting;
the second explicitly enables `external_include_paths`. No disabled-feature
case or Debian package creation is added.

The upstream 0.64.1 harness selects `rules_cc 0.2.18` for Bazel 8 and wraps the
outer Go SDK into the nested module. This differs from Common's previously
tested `rules_cc 0.2.16`; this experiment does not silently override that input.

A small PATH wrapper pins nested Bazel, records its actual version, and adds
only sandbox and evidence flags to the original build arguments. Nested compile
actions use `processwrapper-sandbox`. Before harness cleanup, it saves fixtures,
module locks, production-source hashes, SDK receipts, separate action/event
logs, and the nested build status. Optional `mod show_repo` failures are recorded
separately; no full module graph is evaluated. The job fails for an unexpected
original result, either adapted test failure, or missing required SDK evidence.
