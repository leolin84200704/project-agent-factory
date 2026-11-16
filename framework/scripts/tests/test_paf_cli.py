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

    # 8. Dual-model PR consensus (`paf check-consensus`): rejects same-family and stale-SHA approvals
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    pr_fixture = repo / ".git" / "pr-fixture.json"

    def write_pr_fixture(head_oid: str, body: str, comments: list[dict[str, str]], base: str = "main") -> None:
        pr_fixture.write_text(
            json.dumps(
                {
                    "number": 88,
                    "url": "https://example.test/pr/88",
                    "state": "OPEN",
                    "headRefName": "feature/test-add",
                    "baseRefName": base,
                    "headRefOid": head_oid,
                    "body": body,
                    "comments": comments,
                }
            ),
            encoding="utf-8",
        )

    author_tag = f'<!-- paf-consensus: {{"role":"author","model_family":"claude","head_sha":"{head_sha}"}} -->'
    same_family_approve = (
        f'LGTM\n<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"approve","head_sha":"{head_sha}","round":1}} -->'
    )
    write_pr_fixture(head_sha, author_tag, [{"body": same_family_approve}])
    res_same_fam = run_paf(
        ["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)],
        cwd=repo,
    )
    if res_same_fam.returncode == 0:
        print("FAIL check-consensus should reject same-family approval")
        failures += 1
    else:
        print("PASS check-consensus rejects same-family reviewer")

    stale_approve = (
        'LGTM\n<!-- paf-consensus: {"role":"reviewer","model_family":"gemini","verdict":"approve","head_sha":"0000000000000000000000000000000000000000","round":1} -->'
    )
    write_pr_fixture(head_sha, author_tag, [{"body": stale_approve}])
    res_stale = run_paf(
        ["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)],
        cwd=repo,
    )
    if res_stale.returncode == 0:
        print("FAIL check-consensus should reject stale SHA approval")
        failures += 1
    else:
        print("PASS check-consensus rejects stale SHA approval")

    # 9. Heterogeneous SHA-pinned approval (`claude` author + `gemini` reviewer) passes, and subsequent request_changes blocks
    cross_approve = (
        f'Verified tests.\n<!-- paf-consensus: {{"verdict":"approve","head_sha":"{head_sha}","reviewer":"gemini","round":2}} -->'
    )
    write_pr_fixture(head_sha, author_tag, [{"body": cross_approve}])
    res_cross_ok = run_paf(
        ["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--json"],
        cwd=repo,
    )
    if res_cross_ok.returncode != 0 or json.loads(res_cross_ok.stdout).get("status") != "approved":
        print(f"FAIL check-consensus heterogeneous approve: rc={res_cross_ok.returncode}\n{res_cross_ok.stdout}\n{res_cross_ok.stderr}")
        failures += 1
    else:
        print("PASS check-consensus approves heterogeneous SHA-pinned consensus")

    later_reject = (
        f'Found regression.\n<!-- paf-consensus: {{"verdict":"request_changes","head_sha":"{head_sha}","reviewer":"gemini","round":2}} -->'
    )
    write_pr_fixture(head_sha, author_tag, [{"body": cross_approve}, {"body": later_reject}])
    res_later_reject = run_paf(
        ["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)],
        cwd=repo,
    )
    if res_later_reject.returncode == 0:
        print("FAIL check-consensus should block when newer request_changes exists on same SHA")
        failures += 1
    else:
        print("PASS check-consensus honors latest verdict on current SHA")

    # 10. Deadlock (`round > max_rounds`) or `needs_human_decision: true` triggers PAUSED_FOR_CONSENSUS_TIEBREAK (rc=4)
    deadlock_comment = (
        f'Still disagree.\n<!-- paf-consensus: {{"verdict":"request_changes","head_sha":"{head_sha}","reviewer":"gemini","round":4}} -->'
    )
    write_pr_fixture(head_sha, author_tag, [{"body": deadlock_comment}])
    res_deadlock = run_paf(
        ["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)],
        cwd=repo,
    )
    if res_deadlock.returncode != 4 or "PAUSED_FOR_CONSENSUS_TIEBREAK" not in (res_deadlock.stdout + res_deadlock.stderr):
        print(f"FAIL deadlock tiebreak: rc={res_deadlock.returncode}\n{res_deadlock.stdout}\n{res_deadlock.stderr}")
        failures += 1
    else:
        print("PASS check-consensus escalates >max_rounds deadlock to PAUSED_FOR_CONSENSUS_TIEBREAK")

    # 11. `paf merge-consensus --dry-run` auto-merges on autonomous / guarded-staging and pauses once on guarded-main
    write_pr_fixture(head_sha, author_tag, [{"body": cross_approve}], base="stage_test")
    res_mc_staging = run_paf(
        ["merge-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    if res_mc_staging.returncode != 0 or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in res_mc_staging.stdout:
        print(f"FAIL merge-consensus guarded staging: rc={res_mc_staging.returncode}\n{res_mc_staging.stdout}\n{res_mc_staging.stderr}")
        failures += 1
    else:
        print("PASS merge-consensus auto-merges guarded staging upon dual-model consensus")

    write_pr_fixture(head_sha, author_tag, [{"body": cross_approve}], base="main")
    res_mc_guarded_main = run_paf(
        ["merge-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    res_mc_guarded_approved = run_paf(
        ["merge-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--approved-main", "--dry-run"],
        cwd=repo,
    )
    if (
        res_mc_guarded_main.returncode != 0
        or "PAUSED_FOR_MAIN_APPROVAL" not in res_mc_guarded_main.stdout
        or res_mc_guarded_approved.returncode != 0
        or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in res_mc_guarded_approved.stdout
    ):
        print("FAIL merge-consensus guarded main gate behavior")
        failures += 1
    else:
        print("PASS merge-consensus pauses once on guarded main and auto-merges with --approved-main")

    # 12. `paf ship --open-for-consensus --author-family gemini --dry-run` works on a fresh commit without local .git/paf-reviews
    (repo / "app.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n\ndef mul(a: int, b: int) -> int:\n    return a * b\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: add mul"], cwd=repo, check=True, capture_output=True)
    res_open_cons = run_paf(
        ["ship", "--repo", str(repo), "--open-for-consensus", "--author-family", "gemini", "--dry-run"],
        cwd=repo,
    )
    if res_open_cons.returncode != 0 or "DRY_RUN_OPEN_FOR_CONSENSUS_READY" not in res_open_cons.stdout:
        print(f"FAIL ship --open-for-consensus: rc={res_open_cons.returncode}\n{res_open_cons.stdout}\n{res_open_cons.stderr}")
        failures += 1
    else:
        print("PASS paf ship --open-for-consensus prepares PR for cross-model review without local self-review")

    # 13. B1: Unknown `author_family` or unrecognized/empty `reviewer` family MUST fail closed (both check-consensus and paf ship)
    mul_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    unlabeled_approve = f'<!-- paf-consensus: {{"verdict":"approve","head_sha":"{mul_sha}"}} -->'
    claude_approve_mul = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"approve","head_sha":"{mul_sha}","round":1}} -->'
    )
    unknown_fam_approve = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"mystery-bot","verdict":"approve","head_sha":"{mul_sha}","round":1}} -->'
    )
    gemini_author_mul = f'<!-- paf-consensus: {{"role":"author","model_family":"gemini","verdict":"open","head_sha":"{mul_sha}"}} -->'

    write_pr_fixture(mul_sha, "Manual PR body without author marker", [{"body": unlabeled_approve}])
    res_b1_unlabeled = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    write_pr_fixture(mul_sha, "Manual PR body without author marker", [{"body": claude_approve_mul}])
    res_b1_no_author = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)
    res_b1_ship_no_author = run_paf(
        ["ship", "--repo", str(repo), "--base", "stage_test", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )

    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": unknown_fam_approve}])
    res_b1_unknown_rev = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    if (
        res_b1_unlabeled.returncode == 0
        or res_b1_no_author.returncode == 0
        or res_b1_ship_no_author.returncode == 0
        or res_b1_unknown_rev.returncode == 0
    ):
        print(
            "FAIL B1 fail-closed identity check: "
            f"unlabeled={res_b1_unlabeled.returncode} no_author={res_b1_no_author.returncode} "
            f"ship_no_author={res_b1_ship_no_author.returncode} unknown_rev={res_b1_unknown_rev.returncode}"
        )
        failures += 1
    else:
        print("PASS B1 fails closed when author_family or reviewer model_family is missing/unknown")

    # 14. B2: `paf post-consensus --role reviewer` requires explicit `--head-sha` and rejects moved HEAD
    write_pr_fixture(mul_sha, gemini_author_mul, [])
    res_b2_missing_sha = run_paf(
        [
            "post-consensus",
            "--repo",
            str(repo),
            "--pr",
            "88",
            "--pr-fixture",
            str(pr_fixture),
            "--role",
            "reviewer",
            "--family",
            "claude",
            "--verdict",
            "approve",
        ],
        cwd=repo,
    )
    res_b2_moved_sha = run_paf(
        [
            "post-consensus",
            "--repo",
            str(repo),
            "--pr",
            "88",
            "--pr-fixture",
            str(pr_fixture),
            "--role",
            "reviewer",
            "--family",
            "claude",
            "--verdict",
            "approve",
            "--head-sha",
            "0000000000000000",
        ],
        cwd=repo,
    )
    if (
        res_b2_missing_sha.returncode != 2
        or "--head-sha" not in res_b2_missing_sha.stderr
        or res_b2_moved_sha.returncode != 2
        or "moved to" not in res_b2_moved_sha.stderr
    ):
        print(
            f"FAIL B2 write-time SHA pinning: missing={res_b2_missing_sha.returncode} ({res_b2_missing_sha.stderr.strip()}) "
            f"moved={res_b2_moved_sha.returncode} ({res_b2_moved_sha.stderr.strip()})"
        )
        failures += 1
    else:
        print("PASS B2 enforces required --head-sha and rejects concurrent HEAD movement at post time")

    # 15. B3: `paf ship --dry-run` evaluates live/fixture PR consensus identically to real ship
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": claude_approve_mul}], base="stage_test")
    res_b3_stage = run_paf(
        ["ship", "--repo", str(repo), "--base", "stage_test", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": claude_approve_mul}], base="main")
    res_b3_main = run_paf(
        ["ship", "--repo", str(repo), "--base", "main", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    if (
        res_b3_stage.returncode != 0
        or "DRY_RUN_AUTONOMOUS_MERGE_READY" not in res_b3_stage.stdout
        or res_b3_main.returncode != 0
        or "PAUSED_FOR_MAIN_APPROVAL" not in res_b3_main.stdout
    ):
        print(
            f"FAIL B3 ship --dry-run consensus fidelity: stage={res_b3_stage.returncode} ({res_b3_stage.stdout.strip()}) "
            f"main={res_b3_main.returncode} ({res_b3_main.stdout.strip()})"
        )
        failures += 1
    else:
        print("PASS B3 paf ship --dry-run honors heterogeneous PR consensus identically to real ship")

    # 16. Edge cases: needs_human_decision=true, malformed markers, stale-SHA round scoping, and --max-rounds 0
    needs_human_comment = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"request_changes","head_sha":"{mul_sha}","round":1,"needs_human_decision":true}} -->'
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": needs_human_comment}])
    res_nh = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    malformed_comment = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"approve","head_sha":"{mul_sha}","round":"two","blocking_count":"nan"}} -->'
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": malformed_comment}])
    res_malformed_only = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": malformed_comment}, {"body": claude_approve_mul}])
    res_malformed_then_valid = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    stale_high_round = (
        '<!-- paf-consensus: {"role":"reviewer","model_family":"claude","verdict":"request_changes","head_sha":"0000000000000000000000000000000000000000","round":9} -->'
    )
    current_r1_req = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"request_changes","head_sha":"{mul_sha}","round":1}} -->'
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": stale_high_round}, {"body": current_r1_req}])
    res_stale_round = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)
    res_zero_max_rounds = run_paf(
        ["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--max-rounds", "0"],
        cwd=repo,
    )

    if (
        res_nh.returncode != 4
        or "PAUSED_FOR_CONSENSUS_TIEBREAK" not in (res_nh.stdout + res_nh.stderr)
        or res_malformed_only.returncode != 2
        or "Traceback" in res_malformed_only.stderr
        or res_malformed_then_valid.returncode != 0
        or res_stale_round.returncode != 2
        or res_zero_max_rounds.returncode != 4
    ):
        print(
            f"FAIL edge cases: nh={res_nh.returncode} malformed_only={res_malformed_only.returncode} "
            f"malformed_valid={res_malformed_then_valid.returncode} stale_round={res_stale_round.returncode} "
            f"zero_max={res_zero_max_rounds.returncode}"
        )
        failures += 1
    else:
        print("PASS needs_human_decision, malformed marker tolerance, active-SHA round scoping, and --max-rounds 0")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
