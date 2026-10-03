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


if __name__ == "__main__":
    unittest.main()
