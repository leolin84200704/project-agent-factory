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

    def run_cmd(self, test_cmd: str | None = None, via_paf: bool = False, extra: list[str] | None = None) -> subprocess.CompletedProcess:
        argv = [sys.executable, str(PAF), "tests-fail-on-base"] if via_paf else [sys.executable, str(SCRIPT)]
        args = ["--repo", str(self.repo), "--base", "main"]
        if test_cmd is not None:
            args += ["--test-cmd", test_cmd]
        if extra:
            args += extra
        return subprocess.run(argv + args, capture_output=True, text=True)

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

    def test_refactor_exempt_passes_when_behavior_preserved_and_fails_on_regression(self):
        (self.repo / "calc.py").write_text("def add(a, b):\n    out = a - b\n    return out\n")
        (self.repo / "test_old.py").write_text("import calc\nassert calc.add(5, 2) == 3\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-qm", "pure refactor")
        proc = self.run_cmd(f"{sys.executable} test_old.py", extra=["--change-type", "refactor"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("EXEMPT", proc.stdout)

        # If the refactor broke behavior on head, it must still fail.
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        proc_bad = self.run_cmd(f"{sys.executable} test_old.py", extra=["--change-type", "refactor"])
        self.assertEqual(proc_bad.returncode, 1, proc_bad.stdout + proc_bad.stderr)

    def test_docs_and_config_exemptions_and_anti_gaming_refusal(self):
        (self.repo / "README.md").write_text("# Docs\n")
        sh(self.repo, "add", "README.md")
        sh(self.repo, "commit", "-qm", "docs only")
        proc_auto = self.run_cmd(None, extra=["--change-type", "auto"])
        self.assertEqual(proc_auto.returncode, 0, proc_auto.stdout + proc_auto.stderr)
        self.assertIn("EXEMPT", proc_auto.stdout)

        # Adding a code file makes --change-type docs and config fail closed.
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        proc_docs = self.run_cmd(None, extra=["--change-type", "docs"])
        self.assertEqual(proc_docs.returncode, 1, proc_docs.stdout)
        self.assertIn("REFUSED", proc_docs.stdout)
        proc_cfg = self.run_cmd(None, extra=["--change-type", "config"])
        self.assertEqual(proc_cfg.returncode, 1, proc_cfg.stdout)
        self.assertIn("REFUSED", proc_cfg.stdout)

    def test_untracked_dependency_dir_is_symlinked_into_base_worktree(self):
        # Untracked local dependency dir (mirrors .venv / node_modules) needed by the test runner.
        dep = self.repo / "node_modules" / "helper"
        dep.mkdir(parents=True)
        (dep / "mod.py").write_text("EXPECTED = 5\n")
        (self.repo / ".gitignore").write_text("node_modules/\n")
        self.commit_fix(
            "import sys, calc\n"
            "sys.path.insert(0, 'node_modules/helper')\n"
            "import mod\n"
            "assert calc.add(2, 3) == mod.EXPECTED\n"
        )
        proc = self.run_cmd(f"{sys.executable} test_add.py")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("linked_deps: node_modules", proc.stdout)
        self.assertTrue((dep / "mod.py").is_file())

        # Path traversal in --link-dir is rejected.
        proc_bad = self.run_cmd(f"{sys.executable} test_add.py", extra=["--link-dir", "../outside"])
        self.assertEqual(proc_bad.returncode, 2, proc_bad.stdout + proc_bad.stderr)


if __name__ == "__main__":
    unittest.main()
