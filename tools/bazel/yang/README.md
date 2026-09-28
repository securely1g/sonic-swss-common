# Bazel YANG inputs

This package supplies the model preparation and schema-generation tools for the
standalone Bazel 8 build. The public build option and header override remain in
`//tools/bazel`.

## Source contract

`MODULE.bazel` pins the following inputs with archive hashes:

- The models, templates, and `sonic_yang` Python modules come from
  `securely1g/sonic-buildimage` revision
  [`9ab452d22773c41783092ae3bd6c206d6c257c8d`](https://github.com/securely1g/sonic-buildimage/tree/9ab452d22773c41783092ae3bd6c206d6c257c8d).
- The shared SONiC Bazel registry supplies `libyang-python` module
  `3.1.0-sonic.1`. It builds CESNET/libyang-python `v3.1.0` with the same four
  patches as the production
  [`src/libyang3-py3/Makefile`](https://github.com/securely1g/sonic-buildimage/blob/9ab452d22773c41783092ae3bd6c206d6c257c8d/src/libyang3-py3/Makefile)
  and owns CFFI source generation, the Python library, and binding tests. Its
  [module README](https://github.com/securely1g/sonic-bazel-registry/blob/2a44eca5ae4437528bc5862589839b7b3724b8e0/modules/libyang-python/3.1.0-sonic.1/README.md)
  records patch provenance and strict patch application.
- The shared SONiC Bazel registry supplies source-built `libyang` module
  `3.12.2.sonic.1`. It compiles libyang `3.12.2` with the production
  `LYD_VALIDATE_NOEXTDEPS` patch from that same buildimage revision and enables
  large-file support. Its PCRE2 `10.45` and xxHash `0.8.3` dependencies are
  source-built and statically linked into `libyang.so.3`; no distro libyang or
  separate libxxhash shared object is used. Public headers and the CFFI binding
  resolve the same module in their respective target/execution configurations.
- `requirements.in` and `requirements_lock.txt` pin the remaining Python
  dependencies for schema and model preparation, including the setup
  requirements used by the model package.

The standalone Azure build uses a different Python binding input:
[`build-env/packages/base.yaml`](../../../build-env/packages/base.yaml) installs
`libyang==3.3.0` from pip after installing native libyang packages. The Bazel
generator follows the production buildimage `v3.1.0` plus patches contract.

## Preparation and execution

`prepared_yang_models` declares the model package's `setup.py`, `README.rst`,
raw YANG files, and Jinja templates as inputs. Its execution-side Python tool
copies those inputs into an isolated directory and runs the existing
`setup.py build_py` command. That command validates its explicit model manifest
and renders the `py` and `cvl` template variants. The action publishes the
`yang-models` directory containing the `py` variant used by the model wheel.

`cfg_schema` runs this repository's unchanged `gen_cfg_schema.py` against that
prepared directory. Its Python 3.13 runtime, Python dependencies, CFFI binding,
and native libyang dependencies all use Bazel's execution configuration. The
compiled swsscommon library uses the target configuration. This separation
keeps schema generation independent of the target CPU.

The registry binding uses `current_py_cc_headers` from the selected Python
toolchain. It resolves Python symbols from that interpreter and has no dynamic
dependency on a second Python runtime. Its private CFFI extension disables the
shared `sonic_installed_runtime_paths` feature so the loader uses declared Bazel
runfiles. The pinned infrastructure revision keeps those installed filesystem
paths enabled by default for target outputs. The module runtime test checks the
loaded extension, CFFI backend, and native libyang against the declared runfiles;
the upstream binding suite covers its schema and data APIs. PCRE2 and xxHash are
static implementation dependencies, so they do not need separate shared-library
runfiles. The deployed swsscommon Python package keeps its target-platform
Python dependency.

The actions pass an offline package-installer environment and request network
blocking. Python dependencies and the CFFI source generator are declared Bazel
tools; model and template files are declared action inputs. The source archives
and locked Python wheels are downloaded during dependency resolution.

The shared SWIG rule at the pinned infrastructure revision has no preprocessor
options attribute. `../swig.bzl` provides a small declared interface adapter
for the feature define. The original interface is also an explicit SWIG input.
The adapter supports defines and undefines so a caller can select architecture
preprocessor settings when the shared rule still supplies a default.

## Runtime model dependency

`yang_models_dependency_pkg.tar` installs the prepared model tree at
`/usr/local/yang-models`. This is the runtime payload supplied by the production
`sonic_yang_models` wheel, packaged separately from swsscommon's runtime tar.
Enabled deployments install it alongside `libswsscommon_pkg.tar`,
`libyang_dependency_pkg.tar`, and the remaining Trixie runtime libraries.
The libyang dependency package and `libyang_dependency_pkg.debug_symbols.tar`
come from the same linked ELF through the shared debug-packaging rule.
The model package test compares every archived model byte with the prepared
tree and checks the installed path.

When updating these pins, compare the prepared model tree and generated header
with the Make path and run the enabled and disabled target lists in
[`docs/bazel.md`](../../../docs/bazel.md).

The staged Python/Go package tests preload the exact extracted libyang SONAME.
Bazel's embedded `DT_RPATH` would otherwise take precedence over
`LD_LIBRARY_PATH` and could select the build-tree copy. The tests retain their
loaded-path checks, so this validates the deployed payload explicitly.
