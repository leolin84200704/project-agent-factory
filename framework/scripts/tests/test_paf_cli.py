#!/usr/bin/env python3
"""Behavioral unit tests for project-agent-factory/bin/paf CLI."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

FACTORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PAF_BIN = FACTORY_ROOT / "bin" / "paf"


def init_temp_git_repo() -> pathlib.Path:
    repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (repo / "test_app.py").write_text(
        "from app import add\nassert add(2, 3) == 5\nprint('unit ok')\n",
        encoding="utf-8",
    )
    verify_cfg = {
        "schema_version": 1,
        "zone": "guarded",
        "default_branch": "main",
        "staging_branch": "stage_test",
        "require_blind_review": True,
        "dune_lint": {"enabled": True},
        "commands": {
            "unit_test": ["python3 test_app.py"],
            "post_merge_check": ["python3 -c \"import app; assert app.add(1, 1) == 2\""],
        },
    }
    (repo / ".agent-verify.json").write_text(json.dumps(verify_cfg, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def run_paf(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PAF_BIN), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    failures = 0
    repo = init_temp_git_repo()

    # 1. `paf verify --mode pre-push` produces JSON + Markdown artifacts and passes
    art_json = repo / ".git" / "verify-test.json"
    art_md = repo / ".git" / "verify-test.md"
    res = run_paf(
        ["verify", "--repo", str(repo), "--mode", "pre-push", "--artifact", str(art_json), "--markdown", str(art_md)],
        cwd=repo,
    )
    if res.returncode != 0 or not art_json.is_file() or not art_md.is_file():
        print(f"FAIL paf verify pre-push: rc={res.returncode}\n{res.stdout}\n{res.stderr}")
        failures += 1
    else:
        payload = json.loads(art_json.read_text(encoding="utf-8"))
        if not payload.get("overall_passed") or len(payload.get("steps", [])) < 2:
            print(f"FAIL paf verify payload invalid: {payload}")
            failures += 1
        else:
            print("PASS paf verify pre-push artifact + dune_lint + unit_test")

    # 2. `paf verify` fails loud when zero command checks are configured (even if dune_lint is enabled!)
    empty_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=empty_repo, check=True, capture_output=True)
    (empty_repo / ".agent-verify.json").write_text(
        json.dumps({"zone": "autonomous", "dune_lint": {"enabled": True}, "commands": {}}),
        encoding="utf-8",
    )
    res_empty = run_paf(["verify", "--repo", str(empty_repo)], cwd=empty_repo)
    if res_empty.returncode == 0:
        print("FAIL lint-only config with empty commands should fail loud without allow_lint_only")
        failures += 1
    else:
        print("PASS paf verify fails loud when commands dict is empty")

    # 3. `paf record-review` writes SHA-pinned receipt in .git/paf-reviews without dirtying git status
    res_rev = run_paf(
        [
            "record-review",
            "--repo",
            str(repo),
            "--verdict",
            "approve",
            "--reviewer",
            "blind-adversarial-subagent",
            "--summary",
            "Diff and tests verified independently.",
        ],
        cwd=repo,
    )
    status_out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    if res_rev.returncode != 0 or status_out != "":
        print(f"FAIL record-review: rc={res_rev.returncode} status='{status_out}' stderr={res_rev.stderr}")
        failures += 1
    else:
        print("PASS paf record-review pins SHA receipt cleanly outside working tree")

    # 4. `paf ship` refuses on default branch `main`
    res_main = run_paf(["ship", "--repo", str(repo), "--dry-run"], cwd=repo)
    if res_main.returncode == 0:
        print("FAIL paf ship should refuse running directly on default branch")
        failures += 1
    else:
        print("PASS paf ship blocks shipping directly from main")

    # 5. `paf ship` in guarded zone ALSO blocks when Blind Review receipt is missing for new HEAD
    subprocess.run(["git", "checkout", "-b", "feature/test-add"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: add sub"], cwd=repo, check=True, capture_output=True)

    res_unreviewed = run_paf(["ship", "--repo", str(repo), "--base", "main", "--dry-run"], cwd=repo)
    if res_unreviewed.returncode == 0:
        print("FAIL guarded zone should require Blind Review receipt before pausing for approval")
        failures += 1
    else:
        print("PASS guarded zone blocks unreviewed commit before opening PR / requesting approval")

    # 6. Once Blind Review approves new HEAD, guarded zone pauses cleanly at main gate until --approved-main
    run_paf(
        [
            "record-review",
            "--repo",
            str(repo),
            "--verdict",
            "approve",
            "--reviewer",
            "blind-subagent",
            "--summary",
            "LGTM",
        ],
        cwd=repo,
    )
    res_guarded = run_paf(["ship", "--repo", str(repo), "--base", "main", "--dry-run"], cwd=repo)
    if res_guarded.returncode != 0 or "PAUSED_FOR_MAIN_APPROVAL" not in res_guarded.stdout:
        print(f"FAIL guarded main dry-run: rc={res_guarded.returncode}\n{res_guarded.stdout}\n{res_guarded.stderr}")
        failures += 1
    else:
        print("PASS guarded zone pauses cleanly at main gate until --approved-main")

    # 7. `paf ship --dry-run --approved-main` proceeds through full autonomous plan
    res_approved = run_paf(
        ["ship", "--repo", str(repo), "--base", "main", "--approved-main", "--dry-run"],
        cwd=repo,
    )
    if res_approved.returncode != 0 or "DRY_RUN_AUTONOMOUS_MERGE_READY" not in res_approved.stdout:
        print(f"FAIL approved-main dry-run: rc={res_approved.returncode}\n{res_approved.stdout}\n{res_approved.stderr}")
        failures += 1
    else:
        print("PASS guarded zone with --approved-main unlocks full merge+verify+revert loop")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
