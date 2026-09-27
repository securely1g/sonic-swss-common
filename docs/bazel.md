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

CI uses `debian:trixie-20260918` and installs these host tools. The package test
uses `readelf` and `objcopy` from `binutils`, GDB, Python, and `tar`.

```sh
apt-get update
apt-get install -y --no-install-recommends \
  binutils build-essential ca-certificates gdb git python3 tar
```

## Build and test

Run the following from the repository root in Bash. Define the explicit target
list once, then run the command matching the machine's native CPU. CI uses the
same flags and target list. Explicit labels make a required output's platform
incompatibility fail the invocation instead of being skipped by a wildcard.

```bash
targets=(
  //:libswsscommon
  //:libswsscommon_shared
  //:libswsscommon_consolidated.so
  //:swssloglevel
  //dist:libswsscommon_pkg
  //dist:libswsscommon_pkg.debug_symbols
  //dist:sonic-db-cli_pkg
  //pyext:swsscommon_pkg
  //goext:swsscommon
  //tests:status_code_util_test
  //tests:saiaclschema_ut
  //tests:notification_queue_ut
  //tests:interface_ut
  //tests:vrf_ut
  //tests:shared_library_runtime_test
  //dist:libswsscommon_package_test
)

# Native AMD64
bazel test --//tools/bazel:yang_modules=False --test_output=errors "${targets[@]}"

# Native ARM64
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

## Build artifacts

Each successful Bazel job uploads its package archives. Open the repository's
**Actions** tab, select a successful **Bazel** workflow run, and download the
artifact for your architecture from the **Artifacts** section:

- `sonic-swss-common-no-yang-AMD64`
- `sonic-swss-common-no-yang-ARM64`

Each download contains these four archives:

- `libswsscommon_pkg.tar`: C++ runtime library, `swssloglevel`, Lua files, and
  database configuration.
- `libswsscommon_pkg.debug_symbols.tar`: detached debug information for the
  runtime library and `swssloglevel` in `libswsscommon_pkg.tar`.
- `sonic-db-cli_pkg.tar`: database CLI.
- `swsscommon_pkg.tar.gz`: Python bindings.

These are native Debian Trixie builds using the no-YANG configuration described
below. GitHub requires you to sign in to download workflow artifacts.

## Debug symbols

Build the runtime package and its matching detached symbols together:

```sh
bazel build --//tools/bazel:yang_modules=False \
  //dist:libswsscommon_pkg //dist:libswsscommon_pkg.debug_symbols
```

Add `--config=aarch64` on native ARM64. Packaging uses `sonic_deploy_tar` with
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

## Supported configuration

The standalone Bazel build currently supports the no-YANG configuration. Every
build or test command above passes `--//tools/bazel:yang_modules=False` because
the setting defaults to enabled and YANG-driven `cfg_schema.h` generation is
not wired into Bazel yet. The supported configuration uses the minimal schema
stub and omits the YANG-dependent sources. YANG functionality remains outside
this CI coverage.

The Go binding target is included in CI. Its Redis-backed integration test,
`//goext:swsscommon_test`, is tagged `manual` and requires the expected Redis
endpoints and database configuration. Run that test separately in an environment
providing those services; the standalone CI target list does not start Redis.

## CodeQL C++ build

The C++ CodeQL job builds with Bazel inside a native AMD64 Debian Trixie
container. CodeQL observes compiler processes, so this job creates a fresh Bazel
output base after CodeQL initialization, uses local compilation, and disables
action caches. Bazelisk and repository download caches remain available for tools
and dependencies.
The build passes `--cxxopt=-nostdinc` so local C++ compilation uses the
toolchain's explicit GCC and Debian include paths while retaining Bazel's header
dependency checks.

The build covers the library, command-line tools, and generated Python SWIG
wrapper. The manual `//tests:codeql_test_sources` target also compiles the 48
legacy C++ test files that the package build exposed to CodeQL, without requiring
Redis services.

## SWIG constant wrapping

SWIG 4.3 generates mutable `char *` variable wrappers for some C++
`static constexpr const char *` constants, which produces const-correctness
errors when GCC compiles the generated code. The directives in
`pyext/swsscommon.i` handle these constants before their headers are included:
Python uses `%naturalvar` to retain class attributes with value-style wrapping,
while Go uses explicit `%extend` getters and `%ignore` for the problematic
automatic wrappers. `%naturalvar` alone does not correct the Go-generated code.
