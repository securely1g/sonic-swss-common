"""ARMHF binding generation while retaining the shared native SWIG API."""

load("//tools/bazel:bzlmod.bzl", "IS_BZLMOD")

def armhf_select(native_value, armhf_value):
    """Selects the ARMHF Bzlmod path without changing legacy WORKSPACE builds."""
    if not IS_BZLMOD:
        return native_value
    return select({
        "@platforms//cpu:armv7": armhf_value,
        "//conditions:default": native_value,
    })

def _armhf_swig_impl(ctx):
    swig_lib = ctx.files.swig_lib
    if len(swig_lib) != 1 or not swig_lib[0].is_directory:
        fail("ARMHF SWIG expects the directory produced by swig_lib_deb")

    # Match the shared rule's current hdrs-only calls and host SWIG tool. The
    # target-size differences are -intgosize 32 for Go and no SWIGWORDSIZE64.
    args = ctx.actions.args()
    if ctx.attr.language == "python":
        args.add_all(["-c++", "-python", "-Wall", "-keyword"])
    else:
        args.add_all(["-go", "-cgo", "-c++", "-intgosize", "32"])
    args.add_all(ctx.attr.defines, format_each = "-D%s")
    for directory in {header.dirname: True for header in ctx.files.hdrs}:
        args.add("-I" + directory)
    args.add("-o", ctx.outputs.wrapper_out.path)
    outputs = [ctx.outputs.wrapper_out, ctx.outputs.module_out]
    if ctx.attr.language == "go":
        if not ctx.outputs.header_out:
            fail("Go SWIG generation requires header_out")
        args.add("-oh", ctx.outputs.header_out.path)
        outputs.append(ctx.outputs.header_out)
    args.add("-outdir", ctx.outputs.module_out.dirname)
    args.add(ctx.file.interface.path)

    ctx.actions.run(
        executable = ctx.attr.swig[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = depset([ctx.file.interface] + ctx.files.hdrs + swig_lib),
        outputs = outputs,
        env = {"SWIG_LIB": swig_lib[0].path},
        mnemonic = "ArmhfSwigGen",
        progress_message = "Generating ARMHF SWIG bindings for %{label}",
    )
    return [DefaultInfo(files = depset(outputs))]

_armhf_swig = rule(
    implementation = _armhf_swig_impl,
    attrs = {
        "language": attr.string(mandatory = True, values = ["python", "go"]),
        "interface": attr.label(mandatory = True, allow_single_file = [".i"]),
        "defines": attr.string_list(),
        "hdrs": attr.label_list(allow_files = True),
        "swig_lib": attr.label(mandatory = True),
        "swig": attr.label(mandatory = True, executable = True, cfg = "exec"),
        "wrapper_out": attr.output(mandatory = True),
        "module_out": attr.output(mandatory = True),
        "header_out": attr.output(),
    },
)

def armhf_swig(name, language, interface, hdrs, swig_lib, wrapper_out, module_out, header_out = None, defines = []):
    """Declares only the ARMHF alternative; native calls keep the shared rule."""
    if not IS_BZLMOD:
        return
    _armhf_swig(
        name = name,
        language = language,
        interface = interface,
        defines = defines,
        hdrs = hdrs,
        swig_lib = swig_lib,
        swig = "@swig//:swig",
        wrapper_out = wrapper_out,
        module_out = module_out,
        header_out = header_out,
        target_compatible_with = ["@platforms//cpu:armv7"],
    )
