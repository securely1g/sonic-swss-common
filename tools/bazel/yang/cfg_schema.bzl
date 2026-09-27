"""Execution-side model preparation and cfg_schema.h generation."""

_OFFLINE_PYTHON_ENV = {
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_INDEX": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
}

def _prepared_yang_models_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add("--setup", ctx.file.setup.path)
    args.add("--readme", ctx.file.readme.path)
    args.add_all(ctx.files.sources, before_each = "--source")
    args.add("--output", output.path)
    ctx.actions.run(
        executable = ctx.executable._preparer,
        arguments = [args],
        inputs = depset([ctx.file.setup, ctx.file.readme] + ctx.files.sources),
        outputs = [output],
        tools = [ctx.attr._preparer[DefaultInfo].files_to_run],
        env = _OFFLINE_PYTHON_ENV,
        execution_requirements = {"block-network": "1"},
        mnemonic = "PrepareYangModels",
        progress_message = "Preparing production YANG models for %{label}",
    )
    return [DefaultInfo(files = depset([output]))]

prepared_yang_models = rule(
    implementation = _prepared_yang_models_impl,
    attrs = {
        "readme": attr.label(allow_single_file = True, mandatory = True),
        "setup": attr.label(allow_single_file = [".py"], mandatory = True),
        "sources": attr.label_list(allow_files = True, mandatory = True),
        "_preparer": attr.label(
            default = Label("//tools/bazel/yang:prepare_yang_models"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _cfg_schema_impl(ctx):
    args = ctx.actions.args()
    args.add("-d", ctx.file.models.path)
    args.add("-o", ctx.outputs.out.path)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = [ctx.file.models],
        outputs = [ctx.outputs.out],
        tools = [ctx.attr._generator[DefaultInfo].files_to_run],
        env = _OFFLINE_PYTHON_ENV,
        execution_requirements = {"block-network": "1"},
        mnemonic = "GenCfgSchema",
        progress_message = "Generating cfg_schema.h for %{label}",
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]))]

cfg_schema = rule(
    implementation = _cfg_schema_impl,
    attrs = {
        "models": attr.label(allow_single_file = True, mandatory = True),
        "out": attr.output(mandatory = True),
        "_generator": attr.label(
            default = Label("//tools/bazel/yang:gen_cfg_schema"),
            executable = True,
            cfg = "exec",
        ),
    },
)
