# Debian packages built from Common's Bazel outputs

```sh
bazel build \
  //debian:libswsscommon_deb \
  //debian:python3_swsscommon_deb \
  //debian:sonic_db_cli_deb
```

The three packages keep the original `1.0.0` version and their source-owned
`control`, `changelog`, `.install` and `conffiles` inputs. Debian's `dh_install`
selects files from the existing source-built runtime TARs. For example, the
runtime `libswsscommon` package includes `libswsscommon.so.0`, while the unversioned
`libswsscommon.so` development link remains outside that package.

The original `dh_fixperms` helper applies Debian's normal file permissions.
For example, a checkout's group-writable `database_config.json` becomes mode
`0644`, while executables and directories use `0755`. It runs in Debian's
supported rootless mode; the archive rule assigns root ownership.

`dh_makeshlibs` generates the library registration and `ldconfig` trigger, and
`dh_installdeb` records the original database configuration file as a conffile.
The shared metadata rule computes dependencies from the actual ELF files and
matching library metadata, including the source-built libnl and libyang packages.
The generated Common symbols are also available through
`//debian:libswsscommon_metadata` for downstream Debian producers.

The existing runtime and debug TAR interfaces remain available. The Debian
producer adds the native changelog and keeps generated file times deterministic.

## Check the resulting archives

```sh
bazel build \
  //debian:libswsscommon_check \
  //debian:python3_swsscommon_check \
  //debian:sonic_db_cli_check
```

These checks compare each actual DEB with its generated data and control files,
including ownership, modes, checksums, conffiles, library metadata and native ELF
architecture. Installation, configuration, upgrade and removal must also be
checked with the real package manager and all source-built dependencies present.

## Check installation

Native AMD64 and ARM64 CI builds both YANG configurations, retains the three
DEBs, and installs them in separate fresh Debian Trixie containers. The YANG
artifact also includes the source-built libyang3 DEB required by its ELF files.
`tools/bazel/check_debian_install.sh` checks Python import, CLI startup, library
registration, configuration preservation, reinstall, removal and purge with
normal apt/dpkg commands. It refuses to run outside the disposable test container.

The original package version is always `1.0.0`, so this validates replacement
with the same version. It does not claim a transition between two different
release versions. A later versioned release needs its own upgrade baseline.

While the producer prerequisites are Draft, CI selects the reviewed
`codex/source-debian-runtime-permissions` registry branch and records its exact
revision. The normal `.bazelrc` continues to use registry `main`. Before making
this PR ready, replace the temporary CI registry selection with main and restore
the pull-request branch filter to master.
