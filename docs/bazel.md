# Standalone Bazel build

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

## Prepare Rust dependencies

Run preparation before the first Bazel command in a clean checkout. It generates
`Cargo.Bazel.lock` from the committed `Cargo.lock` using the pinned `rules_rust`
metadata generator and Rust toolchain. It verifies that the Cargo dependency
versions, sources and checksums remain unchanged and writes a receipt for the
build evidence.

```sh
python3 tools/bazel/prepare_rust.py --receipt artifacts/rust-preparation.json
# On a native ARM64 host, add --bazel-arg=--config=aarch64.
```

Rerun preparation after changing a Cargo manifest, Cargo lock, or Bazel Rust
configuration. Keep `Cargo.lock` in Git. The generated `Cargo.Bazel.lock` and
`MODULE.bazel.lock` are ignored and retained as CI artifacts.

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

## Rust library

`//crates/swss-common:swss_common` is the public Rust library. Common owns its
Rust sources, third-party crate dependencies, generated C API bindings, and the
link to `//:libswsscommon_shared`. The default library has Cargo's `async`
feature disabled. Its four existing unit tests run without a Redis server:

```sh
bazel test //crates/swss-common:swss_common_test
```

Add `--config=aarch64` on native ARM64. CI runs the library and unit tests on
native AMD64 and ARM64 in both YANG modes. Redis-backed Cargo integration tests
and the optional async feature are outside this Bazel test target.

Bazel consumers depend on `@sonic-swss-common//crates/swss-common:swss_common`.
A consumer that also resolves Common through `crate_universe` should use
`crate.annotation(override_target_lib = ...)` to select this public target and
disable Common's Cargo build script. This keeps Common's Rust and native code
at the same Bazel module revision. Common's public types implement Serde
traits, so consumers must also override their `serde` and `serde_core` crate
targets with Common's public `:serde` and `:serde_core` aliases. Both aliases
select version 1.0.228. This shares trait identity across the module boundary;
matching version strings alone does not share Bazel crate targets. Consumers
must register compatible Rust and bindgen toolchains because Common's
standalone toolchains are development dependencies.

`Cargo.lock` records the Cargo workspace resolution. The generated
`Cargo.Bazel.lock` describes `crate_universe`'s Bazel dependency graph. With
`rules_rust` 0.74.0 it must already exist before another Bazel module imports
Common. A consumer must prepare a writable checkout of the pinned Common source
before starting its own Bazel build, then select that prepared source with its
module override. For SWSS and sonic-buildimage, build preparation performs this
step before preparing their own Rust metadata. Preparing Common as a standalone
root also makes its declared development toolchains available to the generator.

After an intentional Cargo dependency update, review and commit `Cargo.lock`,
then rerun preparation. Do not commit generated Bazel lockfiles or copy them from
a different Common revision. The preparation receipt and metadata accompany CI
validation evidence; they do not replace the source pin and Cargo lock.

The underlying `//crates/swss-common:bindings_dir` target remains public. It
generates `bindings.rs` from every header in `common/c-api` and places it in the
directory layout expected by `OUT_DIR`. The header inventory comes from the
same Bazel filegroup used by the native Common library. The target uses
`--with-derive-partialeq`, matching `build.rs`.

Standalone builds select Rust 1.90.0, LLVM 17.0.6, and the bindgen 0.71.1
executable supplied by `rules_rust_bindgen` 0.74.0. Cargo's unchanged `build.rs`
uses bindgen 0.70.1. Tests validate compilation and behavior; they do not claim
byte-for-byte equality with Cargo's generated bindings. The Rust library is a
source dependency; it is not distributed as a precompiled Rust package.

## Build artifacts

Each successful Bazel job uploads its package archives. Open the repository's
**Actions** tab, select a successful **Bazel** workflow run, and download the
artifact for your architecture and feature mode from the **Artifacts** section:

- `sonic-swss-common-yang-AMD64`
- `sonic-swss-common-yang-ARM64`
- `sonic-swss-common-no-yang-AMD64`
- `sonic-swss-common-no-yang-ARM64`

The separate `sonic-swss-common-rust-AMD64` and
`sonic-swss-common-rust-ARM64` artifacts retain the Rust unit-test XML, logs, and Bazel
module lockfile for each YANG mode, the source `Cargo.lock`, generated
`Cargo.Bazel.lock`, preparation receipt, and the tested source commit and tree.

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
