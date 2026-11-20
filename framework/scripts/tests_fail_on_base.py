#!/usr/bin/env python3
"""`paf tests-fail-on-base`: a new test must fail without the change it tests.

"A check you've never seen fail proves nothing" (Lauren Tan, pstack guide 06).
A test that also passes on the base commit does not prove the change does
anything. This command copies the test files the branch added or modified onto
a temporary worktree of the base commit, links untracked local dependency dirs
(`.venv`, `venv`, `node_modules`, or `--link-dir`) so installed runtimes work
in the base worktree, runs the test command there, then runs it on head.

Change types (`--change-type`):
  - `auto` (default): enforces fail-on-base when code changed; exempts pure
    docs-only branches that touch no code or test files.
  - `bugfix` / `feature`: strict fail-on-base (fails on base, passes on head).
  - `refactor`: behavioral equivalence (passes on BOTH base and head).
  - `docs` / `config`: exempt only when every changed file matches the allowed
    doc/config set (refuses exemption if any code file changed).

Exit codes: 0 proven or exempt; 1 not proven or invalid exemption; 2 usage/setup
error; 3 no added or modified test files.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TEST_GLOBS = (
    "test_*.py", "*_test.py", "*.test.py", "*.test.sh", "*.test.ts", "*.test.js", "*.test.tsx",
    "*.spec.ts", "*.spec.js", "*.spec.tsx", "*_test.go", "*Test.java", "*Tests.java",
)
DEFAULT_DEP_DIRS = (".venv", "venv", "node_modules")
CHANGE_TYPES = ("auto", "bugfix", "feature", "refactor", "docs", "config")
DOC_EXTS = frozenset({".md", ".txt", ".rst", ".adoc"})
CONFIG_EXTS = frozenset({".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".plist", ".example"})
CONFIG_NAMES = frozenset({".gitignore", ".editorconfig", ".env.example", ".npmrc", ".nvmrc"})
# Governance and permission-granting files can never self-exempt via --change-type docs, config, or refactor.
GOVERNANCE_PATH_RE = re.compile(
    r"(^|/)(CORE\.md|AGENTS\.md|CLAUDE\.md|GEMINI\.md|WORK-LOOP\.md|PERMISSIONS\.md|FEEDBACK\.md|"
    r"\.agent-verify\.json|\.mcp\.json|\.gitlab-ci\.ya?ml|"
    r"run-regression\.py|dune_lint\.py|paf_watcher\.py|model_router\.py|pre-tool-guard\.py)$|"
    r"^bin/paf$|"
    r"(^|/)(\.claude/settings[^/]*\.json|\.gemini/settings[^/]*\.json|\.gemini/config/|"
    r"framework/templates/|"
    r"\.github/(workflows|actions)/|"
    r"hooks/|githooks/|framework/githooks/|framework/hooks/|"
    r"DailyJob/|launchd/|skills/)",
    re.IGNORECASE,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def changed_all_files(repo: Path, base: str) -> list[str]:
    merge_base = git(repo, "merge-base", base, "HEAD").strip()
    names = git(repo, "diff", "--name-only", merge_base, "HEAD").splitlines()
    names += git(repo, "diff", "--name-only", "HEAD").splitlines()
    names += git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))


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


def _is_governance_file(rel: str) -> bool:
    clean = re.sub(r"^(?:\./)+", "", str(rel or "").strip())
    return bool(clean and GOVERNANCE_PATH_RE.search(clean))


def _is_doc_file(rel: str) -> bool:
    return not _is_governance_file(rel) and Path(rel).suffix.lower() in DOC_EXTS


def _is_config_or_doc_file(rel: str) -> bool:
    if _is_governance_file(rel):
        return False
    p = Path(rel)
    return Path(rel).suffix.lower() in DOC_EXTS or p.suffix.lower() in CONFIG_EXTS or p.name in CONFIG_NAMES


def _link_dependency_dirs(repo: Path, wt: Path, rel_dirs: list[str]) -> list[tuple[Path, str]]:
    """Symlink untracked top-level dependency dirs from `repo` into `wt`, rejecting top-level escapes."""
    created: list[tuple[Path, str]] = []
    repo_root = repo.resolve()
    wt_root = wt.resolve()
    for rel in dict.fromkeys(rel_dirs):
        rel_p = Path(rel)
        if rel_p.is_absolute() or ".." in rel_p.parts:
            raise ValueError(f"--link-dir must be a relative path inside the repo: {rel!r}")
        src = repo_root / rel_p
        if not src.exists():
            continue
        resolved_src = src.resolve()
        if not resolved_src.is_relative_to(repo_root):
            raise ValueError(f"dependency dir symlink target escapes the repo: {rel!r} -> {resolved_src}")
        dst = wt_root / rel_p
        if dst.exists() or dst.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(resolved_src, target_is_directory=resolved_src.is_dir())
        created.append((dst, rel_p.as_posix()))
    return created


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
    ap.add_argument("--test-cmd", default=None, help="command that runs the tests; run from the repo root")
    ap.add_argument("--change-type", choices=CHANGE_TYPES, default="auto",
                    help="change class: auto, bugfix, feature, refactor, docs, or config")
    ap.add_argument("--glob", action="append", default=None, help="test file glob (repeatable); defaults cover common patterns")
    ap.add_argument("--link-dir", action="append", default=None,
                    help="relative dependency dir to symlink into the base worktree (repeatable)")
    ap.add_argument("--no-link-deps", action="store_true",
                    help="disable automatic symlinking of .venv, venv, and node_modules into the base worktree")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    try:
        merge_base = git(repo, "merge-base", args.base, "HEAD").strip()
        all_files = changed_all_files(repo, args.base)
        tests = changed_test_files(repo, args.base, tuple(args.glob or DEFAULT_TEST_GLOBS))
    except subprocess.CalledProcessError as exc:
        print(f"tests-fail-on-base ERROR: {exc.stderr.strip() or exc}")
        return 2

    if args.change_type == "docs":
        non_docs = [f for f in all_files if not _is_doc_file(f)]
        if non_docs:
            print(f"tests-fail-on-base REFUSED: --change-type docs rejected because non-doc files changed: {', '.join(non_docs[:5])}")
            return 1
        print(f"tests-fail-on-base EXEMPT (docs-only: {len(all_files)} file(s))")
        return 0

    if args.change_type == "config":
        non_cfg = [f for f in all_files if not _is_config_or_doc_file(f)]
        if non_cfg:
            print(f"tests-fail-on-base REFUSED: --change-type config rejected because code files changed: {', '.join(non_cfg[:5])}")
            return 1
        if args.test_cmd:
            head_code, head_tail = run_tests(args.test_cmd, repo, args.timeout)
            if head_code != 0:
                print(f"head: exit {head_code} (FAILS)\n--- head output tail ---\n{head_tail}")
                print("tests-fail-on-base NOT_PROVEN")
                return 1
        print(f"tests-fail-on-base EXEMPT (config-only: {len(all_files)} file(s))")
        return 0

    if args.change_type == "refactor":
        gov_files = [f for f in all_files if _is_governance_file(f)]
        if gov_files:
            print(f"tests-fail-on-base REFUSED: --change-type refactor rejected because governance files changed: {', '.join(gov_files[:5])}")
            return 1

    if args.change_type == "auto" and not tests and all_files and all(_is_doc_file(f) for f in all_files):
        print(f"tests-fail-on-base EXEMPT (docs-only: {len(all_files)} file(s))")
        return 0

    if not args.test_cmd:
        print("tests-fail-on-base ERROR: --test-cmd is required for code changes")
        return 2

    if not tests:
        print("tests-fail-on-base NO_TESTS: the branch adds or modifies no test files")
        return 3

    dep_dirs = ([] if args.no_link_deps else list(DEFAULT_DEP_DIRS)) + list(args.link_dir or [])

    base_code = 2
    base_tail = ""
    with tempfile.TemporaryDirectory(prefix="paf-fail-on-base-") as tmp:
        wt = Path(tmp) / "base"
        try:
            git(repo, "worktree", "add", "--detach", str(wt), merge_base)
        except subprocess.CalledProcessError as exc:
            print(f"tests-fail-on-base ERROR: cannot create base worktree: {exc.stderr.strip()}")
            return 2
        linked: list[tuple[Path, str]] = []
        try:
            try:
                linked = _link_dependency_dirs(repo, wt, dep_dirs)
            except ValueError as exc:
                print(f"tests-fail-on-base ERROR: {exc}")
                return 2
            for t in tests:
                dest = wt / t
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(repo / t, dest)
                except OSError as exc:
                    print(f"tests-fail-on-base ERROR: cannot copy test file {t!r}: {exc}")
                    return 2
            base_code, base_tail = run_tests(args.test_cmd, wt, args.timeout)
        finally:
            for link, _ in reversed(linked):
                try:
                    if link.is_symlink():
                        link.unlink()
                except OSError:
                    pass
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)

    head_code, head_tail = run_tests(args.test_cmd, repo, args.timeout)
    if tests:
        print(f"tests: {', '.join(tests)}")
    if linked:
        print(f"linked_deps: {', '.join(name for _, name in linked)}")

    if args.change_type == "refactor":
        print(f"base {merge_base[:12]}: exit {base_code} ({'passes as required for refactor' if base_code == 0 else 'FAILS'})")
        print(f"head: exit {head_code} ({'passes' if head_code == 0 else 'FAILS'})")
        if base_code == 0 and head_code == 0:
            print("tests-fail-on-base EXEMPT (refactor: tests pass on both base and head)")
            return 0
        if base_code != 0:
            print("--- base output tail ---\n" + base_tail)
        if head_code != 0:
            print("--- head output tail ---\n" + head_tail)
        print("tests-fail-on-base NOT_PROVEN")
        return 1

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
