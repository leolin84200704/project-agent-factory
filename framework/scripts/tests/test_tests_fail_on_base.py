#!/usr/bin/env python3
"""Tests for framework/scripts/tests_fail_on_base.py (`paf tests-fail-on-base`)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "framework" / "scripts" / "tests_fail_on_base.py"
PAF = ROOT / "bin" / "paf"


def sh(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class FailOnBaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "r"
        self.repo.mkdir()
        sh(self.repo, "init", "-q", "-b", "main")
        sh(self.repo, "config", "user.email", "t@example.com")
        sh(self.repo, "config", "user.name", "t")
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (self.repo / "test_old.py").write_text("import calc\nassert True\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-qm", "base")
        sh(self.repo, "checkout", "-q", "-b", "fix")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cmd(self, test_cmd: str, via_paf: bool = False) -> subprocess.CompletedProcess:
        argv = [sys.executable, str(PAF), "tests-fail-on-base"] if via_paf else [sys.executable, str(SCRIPT)]
        return subprocess.run(argv + ["--repo", str(self.repo), "--base", "main", "--test-cmd", test_cmd],
                              capture_output=True, text=True)

    def commit_fix(self, test_body: str) -> None:
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (self.repo / "test_add.py").write_text(test_body)
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-qm", "fix")

    def test_real_test_fails_on_base_and_passes_on_head(self):
        self.commit_fix("import calc\nassert calc.add(2, 3) == 5\n")
        proc = self.run_cmd(f"{sys.executable} test_add.py")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("PROVEN", proc.stdout)

    def test_test_that_passes_on_base_proves_nothing(self):
        self.commit_fix("import calc\nassert callable(calc.add)\n")
        proc = self.run_cmd(f"{sys.executable} test_add.py")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("NOT_PROVEN", proc.stdout)

    def test_test_failing_on_head_is_not_proven(self):
        self.commit_fix("import calc\nassert calc.add(2, 3) == 6\n")
        proc = self.run_cmd(f"{sys.executable} test_add.py")
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_uncommitted_new_test_is_included(self):
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (self.repo / "test_add.py").write_text("import calc\nassert calc.add(2, 3) == 5\n")
        proc = self.run_cmd(f"{sys.executable} test_add.py")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_no_tests_and_paf_entrypoint(self):
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        sh(self.repo, "commit", "-qam", "code only")
        proc = self.run_cmd("true", via_paf=True)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("NO_TESTS", proc.stdout)

    def test_base_worktree_is_cleaned_up(self):
        self.commit_fix("import calc\nassert calc.add(2, 3) == 5\n")
        self.run_cmd(f"{sys.executable} test_add.py")
        wts = subprocess.run(["git", "-C", str(self.repo), "worktree", "list"], capture_output=True, text=True).stdout
        self.assertEqual(len(wts.strip().splitlines()), 1, wts)


if __name__ == "__main__":
    unittest.main()
