import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from collect_bazel_resolution import collect_resolution


class ResolutionOwnershipTest(unittest.TestCase):
    def test_container_checkout_ownership_uses_scoped_git_trust(self):
        architecture = {"x86_64": "AMD64", "aarch64": "ARM64"}[platform.machine()]
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            try:
                os.chdir(root)
                Path(".gitignore").write_text("/MODULE.bazel.lock\n")
                Path("MODULE.bazel").write_text('module(name = "example")\n')
                Path(".bazelrc").write_text("common --registry=https://bcr.bazel.build\n")
                subprocess.run(["git", "add", "."], check=True)
                subprocess.run([
                    "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "Create source inputs",
                ], check=True)
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
                Path("MODULE.bazel.lock").write_text(json.dumps({"lockFileVersion": 24}))
                Path("empty-gitconfig").write_text("")
                with patch.dict(os.environ, {
                    "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": str(root / "empty-gitconfig"),
                    "RUNNER_ARCH": architecture,
                }):
                    rejected = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("dubious ownership", rejected.stderr)
                    receipt = collect_resolution(
                        root / "resolution", "yang", architecture,
                        [sys.executable, "-c", "import json; print(json.dumps(dict(name='example')))"],
                    )
                    self.assertEqual(receipt["revision"], revision)
                    self.assertEqual(len(receipt["files"]), 5)
                    self.assertEqual(Path("empty-gitconfig").read_text(), "")
                    self.assertNotEqual(subprocess.run(
                        ["git", "ls-files"], capture_output=True).returncode, 0)
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
