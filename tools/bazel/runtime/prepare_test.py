"""Check package provenance and test-cache identity without installing anything."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import prepare


def resolved_lock():
    packages = {}
    repositories = {}
    for architecture in ("amd64", "arm64"):
        for name in (*prepare.RUNTIME_ROOTS, "zlib1g"):
            version = "1:2.3-1" if name == "zlib1g" else "2.3-1"
            identity = f"/trixie/{name}:{architecture}={version}"
            checksum = (name + architecture).encode().hex().ljust(64, "0")
            packages[identity] = {
                "name": name, "version": version, "architecture": architecture,
                "suite": "trixie", "sha256": checksum,
                "depends_on": [] if name == "zlib1g" else [f"/trixie/zlib1g:{architecture}=1:2.3-1"],
            }
            repositories[identity] = {
                "repoRuleId": "@@rules_distroless+//apt/private:deb_import.bzl%deb_import",
                "attributes": {"package_name": name, "sha256": checksum, "urls": [
                    f"https://snapshot.debian.org/archive/debian/20260727T143429Z/pool/main/p/{name}_2.3-1_{architecture}.deb",
                ]},
            }
    repositories["trixie"] = {"repoRuleId": "hub", "attributes": {"lock_content": json.dumps({"packages": packages})}}
    return {"moduleExtensions": {"@@rules_distroless+//apt:extensions.bzl%apt": {
        "general": {"generatedRepoSpecs": repositories},
    }}}


IMAGE = "debian:trixie-test@sha256:" + "a" * 64


def environment_lock():
    architectures = {}
    for architecture in ("amd64", "arm64"):
        bootstrap = {f"libuuid1:{architecture}": "9.0-1", f"base-files:{architecture}": "13.0",
                     "ca-certificates:all": "20260601"}
        runtime = {f"{name}:{architecture}": package["version"] for name, package in
                   prepare.select_runtime(resolved_lock(), architecture).items()}
        architectures[architecture] = {"bootstrap_packages": bootstrap, "runtime_packages": dict(runtime, **bootstrap)}
    return {"image": IMAGE, "architectures": architectures}


class RuntimePreparationTest(unittest.TestCase):
    def test_selects_native_packages_and_preserves_debian_epoch(self):
        for architecture in ("amd64", "arm64"):
            selected = prepare.select_runtime(resolved_lock(), architecture)
            self.assertEqual(len(selected), 5)
            self.assertEqual(selected["zlib1g"]["version"], "1:2.3-1")
            self.assertEqual({p["architecture"] for p in selected.values()}, {architecture})

    def test_mismatched_download_provenance_is_rejected(self):
        lock = resolved_lock()
        repositories = next(iter(lock["moduleExtensions"].values()))["general"]["generatedRepoSpecs"]
        repositories["/trixie/libzmq5:amd64=2.3-1"]["attributes"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "download provenance"):
            prepare.select_runtime(lock, "amd64")

    def test_latest_debian_source_is_not_substituted(self):
        selected = prepare.select_runtime(resolved_lock(), "amd64")
        selected["libzmq5"]["urls"] = ["https://deb.debian.org/debian/pool/main/z/zmq.deb"]
        with self.assertRaisesRegex(ValueError, "selected Debian snapshots"):
            prepare.snapshot_sources(selected, "amd64")

    def test_approved_base_is_preserved_but_missing_packages_use_exact_versions(self):
        selected = prepare.select_runtime(resolved_lock(), "amd64")
        approved = prepare.select_environment(environment_lock(), "amd64", IMAGE)
        before = approved["bootstrap_packages"]
        prepare.verify_preparation_inventory(before, approved)
        requested = prepare.missing_packages(selected, before)
        self.assertFalse(any(item.startswith("libuuid1:") for item in requested))
        after = dict(before, **{f"{name}:amd64": p["version"] for name, p in selected.items() if name != "libuuid1"})
        prepare.verify_inventory(selected, before, after)
        prepare.verify_approved_inventory(after, approved["runtime_packages"], "runtime environment")
        after["libzmq5:amd64"] = "9.0-1"
        with self.assertRaisesRegex(ValueError, "Installed runtime versions"):
            prepare.verify_inventory(selected, before, after)
        after["libuuid1:amd64"] = "2.3-1"
        with self.assertRaisesRegex(ValueError, "prepared-base package"):
            prepare.verify_inventory(selected, before, after)

    def test_only_approved_image_and_native_architecture_are_accepted(self):
        lock = environment_lock()
        for architecture in ("amd64", "arm64"):
            self.assertEqual(prepare.select_environment(lock, architecture, IMAGE), lock["architectures"][architecture])
        with self.assertRaisesRegex(ValueError, "image reference"):
            prepare.select_environment(lock, "amd64", "debian:trixie-test")
        with self.assertRaisesRegex(ValueError, "architecture"):
            prepare.select_environment(lock, "armhf", IMAGE)
        del lock["architectures"]["arm64"]
        with self.assertRaisesRegex(ValueError, "architecture"):
            prepare.select_environment(lock, "arm64", IMAGE)
        lock["image"] = "debian:trixie-test"
        with self.assertRaisesRegex(ValueError, "image digest"):
            prepare.select_environment(lock, "amd64", lock["image"])

    def test_wrong_architecture_or_base_changes_in_approved_inventory_are_rejected(self):
        lock = environment_lock()
        approved = lock["architectures"]["amd64"]
        approved["bootstrap_packages"]["foreign:arm64"] = "1.0"
        with self.assertRaisesRegex(ValueError, "Invalid approved"):
            prepare.select_environment(lock, "amd64", IMAGE)
        del approved["bootstrap_packages"]["foreign:arm64"]
        approved["runtime_packages"]["libuuid1:amd64"] = "10.0-1"
        with self.assertRaisesRegex(ValueError, "changes a bootstrap package"):
            prepare.select_environment(lock, "amd64", IMAGE)

    def test_old_new_extra_and_missing_packages_are_rejected(self):
        for architecture in ("amd64", "arm64"):
            approved = prepare.select_environment(environment_lock(), architecture, IMAGE)
            for stage in ("bootstrap_packages", "runtime_packages"):
                for change in ("older", "newer", "extra", "missing"):
                    with self.subTest(architecture=architecture, stage=stage, change=change):
                        actual = dict(approved[stage])
                        package = f"libuuid1:{architecture}"
                        if change in ("older", "newer"):
                            actual[package] = "8.0-1" if change == "older" else "10.0-1"
                        elif change == "extra":
                            actual[f"unapproved:{architecture}"] = "1.0"
                        else:
                            del actual[package]
                        with self.assertRaisesRegex(ValueError, "Installed packages differ"):
                            prepare.verify_preparation_inventory(actual, approved)
                        with self.assertRaisesRegex(ValueError, "Installed packages differ"):
                            prepare.verify_approved_inventory(actual, approved[stage], stage)

    def test_partial_runtime_preparation_is_not_an_approved_starting_point(self):
        approved = prepare.select_environment(environment_lock(), "amd64", IMAGE)
        partial = dict(approved["bootstrap_packages"], **{"libzmq5:amd64": "2.3-1"})
        with self.assertRaisesRegex(ValueError, "unexpected libzmq5"):
            prepare.verify_preparation_inventory(partial, approved)

    def test_repeated_runtime_preparation_keeps_identity_and_records_approved_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment.json"
            environment.write_text(json.dumps(environment_lock()))
            module = root / "module.json"
            module.write_text(json.dumps(resolved_lock()))
            output = root / "receipt.json"
            approved = environment_lock()["architectures"]["amd64"]
            arguments = ["prepare.py", "--environment-lock", str(environment), "--lock", str(module),
                         "--output", str(output), "--image-reference", IMAGE]
            with mock.patch("sys.argv", arguments), \
                    mock.patch.object(prepare, "require_container"), \
                    mock.patch.object(prepare, "require_no_installed_common"), \
                    mock.patch.object(prepare.subprocess, "check_output", return_value="amd64\n"), \
                    mock.patch.object(prepare, "inventory", side_effect=[approved["bootstrap_packages"],
                                     approved["runtime_packages"], approved["runtime_packages"], approved["runtime_packages"]]), \
                    mock.patch.object(prepare, "install_missing") as install, \
                    mock.patch.object(prepare, "library_hashes", return_value={"libtest.so": "unchanged"}):
                prepare.main()
                first = json.loads(output.read_text())
                prepare.main()
                repeated = json.loads(output.read_text())
            self.assertEqual(first["SONIC_TEST_RUNTIME_ID"], repeated["SONIC_TEST_RUNTIME_ID"])
            self.assertEqual(first["environment_lock_sha256"], prepare.sha256(environment))
            self.assertEqual(install.call_args_list[1].args[2], approved["runtime_packages"])
            changed = {k: v for k, v in first.items() if k not in
                       ("SONIC_TEST_RUNTIME_ID", "preserved_base_packages", "lock_sha256")}
            changed["environment_lock_sha256"] = "other-approved-environment"
            self.assertNotEqual(first["SONIC_TEST_RUNTIME_ID"], prepare.fingerprint(changed))

    def test_bootstrap_verification_needs_no_bazel_lock_or_runtime_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment.json"
            environment.write_text(json.dumps(environment_lock()))
            output = root / "bootstrap.json"
            arguments = ["prepare.py", "--environment-lock", str(environment), "--lock", str(root / "absent.json"),
                         "--output", str(output), "--image-reference", IMAGE, "--verify-bootstrap-only"]
            with mock.patch("sys.argv", arguments), \
                    mock.patch.object(prepare, "require_container"), \
                    mock.patch.object(prepare, "require_no_installed_common"), \
                    mock.patch.object(prepare.subprocess, "check_output", return_value="arm64\n"), \
                    mock.patch.object(prepare, "inventory", return_value=environment_lock()["architectures"]["arm64"]["bootstrap_packages"]), \
                    mock.patch.object(prepare, "select_runtime") as select, \
                    mock.patch.object(prepare, "install_missing") as install:
                prepare.main()
            select.assert_not_called()
            install.assert_not_called()
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["validation"], "approved bootstrap environment")
            self.assertIn("SONIC_BOOTSTRAP_ENVIRONMENT_ID", receipt)

    def test_fingerprint_changes_with_actual_versions_or_library_bytes(self):
        original = {"installed": {"libc6": "2.41-12"}, "libraries": {"libc.so.6": "old-hash"}}
        reordered = {"libraries": original["libraries"], "installed": original["installed"]}
        self.assertEqual(prepare.fingerprint(original), prepare.fingerprint(reordered))
        changed = copy.deepcopy(original)
        changed["libraries"]["libc.so.6"] = "new-hash"
        self.assertNotEqual(prepare.fingerprint(original), prepare.fingerprint(changed))
        changed = copy.deepcopy(original)
        changed["installed"]["libc6"] = "2.41-13"
        self.assertNotEqual(prepare.fingerprint(original), prepare.fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
