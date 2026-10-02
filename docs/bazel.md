# Standalone Bazel build

The shared [`.bazelrc`](../.bazelrc) uses the maintained SONiC registry `main`
branch followed by Bazel Central Registry, including CI and the commands below.
Module versions, source checksums, package locks and toolchain inputs remain
pinned. CI retains its generated module lock as resolution evidence.

The standalone build uses Bazel 8.5.1, selected by `.bazelversion`. Install
[Bazelisk](https://bazel.build/install/bazelisk) and invoke it as `bazel`.
Dependencies come from the configured SONiC Bazel registry and the Bazel Central
Registry; the native GCC 14.2 toolchain and Debian package inputs are downloaded
by Bazel.

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

## Rust C API bindings

`//crates/swss-common:bindings_dir` generates `bindings.rs` from every header in
`common/c-api` and places it in the directory layout expected by Cargo's
`OUT_DIR`. The header inventory comes from the same Bazel filegroup used by the
native Common library, so adding a C API header also updates the binding input.
The target uses `--with-derive-partialeq`, matching `build.rs`.

A `crate_universe` consumer can disable Common's build script, provide this
directory as `compile_data` and `OUT_DIR`, and depend on
`//:libswsscommon_shared`. The Rust crate source and the native Common module
must select the same source revision. The consuming root must register Rust and
`rules_rust_bindgen` toolchains; Common's standalone toolchains are development
dependencies and do not override a consumer's choices.

Standalone CI generates bindings on native AMD64 and ARM64 in both YANG modes.
It selects Rust 1.90.0, LLVM 17.0.6, and the bindgen 0.71.1 executable supplied
by `rules_rust_bindgen` 0.74.0. Cargo's unchanged `build.rs` uses bindgen 0.70.1.
The Bazel path is validated through downstream Rust compilation; it does not
claim byte-for-byte equality with Cargo's generated file. This target generates
bindings only and does not package or publish the Rust crate.

## Build artifacts

Each successful native build uploads its package archives. Open the repository's
**Actions** tab, select a successful **CodeQL** workflow run for AMD64 or
**Bazel** workflow run for ARM64, and download the
artifact for your architecture and feature mode from the **Artifacts** section:

- `sonic-swss-common-yang-AMD64`
- `sonic-swss-common-yang-ARM64`
- `sonic-swss-common-no-yang-AMD64`
- `sonic-swss-common-no-yang-ARM64`

Every download contains these four archives:

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
container. It also runs AMD64 tests and produces the packages listed above;
the separate Bazel workflow validates ARM64. Manual dispatch follows the same
division. CodeQL records compilation and linkage by observing build processes,
so this job creates a fresh Bazel output base after CodeQL initialization and
uses local execution. Only outputs produced inside this traced job can be reused.
Disk/remote output caches and remote execution remain disabled. Bazelisk and
repository download caches remain available for tools and dependencies. See
[CodeQL and Bazel cache reuse](codeql-cache.md) for the measured cache boundary.

The build passes `--cxxopt=-nostdinc` so local C++ compilation uses the
toolchain's explicit GCC and Debian include paths while retaining Bazel's header
dependency checks.

The build covers the library, command-line tools, and generated Python SWIG
wrapper with YANG enabled. The manual `//tests:codeql_test_sources` target also
compiles all 49 legacy C++ test files, including the YANG fixture source, without
requiring Redis services.

For YANG, the job first builds the union of required native outputs,
packages, test executables and analysis targets under CodeQL. It then executes the tests using those outputs,
with test-result caching disabled. Execution logs must show no build actions in
this second phase, and every required test must execute and pass. Matching
runtime/debug archives are copied before changing feature configuration.
Distinct YANG, debug and linkage configurations still require distinct outputs.

Analysis and its source archive are finalized before switching to no-YANG:
generated files can share output paths across feature configurations. The
no-YANG build and tests then run without tracing, preserving the previous
YANG-only analysis scope and reusing common outputs from the private build.

The `sonic-swss-common-codeql-cpp` artifact retains build profiles, execution
logs, generated module resolution, package hashes and source-extraction evidence.
The existing `Bazel (AMD64)` check requires the combined build/test/analysis job
to succeed; a failed, cancelled or skipped job cannot satisfy it.

## SWIG constant wrapping

SWIG 4.3 generates mutable `char *` variable wrappers for some C++
`static constexpr const char *` constants, which produces const-correctness
errors when GCC compiles the generated code. The directives in
`pyext/swsscommon.i` handle these constants before their headers are included:
Python uses `%naturalvar` to retain class attributes with value-style wrapping,
while Go uses explicit `%extend` getters and `%ignore` for the problematic
automatic wrappers. `%naturalvar` alone does not correct the Go-generated code.
