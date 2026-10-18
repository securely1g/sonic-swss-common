#!/bin/bash
# Exercise the real package manager only in a disposable native Debian container.
set -euo pipefail
if [ ! -f /.dockerenv ] || [ "${SONIC_DEB_TEST_CONTAINER:-}" != 1 ]; then
    echo 'Run only in the disposable Debian package-test container.' >&2
    exit 1
fi
package_directory=$(realpath "$1")
architecture=$(dpkg --print-architecture)
case "$architecture" in amd64|arm64) ;; *) exit 1;; esac
export DEBIAN_FRONTEND=noninteractive
export PYTHONDONTWRITEBYTECODE=1
packages=(libswsscommon python3-swsscommon sonic-db-cli)
files=("$package_directory"/*.deb)
for package in "${packages[@]}"; do
    test -f "$package_directory/${package}_1.0.0_${architecture}.deb"
done
apt-get update
apt-get -y --no-install-recommends install python3 "${files[@]}"
for package in "${packages[@]}"; do
    test "$(dpkg-query -W -f='${Version}' "$package")" = 1.0.0
    test "$(dpkg-query -W -f='${Status}' "$package")" = 'install ok installed'
done
test -z "$(dpkg --audit)"
apt-get check
python3 -c 'from swsscommon import swsscommon; assert hasattr(swsscommon, "DBConnector")'
sonic-db-cli --help
ldconfig -p | grep -F libswsscommon.so.0
# The source .install manifest keeps developer linker names out of this package.
if dpkg-query -L libswsscommon | grep -E '/lib[^/]*\.so$'; then
    echo 'A developer linker name leaked into the runtime package' >&2
    exit 1
fi
configuration=/var/run/redis/sonic-db/database_config.json
test "$(stat -c %a "$configuration")" = 644
test "$(stat -c %a /usr/share/doc/libswsscommon/changelog.gz)" = 644
test "$(stat -c %a /usr/bin/sonic-db-cli)" = 755
dpkg-query -W -f='${Conffiles}' libswsscommon | grep -F "$configuration"
dpkg-query -S "$configuration"
# Preserve a valid local administrator edit across a same-version reinstall.
printf '\n' >> "$configuration"
before=$(sha256sum "$configuration")
apt-get -y --reinstall --no-install-recommends install "${files[@]}"
test "$(sha256sum "$configuration")" = "$before"
apt-get check
test -z "$(dpkg --audit)"
apt-get -y remove "${packages[@]}"
test ! -e /usr/bin/sonic-db-cli
test ! -e /usr/bin/swssloglevel
test ! -e /usr/lib/python3/dist-packages/swsscommon/swsscommon.py
test -f "$configuration"
# Packages with no conffiles can disappear from APT's state after removal.
# Reinstall the same real DEBs before separately checking the purge operation.
apt-get -y --no-install-recommends install "${files[@]}"
apt-get -y purge "${packages[@]}"
test ! -e "$configuration"
test ! -e /usr/bin/sonic-db-cli
if ldconfig -p | grep -F libswsscommon.so.0; then
    echo 'Common library remained in the loader cache after purge' >&2
    exit 1
fi
test -z "$(dpkg --audit)"
apt-get check
printf '%s\n' '{"initial_configuration":"passed","installed_file_modes":"passed","python_import":"passed","cli":"passed","reinstall":"passed","conffile_preservation":"passed","remove":"passed","purge":"passed","version_upgrade":"not tested: the existing source package version is 1.0.0"}' > "$package_directory/installation.json"
