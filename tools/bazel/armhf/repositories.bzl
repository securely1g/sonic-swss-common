"""Small adapter that assembles an immutable Debian ARMHF sysroot."""

def _armhf_sysroot_impl(rctx):
    # LLVM expects one sysroot directory. Reuse the archives resolved and
    # verified by the shared rules_distroless sysroot package set.
    for package in rctx.attr.packages:
        rctx.extract(rctx.path(package))

    # Trixie is usrmerged. These aliases also let the target dynamic loader
    # resolve its normal /lib paths when this tree is used with QEMU -L.
    for directory in ["bin", "lib", "sbin"]:
        if not rctx.path(directory).exists and rctx.path("usr/" + directory).exists:
            link = rctx.execute(["ln", "-s", "usr/" + directory, directory])
            if link.return_code != 0:
                fail("Could not create relative usrmerge alias {}: {}".format(
                    directory,
                    link.stderr,
                ))

    absolute_links = rctx.execute(["find", "usr", "-type", "l", "-lname", "/*", "-print"])
    if absolute_links.return_code != 0 or absolute_links.stdout:
        fail("The ARMHF sysroot contains unexpected absolute symlinks: {}{}".format(
            absolute_links.stdout,
            absolute_links.stderr,
        ))

    rctx.file(".sysroot-root", "Debian Trixie ARMHF sysroot from sonic-build-infra 0.0.4\n")
    rctx.file("BUILD.bazel", """\
package(default_visibility = ["//visibility:public"])
exports_files([".sysroot-root"])
filegroup(
    name = "files",
    srcs = glob(["usr/**", "bin/**", "lib/**", "sbin/**"]),
)
""")

armhf_sysroot = repository_rule(
    implementation = _armhf_sysroot_impl,
    attrs = {
        "packages": attr.label_list(allow_files = [".tar.xz"], mandatory = True),
    },
)
