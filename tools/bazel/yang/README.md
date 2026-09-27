# Bazel YANG inputs

This package supplies the model preparation and schema-generation tools for the
standalone Bazel 8 build. The public build option and header override remain in
`//tools/bazel`.

## Source contract

`MODULE.bazel` pins the following inputs with archive hashes:

- The models, templates, `sonic_yang` Python modules, and libyang-Python patch
  series come from `securely1g/sonic-buildimage` revision
  [`9ab452d22773c41783092ae3bd6c206d6c257c8d`](https://github.com/securely1g/sonic-buildimage/tree/9ab452d22773c41783092ae3bd6c206d6c257c8d).
- The production
  [`src/libyang3-py3/Makefile`](https://github.com/securely1g/sonic-buildimage/blob/9ab452d22773c41783092ae3bd6c206d6c257c8d/src/libyang3-py3/Makefile)
  clones CESNET/libyang-python at `v3.1.0` and applies the four patches listed
  in `src/libyang3-py3/patch/series`. Bazel uses that same release and series.
- The shared SONiC Bazel registry supplies Debian Trixie `libyang3` and
  `libyang-dev` version `3.12.2-1`. The CFFI binding is compiled against those
  declared headers and libraries. Production Make also starts from libyang
  `3.12.2` and adds its `LYD_VALIDATE_NOEXTDEPS` patch. The swsscommon schema
  generator and default-value provider do not use that additional flag; schema
  generation and the shared-library fixture test exercise their compatibility
  with the selected Debian library.
- `requirements.in` and `requirements_lock.txt` pin the Python dependencies
  for the tools, including the setup requirements used by the model package.

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

The CFFI extension uses `current_py_cc_headers` from the selected Python
toolchain. It resolves Python symbols from that interpreter and has no dynamic
dependency on a second Python runtime. The pinned GCC toolchain normally adds
runtime search paths for the installed filesystem. The local
[`sonic-build-infra-optional-runtime-paths.patch`](patches/sonic-build-infra-optional-runtime-paths.patch)
keeps those paths enabled by default and lets this private extension disable
them. Its execution dependency adapter also removes installed-filesystem paths
contributed by the distro library rules.

The extension uses `DT_RPATH` so Bazel's library search paths also apply to
libyang's indirect libxxhash dependency. The runtime test compares the loaded
libyang and libxxhash files with the declared runfiles tree. The deployed
swsscommon Python package keeps its target-platform Python dependency.

The actions pass an offline package-installer environment and request network
blocking. Python dependencies and the CFFI source generator are declared Bazel
tools; model and template files are declared action inputs. The source archives
and locked Python wheels are downloaded during dependency resolution.

The shared SWIG rule at the pinned infrastructure revision has no preprocessor
options attribute. `../swig.bzl` provides a small declared interface adapter
for the feature define. The original interface is also an explicit SWIG input.
The adapter supports defines and undefines so a caller can select architecture
preprocessor settings when the shared rule still supplies a default.

## Strict patch application

`patches/0003-pr132-backlinks.patch` is the production patch with its context
refreshed after `0002-pr134-json-string-datatypes.patch`. The original third
patch expects `json_null` immediately before a closing parenthesis; the second
patch inserts `json_string_datatypes` there. The local copy adds that unchanged
context line and updates the hunk coordinates. Its semantic additions and
deletions are unchanged, and the source URL is recorded in the patch.

For this pinned series, comparison against the original production Quilt
application found the same 54 file paths and identical source bytes. The only
byte difference was a final newline in `debian/watch`: Bazel's native patcher
adds it where the original patch records no final newline. That Debian metadata
file is not an input to the CFFI binding build.

## Runtime model dependency

`yang_models_dependency_pkg.tar` installs the prepared model tree at
`/usr/local/yang-models`. This is the runtime payload supplied by the production
`sonic_yang_models` wheel, packaged separately from swsscommon's runtime tar.
Enabled deployments install it alongside `libswsscommon_pkg.tar` and the native
runtime libraries. The package test compares every archived model byte with the
prepared tree and checks the installed path.

When updating these pins, compare the prepared model tree and generated header
with the Make path and run the enabled and disabled target lists in
[`docs/bazel.md`](../../../docs/bazel.md).
