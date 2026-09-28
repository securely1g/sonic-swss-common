# Standalone Bazel build

The standalone build uses Bazel 8.5.1, selected by `.bazelversion`. Install
[Bazelisk](https://bazel.build/install/bazelisk) and invoke it as `bazel`.
Dependencies come from the configured SONiC Bazel registry and the Bazel Central
Registry. Bazel downloads the native GCC 14.2 toolchains, the LLVM 20.1.4 ARMHF
toolchain, and the Debian package inputs.

## Environment

Use Debian Trixie userspace on the execution host. The supported configurations
are:

| Configuration | Execution host in CI | Target and compiler |
| --- | --- | --- |
| Default | AMD64 | AMD64, GCC 14.2 |
| `--config=aarch64` | ARM64 | ARM64, GCC 14.2 |
| `--config=armhf` | ARM64 | ARMv7 hard-float, LLVM 20.1.4 |

The native GCC toolchains require the execution and target CPUs to match. The
ARMHF configuration runs Bazel and SWIG on ARM64 and uses LLVM for cross
compilation. Its target ABI is Debian `arm-linux-gnueabihf`: ARMv7-A,
VFPv3-D16, EABI5 hard-float, and `/lib/ld-linux-armhf.so.3`. It uses the same
Trixie glibc 2.41 and GCC 14 libstdc++/libgcc runtime packages as the shared
toolchain inputs. Target C/C++ compilation defines `_LARGEFILE_SOURCE`,
`_FILE_OFFSET_BITS=64`, and `_TIME_BITS=64` to match Debian Trixie's ARMHF
large file and time64 ABI. Go targets `linux/arm` with GOARM 7.

CI uses `debian:trixie-20260918` and installs these host tools. Native package
tests use `readelf` and `objcopy` from `binutils`, GDB, Python, and `tar`.

```sh
apt-get update
apt-get install -y --no-install-recommends \
  binutils build-essential ca-certificates gdb git python3 tar
```

For the ARMHF configuration on ARM64, also install the execution and inspection
dependencies:

```sh
apt-get install -y --no-install-recommends gdb-multiarch libxml2 qemu-user
```

The downloaded ARM64 LLVM linker requires `libxml2.so.2` from the host's
`libxml2` package. The package test uses the pinned LLVM `objcopy` and
`gdb-multiarch` to inspect ARM binaries.
QEMU executes the target C++, Go, and Python binaries while Bazel and build
tools continue to run on the ARM64 host.

## Build and test

Run the following from the repository root in Bash. CI uses these explicit
labels so a required output's platform incompatibility fails the invocation.

```bash
outputs=(
  //:libswsscommon
  //:libswsscommon_shared
  //:libswsscommon_consolidated.so
  //:swssloglevel
  //dist:libswsscommon_pkg
  //dist:libswsscommon_pkg.debug_symbols
  //dist:sonic-db-cli_pkg
  //pyext:swsscommon_pkg
  //goext:swsscommon
)
compiled_tests=(
  //tests:status_code_util_test
  //tests:saiaclschema_ut
  //tests:notification_queue_ut
  //tests:interface_ut
  //tests:vrf_ut
  //tests:shared_library_runtime_test
)
package_tests=(
  //dist:libswsscommon_package_test
  //goext:swsscommon_runtime_test
)

# Native AMD64
bazel test --//tools/bazel:yang_modules=False --test_output=errors \
  "${outputs[@]}" "${compiled_tests[@]}" "${package_tests[@]}"

# Native ARM64
bazel test --config=aarch64 --//tools/bazel:yang_modules=False --test_output=errors \
  "${outputs[@]}" "${compiled_tests[@]}" "${package_tests[@]}"

# ARMHF packages and host shell tests, from an ARM64 host with qemu-user
bazel test --config=armhf --//tools/bazel:yang_modules=False --jobs=4 --test_output=errors \
  "${outputs[@]}" "${package_tests[@]}" //tools/bazel/armhf:python_runtime_test

# ARMHF compiled tests, using the scoped QEMU runner
bazel test --config=armhf --//tools/bazel:yang_modules=False --jobs=4 --test_output=errors \
  --run_under=//tools/bazel/armhf:qemu_run_under "${compiled_tests[@]}" \
  //tools/bazel/armhf:abi_runtime_test
```

`bazel test` builds the listed libraries, binaries, bindings, and packages as
well as running the listed tests. For ARMHF, the package inspection remains a
host shell test. The scoped runner extracts the pinned target runtime and
verifies that each compiled test is an ARM ELF32 hard-float binary before starting
QEMU. The Go and Python host tests invoke QEMU for their target binaries. The
Python test extracts the target runtime and package, verifies their ELF
architecture, then imports and calls the binding using the ARMHF interpreter.
The ARMHF ABI test checks 64-bit `off_t` and `time_t`, reads and writes beyond
2 GiB in a sparse file, preserves post-2038 timestamps, and checks for errors
while reading directories.

CI also checks Bazel file formatting on the native jobs:

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
- `sonic-swss-common-no-yang-ARMHF`

Each download contains these four archives:

- `libswsscommon_pkg.tar`: C++ runtime library, `swssloglevel`, Lua files, and
  database configuration.
- `libswsscommon_pkg.debug_symbols.tar`: detached debug information for the
  runtime library and `swssloglevel` in `libswsscommon_pkg.tar`.
- `sonic-db-cli_pkg.tar`: database CLI.
- `swsscommon_pkg.tar.gz`: Python bindings.

These target Debian Trixie using the no-YANG configuration described below.
The runtime library is installed under the target multiarch directory:
`x86_64-linux-gnu`, `aarch64-linux-gnu`, or `arm-linux-gnueabihf`. GitHub requires
you to sign in to download workflow artifacts.

## Debug symbols

Build the runtime package and its matching detached symbols together:

```sh
bazel build --//tools/bazel:yang_modules=False \
  //dist:libswsscommon_pkg //dist:libswsscommon_pkg.debug_symbols
```

Add `--config=aarch64` on native ARM64 or `--config=armhf` for the ARMHF target.
Packaging uses `sonic_deploy_tar` with
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

The Go binding and a small consuming test are included in CI. The consuming
test links the cgo wrapper and calls wrapped value types and `Select` without
Redis. It checks that the current `libswsscommon` and staged hiredis library
were loaded, and the no-YANG test rejects loading libyang. A host wrapper stages
the pinned Trixie runtime libraries before starting the test. Ordinary Go
consumers still need matching native libraries and loader configuration. Its
Redis-backed integration test,
`//goext:swsscommon_test`, is tagged `manual` and requires the expected Redis
endpoints and database configuration. Run that test separately in an environment
providing those services and the matching native runtime libraries; the
standalone CI target list does not start Redis.

## ARMHF integration boundary

The ARMHF setup is for the standalone Bazel 8.5.1 entry point. `MODULE.bazel`
adds ARMHF to the shared pinned Trixie package sources, selects the cumulative
`rules_distroless` `0.9.4.sonic.2` release from the pinned securely1g registry,
and assembles the shared sysroot package
archives into the directory LLVM expects. The published shared GCC toolchains
currently cover native AMD64 and ARM64, so ARMHF uses LLVM while retaining the
Debian GCC 14 runtime libraries. The ARMHF configuration registers the pinned
LLVM inspection tools with the existing binutils toolchain interface so the
same debug-package rule can split ARM binaries.

`tools/bazel/armhf/swig.bzl` supplies the ARMHF binding action. It uses the same
pinned host SWIG and current header inputs as the shared generator, omits
`SWIGWORDSIZE64`, and uses `-intgosize 32` for Go. The native generator calls
keep their existing API, including WORKSPACE and local infra override use.
The ARMHF Go link selects lld and PIE mode so Go emits position-independent
objects for LLVM's PIE executable link; native paths retain bfd and their
existing Go link mode. The Go host wrapper selects its explicit QEMU mode for
ARMHF and disables rules_go's XML wrapper, which re-executes the binary, so its
single execution stays inside QEMU. Bazel still records the test's exit status
and log.
The ARMHF configuration omits GCC's numeric strict-aliasing warning flag. It
keeps `-Werror` but leaves the observed LLVM diagnostics for existing component
and dependency-header patterns as warnings; the exact list is in
`tools/bazel/flags.bzl`. Native warning policy is unchanged. ARMHF also disables
optional Python bytecode precompilation because no ARMHF rules_python interpreter
toolchain is registered. The Python archive retains its sources, which the
package test runs with the extracted Trixie ARMHF interpreter.

The shared Distroless release includes both protobuf `.inc` header support and
the ARMHF CPU mapping, so consumers do not need a root-only patch override. A
consuming root must configure the pinned registry because Bazel does not import
a dependency's `.bazelrc`. Top-level sonic-buildimage ARMHF integration must also
register the platform and toolchains and validate its own graph. This standalone
support does not claim that integration.

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
