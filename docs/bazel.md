# Standalone Bazel build

The standalone build uses Bazel 8.5.1, selected by `.bazelversion`. Install
[Bazelisk](https://bazel.build/install/bazelisk) and invoke it as `bazel`.
Dependencies come from the configured SONiC Bazel registry and the Bazel Central
Registry; the native GCC 14.2 toolchain and Debian package inputs are downloaded
by Bazel.

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

## Build artifacts

Each successful Bazel job uploads its package archives. Open the repository's
**Actions** tab, select a successful **Bazel** workflow run, and download the
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

## ARMHF build and test

ARMHF CI runs both YANG modes. From the Trixie host described above, use the
explicit native target lists above with `--config=armhf`, but replace the native
Python package test with `//tools/bazel/armhf:python_runtime_test`. Run C++ tests
with `--run_under=//tools/bazel/armhf:qemu_run_under`; include
`//tools/bazel/armhf:abi_runtime_test`. Run `//tests:defaultvalueprovider_ut`
without `--run_under`: its host fixture wrapper invokes the scoped QEMU runner.
The package/debug, Go, and Python tests also own their host wrappers and run
without a global `--run_under`.

Each mode uploads `sonic-swss-common-<yang|no-yang>-ARMHF` with the same four
Common archives as the native jobs. Enabled deployment additionally needs the
model payload and source-built libyang runtime for ARMHF. The existing registry
hosted artifacts cover native CPUs; Common's ARMHF tests build and stage the
dependency locally and do not publish its package as a Common artifact.

## ARMHF shared dependencies

The shared `debian_sysroot` repository rule combines pinned target packages
to form the directory LLVM expects. The published shared GCC toolchains
currently cover native AMD64 and ARM64, so ARMHF uses LLVM while retaining the
Debian GCC 14 runtime libraries. The ARMHF configuration registers the pinned
LLVM inspection tools with the existing binutils toolchain interface so the
same debug-package rule can split ARM binaries.

`sonic-build-infra` owns both native and ARMHF SWIG actions. The ARMHF calls
select `wordsize = 32`, omit `SWIGWORDSIZE64`, and use `-intgosize 32` for Go;
the Python call retains its YANG feature definitions. Common owns its binding
interfaces, output paths, packaging, and runtime tests. Its small
`tools/bazel/armhf/swig.bzl` adapter only declares the shared 32-bit generators
in Bzlmod builds and selects their outputs. The native calls retain the default
64-bit behavior, and WORKSPACE builds still skip the ARMHF declarations. Bzlmod
source overrides must provide the shared sysroot and SWIG APIs from the pinned
0.0.9 infrastructure release or a compatible newer version.
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

### Required architecture checks

The required `Bazel (AMD64)` check is a completion gate for both native matrix
jobs and the ARM64-hosted ARMHF job. Its native AMD64 compilation job is shown
as `Bazel native (AMD64)`; `Bazel (ARM64)` and `Bazel (ARMHF)` retain their names.
The gate runs even when a dependency fails or is skipped and passes only when
all supported jobs succeed, including both YANG configurations. This preserves
the required context used by older documentation branches while enforcing the
ARMHF coverage introduced here.
