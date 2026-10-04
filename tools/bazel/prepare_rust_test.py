"""Check failures before the shared preparation helper can take over."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

import prepare_rust


class RustBootstrapTest(unittest.TestCase):
    def test_failed_download_cannot_keep_an_old_dependency_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overrides = root / "artifacts/rust-deps/overrides.bazelrc"
            overrides.parent.mkdir(parents=True)
            overrides.write_text(prepare_rust.RC_MARKER + "common --override_module=sonic-rust-deps=/old\n")
            with mock.patch.object(prepare_rust, "__file__", str(root / "tools/bazel/prepare_rust.py")), \
                    mock.patch.object(prepare_rust.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
                with self.assertRaises(urllib.error.URLError):
                    prepare_rust.main()
            self.assertFalse(overrides.exists())

    def test_user_configuration_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overrides = root / "artifacts/rust-deps/overrides.bazelrc"
            overrides.parent.mkdir(parents=True)
            overrides.write_text("user configuration\n")
            with mock.patch.object(prepare_rust, "__file__", str(root / "tools/bazel/prepare_rust.py")), \
                    mock.patch.object(prepare_rust.urllib.request, "urlopen") as download:
                with self.assertRaisesRegex(SystemExit, "non-generated"):
                    prepare_rust.main()
            self.assertEqual(overrides.read_text(), "user configuration\n")
            download.assert_not_called()

    def test_custom_and_default_overrides_are_removed_before_bootstrap_failure(self):
        for failure in ("download", "checksum"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                default = root / "artifacts/rust-deps/overrides.bazelrc"
                custom = root / "image-overrides.bazelrc"
                default.parent.mkdir(parents=True)
                for path in (default, custom):
                    path.write_text(prepare_rust.RC_MARKER + "common --override_module=sonic-rust-deps=/old\n")
                with mock.patch.object(prepare_rust, "__file__", str(root / "tools/bazel/prepare_rust.py")), \
                        mock.patch.object(prepare_rust.sys, "argv", ["prepare_rust.py", "--overrides-rc", str(custom)]), \
                        mock.patch.object(prepare_rust.urllib.request, "urlopen") as download:
                    if failure == "download":
                        download.side_effect = urllib.error.URLError("offline")
                        expected_error = urllib.error.URLError
                    else:
                        download.return_value.__enter__.return_value.read.return_value = b"wrong helper"
                        expected_error = SystemExit
                    with self.assertRaises(expected_error):
                        prepare_rust.main()
                self.assertFalse(default.exists())
                self.assertFalse(custom.exists())

    def test_custom_user_configuration_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            default = root / "artifacts/rust-deps/overrides.bazelrc"
            custom = root / "user-overrides.bazelrc"
            default.parent.mkdir(parents=True)
            generated = prepare_rust.RC_MARKER + "common --override_module=sonic-rust-deps=/old\n"
            default.write_text(generated)
            custom.write_text("user configuration\n")
            with mock.patch.object(prepare_rust, "__file__", str(root / "tools/bazel/prepare_rust.py")), \
                    mock.patch.object(prepare_rust.sys, "argv", ["prepare_rust.py", f"--overrides-rc={custom}"]), \
                    mock.patch.object(prepare_rust.urllib.request, "urlopen") as download:
                with self.assertRaisesRegex(SystemExit, "non-generated"):
                    prepare_rust.main()
            self.assertEqual(default.read_text(), generated)
            self.assertEqual(custom.read_text(), "user configuration\n")
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
