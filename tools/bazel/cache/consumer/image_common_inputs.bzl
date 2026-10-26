"""Seed Common's extra image configurations without building an image.

The pinned buildimage revision b3d6c3971fa2c6ffedf457a9c18c734ba34b0efb
uses Common in two additional ways:

* docker-base-trixie/config renders sources.list.j2 with sonic-cfggen, whose
  Common Python binding is built in the execution configuration.
* The Rust supervisor listener links Common under its release transition.
  rules_rust also analyzes its dependency copies in the execution configuration.

The three release settings below mirror that revision's
src/sonic-supervisord-utilities-rs/bazel/release.bzl:
https://github.com/securely1g/sonic-buildimage/blob/b3d6c3971fa2c6ffedf457a9c18c734ba34b0efb/src/sonic-supervisord-utilities-rs/bazel/release.bzl

These test-fixture rules forward existing Common outputs. They do not assemble
containers, build the listener, or change any production build rule.
"""

_LTO = str(Label("@rules_rust//rust/settings:lto"))
_CODEGEN_UNITS = str(Label("@rules_rust//rust/settings:codegen_units"))

def _release_impl(_settings, _attr):
    return {
        "//command_line_option:compilation_mode": "opt",
        _LTO: "fat",
        _CODEGEN_UNITS: 1,
    }

_release = transition(
    implementation = _release_impl,
    inputs = [],
    outputs = ["//command_line_option:compilation_mode", _LTO, _CODEGEN_UNITS],
)

def _image_common_inputs_impl(ctx):
    python = ctx.attr.python_binding[DefaultInfo]
    release = ctx.attr.release_inputs[0][DefaultInfo]
    return [DefaultInfo(files = depset(transitive = [
        python.files,
        python.default_runfiles.files,
        python.data_runfiles.files,
        release.files,
    ]))]

image_common_inputs = rule(
    implementation = _image_common_inputs_impl,
    attrs = {
        "python_binding": attr.label(mandatory = True, cfg = "exec"),
        "release_inputs": attr.label(mandatory = True, cfg = _release),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)

def _release_inputs_impl(ctx):
    return [DefaultInfo(files = depset(transitive = [
        ctx.attr.library[DefaultInfo].files,
        ctx.attr.exec_library[DefaultInfo].files,
    ]))]

release_inputs = rule(
    implementation = _release_inputs_impl,
    attrs = {
        "library": attr.label(mandatory = True),
        # Apply exec after release, preserving the listener's Rust settings.
        "exec_library": attr.label(mandatory = True, cfg = "exec"),
    },
)
