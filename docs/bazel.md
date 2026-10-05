# Standalone Bazel build

The standalone build uses Bazel 8.5.1, selected by `.bazelversion`. Install
[Bazelisk](https://bazel.build/install/bazelisk) and invoke it as `bazel`.
Dependencies come from the configured SONiC Bazel registry and the Bazel Central
Registry; the native GCC 14.2 toolchain and Debian package inputs are downloaded
by Bazel. Local builds and CI use the maintained SONiC registry `main` branch,
followed by the Bazel Central Registry.

Local builds and normal CI use `sonic-bazel-registry/main` in `.bazelrc`, with
Bazel Central Registry as the separate third-party registry. Module versions
and source checksums still select the dependencies; the registry URL does not
select their latest source code.

The timestamp integration is published on registry `main`. Builds select its
landed source version through `MODULE.bazel`; no temporary registry override
is needed.

Package artifacts retain `effective.bazelrc` and the generated module lock for
dependency inspection.

## Environment

Use a native AMD64 or ARM64 Linux environment with Debian Trixie userspace. The
toolchains require the execution CPU and target CPU to match. AMD64 is the
default; native ARM64 requires `--config=aarch64`. The target libraries link
against Trixie's glibc 2.41, so CI runs inside a Trixie container on each native
GitHub-hosted runner.

CI uses `debian:trixie-20260918` and installs these host tools. The package tests
use `readelf`, `objcopy`, and `nm` from `binutils`, plus GDB, Python, and `tar`.

```sh
apt-get update
apt-get install -y --no-install-recommends \
  binutils build-essential ca-certificates gdb git python3 tar
```

## Rust dependency resolution

Common uses the third-party Rust targets published by the pinned
`sonic-rust-deps` registry module. That module resolves its committed Cargo
inputs with `rules_rs` and owns the shared Serde features. Common's Rust BUILD
file lists its five normal dependencies explicitly; it does not create a second
Cargo dependency graph. Their versions and checksums match Common's `Cargo.lock`.

A clean checkout needs no preparation script or `Cargo.Bazel.lock`. Keep
Common's Cargo files in Git for Cargo builds. When changing dependencies, update
the owning shared module and check its lock against the affected Cargo inputs
before updating the module pin. The generated `MODULE.bazel.lock` stays ignored
and is retained with CI evidence.

The Bazel 8.5.1 root selects `rules_cc` 0.2.20 with a
`single_version_override`. Distroless uses its private C++ import rule, and
rules_cc 0.2.21/0.2.22 require a runtime toolchain type absent from Bazel 8.5.1.
An external root must repeat this override because module overrides do not
propagate from dependencies.

## Go build inputs

This branch selects `rules_go 0.64.1-sonic.1`, which adds the remaining relative
cgo header-path repair to upstream 0.64.1. The root override preserves that
prerelease selection, and standalone builds retain Go SDK 1.25.0. The original
upstream regression cases pass with the repair on native AMD64 and ARM64 using
Bazel 8.5.1 ([validation run](https://github.com/securely1g/sonic-swss-common/actions/runs/37182396550)).

The version is available on the maintained registry through
[registry PR #37](https://github.com/securely1g/sonic-bazel-registry/pull/37).
Local builds, native CI and CodeQL use the canonical `main` registry endpoint,
with BCR as the other registry. The evidence helper records the observed registry
revision and effective configuration without changing `.bazelrc`.

To retain the same evidence locally, use an empty evidence directory:

```sh
python3 tools/bazel/go_validation.py record /tmp/common-go-validation
# Run the appropriate native build/test commands below.
python3 tools/bazel/go_validation.py collect /tmp/common-go-validation
```

Add `--config=aarch64` to `collect` on native ARM64. Collection checks the
resolved module version, reviewed cgo source hash, Bazel 8.5.1, and Go 1.25.0,
and retains the generated module lock, source files, SDK receipts and repository
definition. It also verifies that the tracked checkout remains unchanged.

The existing six native jobs retain YANG enabled/disabled coverage and the
hiredis cgo probe with `external_include_paths` enabled and disabled. Common's
SWIG wrapper still compiles its generated C++ separately; the dedicated upstream
regression above covers the relative `cc_import` path that this arrangement does
not exercise. Package outputs remain tar archives.

## Build and test

Run the following from the repository root in Bash. Define the common and YANG
target lists, then run the command matching the machine's native CPU and desired
feature mode. CI runs both modes with these explicit labels. Explicit labels
make a required output's platform incompatibility fail the invocation instead
of being skipped by a wildcard.

```bash
targets=(
  //:libswsscommon
  //:libswsscommon_shared
  //:libswsscommon_consolidated.so
  //:swssloglevel
  //crates/swss-common:bindings_dir
  //crates/swss-common:swss_common
  //crates/swss-common:swss_common_test
  //tools/bazel/rust:shared_serde_test
  //dist:libswsscommon_pkg
  //dist:libswsscommon_pkg.debug_symbols
  //dist:sonic-db-cli_pkg
  //pyext:swsscommon_pkg
  //goext:swsscommon
  //goext:swsscommon_runtime_test
  //tests:status_code_util_test
  //tests:saiaclschema_ut
  //tests:notification_queue_ut
  //tests:interface_ut
  //tests:vrf_ut
  //tests:shared_library_runtime_test
  //dist:libswsscommon_package_test
  //dist:package_timestamps_test
  //pyext:swsscommon_package_test
)

yang_targets=(
  //common:cfg_schema_generated
  //tests:defaultvalueprovider_ut
)

# Native AMD64, YANG enabled (the default)
bazel test --//tools/bazel:yang_modules=True --test_output=errors "${targets[@]}" "${yang_targets[@]}"

# Native AMD64, YANG disabled
bazel test --//tools/bazel:yang_modules=False --test_output=errors "${targets[@]}"

# Native ARM64, YANG enabled
bazel test --config=aarch64 --//tools/bazel:yang_modules=True --test_output=errors "${targets[@]}" "${yang_targets[@]}"

# Native ARM64, YANG disabled
bazel test --config=aarch64 --//tools/bazel:yang_modules=False --test_output=errors "${targets[@]}"
```

`bazel test` builds the listed libraries, binaries, bindings, and packages as
well as running the listed tests. CI also checks Bazel file formatting:

```sh
# Native AMD64
bazel run //tools/bazel/buildifier:buildifier.format.check

# Native ARM64
bazel run --config=aarch64 //tools/bazel/buildifier:buildifier.format.check
```

The formatting target leaves lint disabled while the build retains intentional
Bazel 6 compatibility code. The existing `buildifier.check` target remains
available for reviewing those lint warnings.

## Rust library

`//crates/swss-common:swss_common` is the public Rust library. Common owns its
Rust sources, generated C API bindings, and the
link to `//:libswsscommon_shared`. The default library has Cargo's `async`
feature disabled. Its four existing unit tests run without a Redis server:

```sh
bazel test //crates/swss-common:swss_common_test \
  //tools/bazel/rust:shared_serde_test
```

Add `--config=aarch64` on native ARM64. CI runs the library and unit tests on
native AMD64 and ARM64 in both YANG modes. Redis-backed Cargo integration tests
and the optional async feature are outside this Bazel test target.

Bazel consumers depend on `@sonic-swss-common//crates/swss-common:swss_common`.
Common and SWSS both use `@sonic_rust_deps//:serde` and
`@sonic_rust_deps//:serde_core`, currently version 1.0.228. The shared module owns
the compiled traits and their feature selection; Common no longer exports Serde
aliases. Direct dependencies work when Common is built alone or imported by
another root, without repository overrides. The `shared_serde_test` exercises
Common's `CxxString` through both shared trait APIs, and SWSS's downstream
`common_rust_test` checks the boundary with a JSON roundtrip.

Common retains its own library, native link dependencies, bindings, and tests.
Consumers must register compatible Rust and bindgen toolchains because Common's
standalone toolchain registrations are development dependencies. When changing
third-party dependencies, update the tracked Cargo inputs and validate the
downstream consumer as well as Common's tests.

The underlying `//crates/swss-common:bindings_dir` target remains public. It
generates `bindings.rs` from every header in `common/c-api` and places it in the
directory layout expected by `OUT_DIR`. The header inventory comes from the
same Bazel filegroup used by the native Common library. The target uses
`--with-derive-partialeq`, matching `build.rs`.

Standalone builds select Rust 1.90.0 and use the Rust and bindgen integration
provided by `rules_rs` 0.1.0. Cargo's unchanged `build.rs` uses bindgen 0.70.1.
Tests validate compilation and behavior; they do not claim byte-for-byte
equality with Cargo's generated bindings. The Rust library is a source
dependency; it is not distributed as a precompiled Rust package.

## Build artifacts

Each successful Bazel job uploads its package archives. Open the repository's
**Actions** tab, select a successful **Bazel** workflow run, and download the
artifact for your architecture and feature mode from the **Artifacts** section:

- `sonic-swss-common-yang-AMD64`
- `sonic-swss-common-yang-ARM64`
- `sonic-swss-common-no-yang-AMD64`
- `sonic-swss-common-no-yang-ARM64`

The separate `sonic-swss-common-rust-AMD64` and
`sonic-swss-common-rust-ARM64` artifacts retain the Rust unit-test and shared
Serde test XML and logs, the Bazel module lockfile for each YANG mode, Common's
source `Cargo.lock`, and the tested source commit and tree. The CodeQL job retains
the Cargo and Bazel module locks.

Every package download contains these four archives:

- `libswsscommon_pkg.tar`: C++ runtime library, `swssloglevel`, Lua files, and
  database configuration.
- `libswsscommon_pkg.debug_symbols.tar`: detached debug information for the
  runtime library and `swssloglevel` in `libswsscommon_pkg.tar`.
- `sonic-db-cli_pkg.tar`: database CLI.
- `swsscommon_pkg.tar.gz`: Python bindings.

### Dependency runtime packages

Common's artifacts contain its own library, bindings, CLI, and symbols. Enabled
deployments also require packages owned by the
[SONiC Bazel registry](https://github.com/securely1g/sonic-bazel-registry/actions):

- `sonic-yang-models`: `//:yang_models_pkg` installs the prepared model payload
  under `/usr/local/yang-models`, the path used by `DefaultValueProvider`.
- `libyang`: `//:libyang_pkg` supplies the native runtime, and
  `//:libyang_pkg.debug_symbols` supplies its matching detached symbols.

Select a successful **Registry CI** workflow for the module versions pinned
by this build and download `registry-ci-<module>-<version>-<architecture>`
(`amd64` or `arm64`). Its `outputs.json` maps declared build targets to retained
files under `outputs/`, including their hashes. Keep each runtime/debug pair
from the same run. Registry module READMEs linked in the
[YANG input guide](../tools/bazel/yang/README.md) document the package targets.
Common CI does not copy or upload these dependency archives. Its staged Python
and Go tests still consume the pinned libyang runtime to verify integration.
Install the model and libyang runtime with Common's runtime archive and the
remaining Trixie runtime libraries. The Python bindings archive is required by
Python consumers. Production Make supplies the models through the
`sonic_yang_models` wheel; the registry tar carries the same model payload.

The standalone build produces tar archives. Debian dependency metadata, the
`libswsscommon-dev` package, and wheel packaging remain owned by the Make
workflow. GitHub requires you to sign in to download workflow artifacts.

### Reproducible package timestamps

The registry's `tar.bzl` patch adds an optional `default_mtime` setting to `tar()`.
It supplies timestamps only where the manifest has no explicit or inherited
time. `sonic_deploy_tar` selects `1672560000` for the library's runtime archive;
the CLI calls `tar(default_mtime = "1672560000")` directly. The library symlinks
retain their explicit `time=0`. The Lua package already uses an automatically
generated manifest with deterministic timestamps.

Common's package test checks its actual library, program, configuration and
symlink headers. Generic manifest and reproducibility tests accompany the
`tar.bzl` patch in the registry, and deployment integration tests remain in
`sonic-build-infra`. The root module pins the patched `tar.bzl` version because
Bazel otherwise ranks a transitive request for upstream `0.10.5` above
`0.10.5-sonic.1`.

## Debug symbols

Build the runtime package and its matching detached symbols together:

```sh
bazel build --//tools/bazel:yang_modules=True \
  //dist:libswsscommon_pkg //dist:libswsscommon_pkg.debug_symbols
```

Use `--//tools/bazel:yang_modules=False` for disabled mode, and add
`--config=aarch64` on native ARM64. Packaging uses `sonic_deploy_tar` with
`force_debug_build = True`, which applies `--copt=-g`, `--strip=never`, and a
linker build ID to the package inputs while retaining the selected compilation
mode and optimization settings. It derives the runtime copy and detached debug
file from the same linked ELF. The runtime package has its DWARF sections removed;
the separate archive stores them under `/usr/lib/debug/.build-id/`.

The package test validates the library filename, SONAME, symlinks, build IDs,
debug-link checksums, and GDB source-line lookup. It checks both the library and
`swssloglevel` for detached debug information. The symbol archive covers these
two files; the CLI and Python package archives have their own packaging paths.

The direct targets `//:libswsscommon`, `//:libswsscommon_shared`, and
`//:libswsscommon_consolidated.so` continue to use the caller's compilation
settings. To build the raw shared library with embedded debug information, use
`--copt=-g --strip=never`. The package targets apply their debug settings through
the deployment transition, so those flags are unnecessary for the package command
above.

## YANG configuration

`--//tools/bazel:yang_modules=True` is the default. It generates `cfg_schema.h`
from the production model set, compiles `DefaultValueProvider` and both decorator
table classes with libyang, and exposes those classes and generated table-name
constants through the Python bindings. The disabled setting selects the minimal
schema stub and omits the YANG native sources, libyang dependency, and Python
API entries.

Registry module `sonic-yang-models` prepares the production model set with its
manifest, raw models, templates, and locked Python tools. Common's unchanged
`gen_cfg_schema.py` consumes that prepared directory through the registry's
`sonic-yang-mgmt` Python library. Schema generation runs on the execution
platform, including its Python 3.13 and libyang inputs; the library's native
dependencies follow the target platform. The generator declares its inputs and
requests network blocking, with package installer network access disabled.

The source revisions, libyang-Python patch provenance, and the difference from
the standalone Azure build inputs are recorded in the
[YANG input guide](../tools/bazel/yang/README.md). The public
`//tools/bazel:cfg_schema` label setting remains available for downstream Bazel
6 builds that supply a Make-generated header. Standalone Bazel 8 uses a selected
default header for the enabled and disabled modes.

Common CI checks the integration with these dependencies. The enabled C++
fixture loads models through the shared library; package tests check native
feature symbols and import and construct the enabled Python class from the
archive. Registry CI independently checks the model package, management
library, libyang runtime/package, and Python binding. PCRE2 and xxHash remain
static implementation dependencies of native libyang.

The Python package and Go consumer tests stage the same source-built libyang
and pinned Trixie runtime packages used by the build. The Go test exercises
wrapped value types and calls `Select` in the current shared library without
Redis. It checks that the current library is loaded and that hiredis and the
enabled mode's libyang come from the staged runtime; disabled mode must not load
libyang.

The Go binding target is included in CI. Its Redis-backed integration test,
`//goext:swsscommon_test`, is tagged `manual` and requires the expected Redis
endpoints and database configuration, matching Trixie native libraries, and a
loader configuration that can find them. Run that test separately in an
environment providing those dependencies and services.

## CodeQL C++ build

The C++ CodeQL job builds with Bazel inside a native AMD64 Debian Trixie
container. CodeQL records compilation and linkage by observing build processes,
so this job creates a fresh Bazel output base after CodeQL initialization, uses
local execution, and disables action caches. Bazelisk and repository download
caches remain available for tools and dependencies. See
[CodeQL and Bazel cache reuse](codeql-cache.md) for the measured cache boundary.

The build passes `--cxxopt=-nostdinc` so local C++ compilation uses the
toolchain's explicit GCC and Debian include paths while retaining Bazel's header
dependency checks.

The build covers the library, command-line tools, and generated Python SWIG
wrapper with YANG enabled. The manual `//tests:codeql_test_sources` target also
compiles all 49 legacy C++ test files, including the YANG fixture source, without
requiring Redis services.

## SWIG constant wrapping

SWIG 4.3 generates mutable `char *` variable wrappers for some C++
`static constexpr const char *` constants, which produces const-correctness
errors when GCC compiles the generated code. The directives in
`pyext/swsscommon.i` handle these constants before their headers are included:
Python uses `%naturalvar` to retain class attributes with value-style wrapping,
while Go uses explicit `%extend` getters and `%ignore` for the problematic
automatic wrappers. `%naturalvar` alone does not correct the Go-generated code.
