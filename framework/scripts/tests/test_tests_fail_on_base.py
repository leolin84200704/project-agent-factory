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

    def test_refactor_requires_changed_test_file_and_fails_on_regression(self):
        (self.repo / "calc.py").write_text("def add(a, b):\n    out = a - b\n    return out\n")
        sh(self.repo, "add", "calc.py")
        sh(self.repo, "commit", "-qm", "refactor without touching tests")
        # Refactor with zero added/modified test files must stay NO_TESTS (exit 3), never self-exempt.
        proc_no_tests = self.run_cmd(f"{sys.executable} test_old.py", extra=["--change-type", "refactor"])
        self.assertEqual(proc_no_tests.returncode, 3, proc_no_tests.stdout + proc_no_tests.stderr)
        self.assertIn("NO_TESTS", proc_no_tests.stdout)

        (self.repo / "test_old.py").write_text("import calc\nassert calc.add(5, 2) == 3\n")
        sh(self.repo, "add", "test_old.py")
        sh(self.repo, "commit", "-qm", "add equivalence test for refactor")
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

        # Plain non-governance config file may use --change-type config.
        (self.repo / "app.config.json").write_text('{"timeout": 5}\n')
        proc_ok_cfg = self.run_cmd(None, extra=["--change-type", "config"])
        self.assertEqual(proc_ok_cfg.returncode, 0, proc_ok_cfg.stdout + proc_ok_cfg.stderr)
        self.assertIn("EXEMPT", proc_ok_cfg.stdout)
        (self.repo / "app.config.json").unlink()

        # Governance and permission files must NEVER self-exempt under config, docs, or auto.
        for gov_rel, content, ctype in (
            (".agent-verify.json", '{"zone":"autonomous"}\n', "config"),
            (".github/workflows/ci.yml", "name: ci\n", "config"),
            ("launchd/com.test.plist", "<plist/>\n", "config"),
            ("CORE.md", "# Core rule edit\n", "docs"),
        ):
            p = self.repo / gov_rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            proc_gov = self.run_cmd(None, extra=["--change-type", ctype])
            self.assertEqual(proc_gov.returncode, 1, f"{gov_rel} should be refused under {ctype}: {proc_gov.stdout}")
            self.assertIn("REFUSED", proc_gov.stdout)
            if ctype == "docs":
                proc_auto_gov = self.run_cmd(f"{sys.executable} test_old.py", extra=["--change-type", "auto"])
                self.assertEqual(proc_auto_gov.returncode, 3, proc_auto_gov.stdout)
            p.unlink()

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
        (self.repo / ".gitignore").write_text("node_modules/\nvenv/\n")

        # Distinguishing pair: a behavior-preserving test importing the untracked dep MUST pass on base
        # (NOT_PROVEN) when linked, and fail on base (ImportError -> PROVEN) when --no-link-deps is set.
        (self.repo / "test_dep.py").write_text(
            "import sys\n"
            "sys.path.insert(0, 'node_modules/helper')\n"
            "import mod\n"
            "assert mod.EXPECTED == 5\n"
        )
        proc_linked = self.run_cmd(f"{sys.executable} test_dep.py")
        self.assertEqual(proc_linked.returncode, 1, proc_linked.stdout + proc_linked.stderr)
        self.assertIn("NOT_PROVEN", proc_linked.stdout)
        self.assertIn("linked_deps: node_modules", proc_linked.stdout)

        proc_unlinked = self.run_cmd(f"{sys.executable} test_dep.py", extra=["--no-link-deps"])
        self.assertEqual(proc_unlinked.returncode, 0, proc_unlinked.stdout + proc_unlinked.stderr)
        self.assertIn("PROVEN", proc_unlinked.stdout)
        (self.repo / "test_dep.py").unlink()

        # Real bugfix test using the linked dependency succeeds cleanly and keeps original dep intact.
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

        # Path traversal in --link-dir and symlinks escaping the repo are both rejected.
        proc_bad = self.run_cmd(f"{sys.executable} test_add.py", extra=["--link-dir", "../outside"])
        self.assertEqual(proc_bad.returncode, 2, proc_bad.stdout + proc_bad.stderr)

        outside = Path(self.tmp.name) / "outside_venv"
        outside.mkdir()
        (self.repo / "venv").symlink_to(outside, target_is_directory=True)
        proc_escape = self.run_cmd(f"{sys.executable} test_add.py")
        self.assertEqual(proc_escape.returncode, 2, proc_escape.stdout + proc_escape.stderr)
        self.assertIn("escapes the repo", proc_escape.stdout)
        (self.repo / "venv").unlink()


if __name__ == "__main__":
    unittest.main()
