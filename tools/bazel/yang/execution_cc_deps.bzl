"""C++ dependencies for the private YANG execution tool."""

load("@rules_cc//cc:defs.bzl", "CcInfo", "cc_common")

def _execution_cc_deps_impl(ctx):
    source = ctx.attr.dep[CcInfo]
    linker_inputs = []
    for linker_input in source.linking_context.linker_inputs.to_list():
        # The distro rules add paths for the installed target filesystem. They
        # are inappropriate for an execution tool: the loader must use Bazel's
        # declared library runfiles before any ambient host directories.
        flags = [
            flag
            for flag in linker_input.user_link_flags
            if not flag.startswith("-Wl,-rpath=/") and not flag.startswith("-Wl,-rpath,/")
        ]
        linker_inputs.append(cc_common.create_linker_input(
            owner = linker_input.owner,
            libraries = depset(linker_input.libraries),
            additional_inputs = depset(linker_input.additional_inputs),
            user_link_flags = depset(flags),
        ))

    return [CcInfo(
        compilation_context = source.compilation_context,
        linking_context = cc_common.create_linking_context(
            linker_inputs = depset(linker_inputs),
        ),
    )]

execution_cc_deps = rule(
    implementation = _execution_cc_deps_impl,
    attrs = {
        "dep": attr.label(mandatory = True, providers = [CcInfo]),
    },
)
