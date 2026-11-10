"""資格情報の生成と、実行時データがリポジトリに混入しないことの検証。"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from policy import LabError
from server import load_credentials

ROLES = {"agent", "supervisor", "planner", "viewer"}


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_generates_fresh_tokens_with_private_permissions(self):
        path = self.dir / "nested" / "credentials.json"
        data = load_credentials(path)
        self.assertEqual(set(data), ROLES)
        tokens = [item["token"] for item in data.values()]
        self.assertEqual(len(set(tokens)), len(tokens))
        self.assertTrue(all(len(t) >= 32 for t in tokens))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_each_data_dir_gets_its_own_tokens(self):
        a = load_credentials(self.dir / "a" / "credentials.json")
        b = load_credentials(self.dir / "b" / "credentials.json")
        for role in ROLES:
            self.assertNotEqual(a[role]["token"], b[role]["token"])

    def test_existing_file_is_reused(self):
        path = self.dir / "credentials.json"
        first = load_credentials(path)
        self.assertEqual(load_credentials(path), first)

    def test_rejects_short_or_mismatched_tokens(self):
        path = self.dir / "credentials.json"
        path.write_text(json.dumps({"agent": {"id": "agent-01", "role": "agent", "token": "short"}}))
        with self.assertRaises(LabError):
            load_credentials(path)
        path.write_text(json.dumps({"agent": {"id": "x", "role": "supervisor", "token": "x" * 40}}))
        with self.assertRaises(LabError):
            load_credentials(path)


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=True, text=True)


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "git リポジトリ外では省略")
class RepositoryHygieneTests(unittest.TestCase):
    RUNTIME_PATHS = [
        ".runtime/credentials.json",
        ".runtime/factory.sqlite",
        ".runtime/factory.sqlite-wal",
        ".runtime/factory.sqlite-shm",
        ".runtime-factory3d/credentials.json",
        ".runtime-other/factory.sqlite",
    ]

    def test_runtime_data_is_ignored(self):
        for path in self.RUNTIME_PATHS:
            with self.subTest(path=path):
                result = git("check-ignore", "--no-index", "-q", path)
                self.assertEqual(result.returncode, 0, path + " が .gitignore で除外されていません")

    def test_source_files_are_not_ignored(self):
        for path in ["server.py", "credentials.py", "tests/test_lab.py", "models/motor_factory_line.blend"]:
            with self.subTest(path=path):
                self.assertEqual(git("check-ignore", "--no-index", "-q", path).returncode, 1)

    def test_no_runtime_data_is_tracked(self):
        tracked = git("ls-files").stdout.splitlines()
        leaked = [p for p in tracked
                  if p.startswith(".runtime") or p.endswith(("credentials.json", ".sqlite", ".sqlite-wal", ".sqlite-shm"))]
        self.assertEqual(leaked, [], "実行時データがコミット対象になっています")


if __name__ == "__main__":
    unittest.main()
