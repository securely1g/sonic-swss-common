"""Component platform selection and shared SWIG declarations."""

load("//tools/bazel:bzlmod.bzl", "IS_BZLMOD")

def armhf_select(native_value, armhf_value):
    """Selects the ARMHF Bzlmod path without changing legacy WORKSPACE builds."""
    if not IS_BZLMOD:
        return native_value
    return select({
        "@platforms//cpu:armv7": armhf_value,
        "//conditions:default": native_value,
    })

def armhf_swig(generator, **kwargs):
    """Declares shared 32-bit bindings without changing legacy WORKSPACE builds."""
    if IS_BZLMOD:
        generator(
            wordsize = 32,
            target_compatible_with = ["@platforms//cpu:armv7"],
            **kwargs
        )
