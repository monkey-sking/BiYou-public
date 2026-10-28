"""Exercise real Git hooks in disposable repositories, without credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PrivateConfigGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q")
        self.git("config", "user.name", "Guard test")
        self.git("config", "user.email", "guard@example.invalid")
        for name in ("scripts/check_public_history.py",
                     ".githooks/pre-commit", ".githooks/pre-push"):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
            target.chmod(0o755)
        self.git("config", "core.hooksPath", ".githooks")
        self.git("add", ".")
        self.git("commit", "-qm", "Clean baseline")

    def git(self, *args, check=True, input=None):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env,
            text=True, input=input, capture_output=True, check=check,
        )

    def stage(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git("add", "-f", name)
        return path

    def test_forced_private_config_commit_is_blocked(self):
        self.stage("config/config.yaml", "language: zh\n")
        result = self.git("commit", "-qm", "Private config", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED private staged path", result.stderr)

    def test_private_config_symlink_commit_is_blocked(self):
        self.stage("config/config.example.yaml", "api_key: \"\"\n")
        (self.repo / "config/config.yaml").symlink_to("/private/config.yaml")
        self.git("add", "-f", "config/config.yaml")
        result = self.git("commit", "-qm", "Private link", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED private staged path", result.stderr)

    def test_aq_key_in_staged_blob_cannot_be_hidden_by_worktree(self):
        key = "AQ." + "aB7cD9" * 9
        path = self.stage("settings.txt", "api_key=" + key + "\n")
        path.write_text("api_key=\n", encoding="utf-8")
        result = self.git("commit", "-qm", "Hidden key", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED likely secret", result.stderr)
        self.assertNotIn(key, result.stderr)

    def test_sanitized_template_can_be_committed(self):
        self.stage("config/config.example.yaml", 'api_key: ""\nlanguage: zh\n')
        self.git("commit", "-qm", "Template")

    def test_aq_key_with_placeholder_substring_is_still_blocked(self):
        self.stage("settings.txt", "AQ." + "example" + "aB7cD9" * 8 + "\n")
        result = self.git("commit", "-qm", "Key contains ordinary word", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED likely secret", result.stderr)

    def test_private_path_deleted_from_tip_still_blocks_push(self):
        self.stage("config/context.json", "{}\n")
        self.git("-c", "core.hooksPath=/dev/null",
                 "commit", "-qm", "Unsafe old commit")
        self.git("rm", "config/context.json")
        self.git("commit", "-qm", "Remove from tip")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        result = subprocess.run(
            ["sh", ".githooks/pre-push", "origin", "unused"],
            cwd=self.repo, env=self.env, text=True, capture_output=True,
            input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED private historical path", result.stderr)

    def test_aq_key_in_history_blocks_push(self):
        self.stage("settings.txt", "AQ." + "aB7cD9" * 9 + "\n")
        self.git("-c", "core.hooksPath=/dev/null",
                 "commit", "-qm", "Unsafe old key")
        self.git("rm", "settings.txt")
        self.git("commit", "-qm", "Remove from tip")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        result = subprocess.run(
            ["sh", ".githooks/pre-push", "origin", "unused"],
            cwd=self.repo, env=self.env, text=True, capture_output=True,
            input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED likely secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
