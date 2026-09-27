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
uses `readelf` from `binutils` and `tar`.

```sh
apt-get update
apt-get install -y --no-install-recommends \
  binutils build-essential ca-certificates git python3 tar
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

Each download contains these three archives:

- `libswsscommon_pkg.tar`: C++ runtime library, `swssloglevel`, Lua files, and
  database configuration.
- `sonic-db-cli_pkg.tar`: database CLI.
- `swsscommon_pkg.tar.gz`: Python bindings.

These are native Debian Trixie builds using the no-YANG configuration described
below. GitHub requires you to sign in to download workflow artifacts.

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

## SWIG constant wrapping

SWIG 4.3 generates mutable `char *` variable wrappers for some C++
`static constexpr const char *` constants, which produces const-correctness
errors when GCC compiles the generated code. The directives in
`pyext/swsscommon.i` handle these constants before their headers are included:
Python uses `%naturalvar` to retain class attributes with value-style wrapping,
while Go uses explicit `%extend` getters and `%ignore` for the problematic
automatic wrappers. `%naturalvar` alone does not correct the Go-generated code.
