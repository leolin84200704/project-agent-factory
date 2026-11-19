#!/usr/bin/env python3
"""`paf tests-fail-on-base`: a new test must fail without the change it tests.

"A check you've never seen fail proves nothing" (Lauren Tan, pstack guide 06).
A test that also passes on the base commit does not prove the change does
anything. This command copies the test files the branch added or modified onto
a temporary worktree of the base commit, runs the test command there (it must
FAIL), then runs it on the current tree (it must PASS).

Exit codes: 0 proven (fails on base, passes on head); 1 not proven (passes on
base, or fails on head); 2 usage/setup error; 3 no added or modified test files.

Limitation: the base worktree is a bare checkout. A test command that needs
installed dependencies (node_modules, a venv) must reference them by absolute
path or install them itself.
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TEST_GLOBS = (
    "test_*.py", "*_test.py", "*.test.py", "*.test.sh", "*.test.ts", "*.test.js", "*.test.tsx",
    "*.spec.ts", "*.spec.js", "*.spec.tsx", "*_test.go", "*Test.java", "*Tests.java",
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def changed_test_files(repo: Path, base: str, globs: tuple[str, ...]) -> list[str]:
    merge_base = git(repo, "merge-base", base, "HEAD").strip()
    names = git(repo, "diff", "--name-only", "--diff-filter=AM", merge_base, "HEAD").splitlines()
    names += git(repo, "diff", "--name-only", "--diff-filter=AM", "HEAD").splitlines()
    names += git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    out = []
    for n in dict.fromkeys(names):
        base_name = Path(n).name
        if any(fnmatch.fnmatch(base_name, g) for g in globs) and (repo / n).is_file():
            out.append(n)
    return out


def run_tests(cmd: str, cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="paf tests-fail-on-base", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--base", default="origin/main", help="base ref (default origin/main)")
    ap.add_argument("--test-cmd", required=True, help="command that runs the new tests; run from the repo root")
    ap.add_argument("--glob", action="append", default=None, help="test file glob (repeatable); defaults cover common patterns")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    try:
        merge_base = git(repo, "merge-base", args.base, "HEAD").strip()
        tests = changed_test_files(repo, args.base, tuple(args.glob or DEFAULT_TEST_GLOBS))
    except subprocess.CalledProcessError as exc:
        print(f"tests-fail-on-base ERROR: {exc.stderr.strip() or exc}")
        return 2
    if not tests:
        print("tests-fail-on-base NO_TESTS: the branch adds or modifies no test files")
        return 3

    with tempfile.TemporaryDirectory(prefix="paf-fail-on-base-") as tmp:
        wt = Path(tmp) / "base"
        try:
            git(repo, "worktree", "add", "--detach", str(wt), merge_base)
        except subprocess.CalledProcessError as exc:
            print(f"tests-fail-on-base ERROR: cannot create base worktree: {exc.stderr.strip()}")
            return 2
        try:
            for t in tests:
                dest = wt / t
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo / t, dest)
            base_code, base_tail = run_tests(args.test_cmd, wt, args.timeout)
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)

    head_code, head_tail = run_tests(args.test_cmd, repo, args.timeout)
    print(f"tests: {', '.join(tests)}")
    print(f"base {merge_base[:12]}: exit {base_code} ({'fails as required' if base_code != 0 else 'PASSES: the tests do not prove the change'})")
    print(f"head: exit {head_code} ({'passes' if head_code == 0 else 'FAILS'})")
    if base_code != 0 and head_code == 0:
        print("tests-fail-on-base PROVEN")
        return 0
    if base_code == 0:
        print("--- base output tail ---\n" + base_tail)
    if head_code != 0:
        print("--- head output tail ---\n" + head_tail)
    print("tests-fail-on-base NOT_PROVEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
