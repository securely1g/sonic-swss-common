"""Compiler and linker flags shared across the sonic-swss-common build."""

load(":bzlmod.bzl", "IS_BZLMOD")

# CXXFLAGS that we need for Bazel specifically. Not present in the Makefile
CXXFLAGS_COMMON_BAZEL = [
    # TODO(bazel-ready): rules_distroless introduces a bunch of include directories that don't exist
    # so we need to disable that warning or else -Werror will fail the build.
    "-Wno-missing-include-dirs",
]

# CFLAGS_COMMON from configure.ac, with the compiler-specific warning policy below.
CXXFLAGS_COMMON_MAKEFILE = [
    "-ansi",
    "-fPIC",
    "-std=c++14",
    "-Wall",
    "-Wcast-align",
    "-Wcast-qual",
    "-Wconversion",
    "-Wdisabled-optimization",
    "-Werror",
    "-Wextra",
    "-Wfloat-equal",
    "-Wformat=2",
    "-Wformat-nonliteral",
    "-Wformat-security",
    "-Wformat-y2k",
    "-Wimport",
    "-Winit-self",
    "-Winvalid-pch",
    "-Wlong-long",
    "-Wmissing-field-initializers",
    "-Wmissing-format-attribute",
    "-Wmissing-include-dirs",
    "-Wmissing-noreturn",
    "-Wno-aggregate-return",
    "-Wno-padded",
    "-Wno-switch-enum",
    "-Wno-unused-parameter",
    "-Wpacked",
    "-Wpointer-arith",
    "-Wredundant-decls",
    "-Wshadow",
    "-Wstack-protector",
    "-Wswitch",
    "-Wswitch-default",
    "-Wunreachable-code",
    "-Wunused",
    "-Wvariadic-macros",
    "-Wno-write-strings",
    "-Wno-missing-format-attribute",
    "-Wno-long-long",
    "-fstack-protector-strong",
]

# LLVM rejects GCC's numeric strict-aliasing warning level and diagnoses these
# existing component and dependency-header patterns differently. Keep them as
# warnings for ARMHF while retaining -Werror for every other diagnostic.
COMPILER_WARNING_FLAGS = select({
    "@platforms//cpu:armv7": [
        "-Wno-error=implicit-int-conversion",
        "-Wno-error=inconsistent-missing-override",
        "-Wno-error=packed",
        "-Wno-error=shorten-64-to-32",
        "-Wno-error=string-plus-int",
        "-Wno-error=switch-default",
        "-Wno-error=variadic-macros",
    ],
    "//conditions:default": ["-Wstrict-aliasing=3"],
}) if IS_BZLMOD else ["-Wstrict-aliasing=3"]

CXXFLAGS_COMMON = CXXFLAGS_COMMON_MAKEFILE + CXXFLAGS_COMMON_BAZEL + COMPILER_WARNING_FLAGS

DBGFLAGS = select({
    "@sonic_build_infra//:debug_enabled": [
        "-ggdb",
        "-gdwarf-5",
    ],
    "//conditions:default": ["-g"],
    # TODO(bazel-ready): Remove when we only have to support Bazel 8+.
}) if IS_BZLMOD else ["-g"]
