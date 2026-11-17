#!/usr/bin/env python3
"""Behavioral unit tests for project-agent-factory/bin/paf CLI."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

FACTORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PAF_BIN = FACTORY_ROOT / "bin" / "paf"
HERMETIC_KEY_DIR = pathlib.Path(tempfile.mkdtemp(prefix="paf-cli-test-key-"))
HERMETIC_KEY_FILE = HERMETIC_KEY_DIR / "consensus.key"
_SCRUBBED_CALLER_ENV = (
    "PAF_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDECODE",
    "AGY_CONVERSATION_ID",
    "PAF_AUTHOR_FAMILY",
    "PAF_CONSENSUS_KEY",
    "PAF_CONSENSUS_KEY_FILE",
)


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


def run_paf(
    args: list[str],
    cwd: pathlib.Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for k in _SCRUBBED_CALLER_ENV:
        env.pop(k, None)
    env["PAF_CONSENSUS_KEY_FILE"] = str(HERMETIC_KEY_FILE)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(PAF_BIN), *args],
        cwd=cwd,
        env=env,
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

    def write_pr_fixture(
        head_oid: str,
        body: str,
        comments: list[dict[str, str]],
        base: str = "main",
        files: list[str] | None = None,
        review_decision: str = "",
    ) -> None:
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
                    "files": [{"path": p} for p in (files if files is not None else ["app.py"])],
                    "reviewDecision": review_decision,
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

    # 17. C1: Guarded non-staging base (`master`, `release/1.0`) pauses for approval, and missing `zone` key fails closed
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": claude_approve_mul}], base="master")
    res_c1_master = run_paf(
        ["merge-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    res_c1_ship_master = run_paf(
        ["ship", "--repo", str(repo), "--base", "master", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    no_zone_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=no_zone_repo, check=True, capture_output=True)
    (no_zone_repo / ".agent-verify.json").write_text(
        json.dumps({"default_branch": "main", "commands": {"unit_test": ["true"]}}),
        encoding="utf-8",
    )
    res_c1_no_zone = run_paf(["verify", "--repo", str(no_zone_repo)], cwd=no_zone_repo)
    if (
        res_c1_master.returncode != 0
        or "PAUSED_FOR_MAIN_APPROVAL" not in res_c1_master.stdout
        or res_c1_ship_master.returncode != 0
        or "PAUSED_FOR_MAIN_APPROVAL" not in res_c1_ship_master.stdout
        or res_c1_no_zone.returncode == 0
    ):
        print(
            f"FAIL C1 guarded allowlist & missing-zone check: master={res_c1_master.stdout.strip()} "
            f"ship_master={res_c1_ship_master.stdout.strip()} no_zone_rc={res_c1_no_zone.returncode}"
        )
        failures += 1
    else:
        print("PASS C1 guarded repo pauses on every non-staging base branch and fails closed on missing zone")

    # 18. C3 & C4: Governance/permission file edits (`CORE.md`, `.agent-verify.json`, etc.) merge on consensus by default;
    #     with `governance_requires_human` the autonomous repo pauses once until `--approved-governance`, and blocks when GitHub `reviewDecision` requires human review
    auto_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=auto_repo, check=True, capture_output=True)
    (auto_repo / ".agent-verify.json").write_text(
        json.dumps({"zone": "autonomous", "default_branch": "main", "commands": {"unit_test": ["true"]}}),
        encoding="utf-8",
    )
    auto_pr_fix = auto_repo / ".git" / "pr-auto.json"
    auto_pr_fix.write_text(
        json.dumps(
            {
                "number": 115,
                "url": "https://example.test/pr/115",
                "state": "OPEN",
                "headRefName": "feature/rules",
                "baseRefName": "main",
                "headRefOid": mul_sha,
                "body": gemini_author_mul,
                "comments": [{"body": claude_approve_mul}],
                "files": [{"path": "CORE.md"}, {"path": "bin/paf"}],
                "reviewDecision": "",
            }
        ),
        encoding="utf-8",
    )
    # Default (Leo, 2026-11-16): verified dual-model consensus alone merges governance-file PRs, no pause
    res_c3_default = run_paf(
        ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
        cwd=auto_repo,
    )
    if (
        res_c3_default.returncode != 0
        or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in res_c3_default.stdout
        or "PAUSED_FOR_GOVERNANCE_APPROVAL" in res_c3_default.stdout
    ):
        print(f"FAIL C3a governance PR with consensus should merge by default: {res_c3_default.stdout.strip()}")
        failures += 1
    else:
        print("PASS C3a governance-file PR with verified dual-model consensus merges without Leo's approval")
    # Opt-in `governance_requires_human` restores the single human pause
    (auto_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "zone": "autonomous",
                "default_branch": "main",
                "governance_requires_human": True,
                "commands": {"unit_test": ["true"]},
            }
        ),
        encoding="utf-8",
    )
    res_c3_pause = run_paf(
        ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
        cwd=auto_repo,
    )
    res_c3_approved_gov = run_paf(
        [
            "merge-consensus",
            "--repo",
            str(auto_repo),
            "--pr-fixture",
            str(auto_pr_fix),
            "--approved-governance",
            "--dry-run",
        ],
        cwd=auto_repo,
    )
    write_pr_fixture(
        mul_sha,
        gemini_author_mul,
        [{"body": claude_approve_mul}],
        base="stage_test",
        review_decision="REVIEW_REQUIRED",
    )
    res_c4_human_req = run_paf(
        ["merge-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    if (
        res_c3_pause.returncode != 0
        or "PAUSED_FOR_GOVERNANCE_APPROVAL" not in res_c3_pause.stdout
        or res_c3_approved_gov.returncode != 0
        or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in res_c3_approved_gov.stdout
        or res_c4_human_req.returncode == 0
    ):
        print(
            f"FAIL C3b/C4 governance opt-in & human-review gates: pause={res_c3_pause.stdout.strip()} "
            f"approved={res_c3_approved_gov.stdout.strip()} c4_rc={res_c4_human_req.returncode}"
        )
        failures += 1
    else:
        print("PASS C3b/C4 governance_requires_human pauses until --approved-governance, and honors GitHub human review")

    # 19. D1: Local `paf record-review` receipt NEVER overrides an open PR consensus gate (`request_changes`, `pending_review`, `needs_human_decision`)
    res_rec = run_paf(
        [
            "record-review",
            "--repo",
            str(repo),
            "--verdict",
            "approve",
            "--reviewer",
            "local-subagent",
            "--summary",
            "Local review pass",
        ],
        cwd=repo,
    )
    claude_req_block2 = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"request_changes","head_sha":"{mul_sha}","round":1,"blocking_count":2}} -->'
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": claude_req_block2}], base="stage_test")
    res_d1_req = run_paf(
        ["ship", "--repo", str(repo), "--base", "stage_test", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [{"body": needs_human_comment}], base="stage_test")
    res_d1_nh = run_paf(
        ["ship", "--repo", str(repo), "--base", "stage_test", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    write_pr_fixture(mul_sha, gemini_author_mul, [], base="stage_test")
    res_d1_pending = run_paf(
        ["ship", "--repo", str(repo), "--base", "stage_test", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    write_pr_fixture(mul_sha, "Plain PR body with zero consensus markers", [], base="stage_test")
    res_d1_plain_local = run_paf(
        ["ship", "--repo", str(repo), "--base", "stage_test", "--pr-fixture", str(pr_fixture), "--dry-run"],
        cwd=repo,
    )
    if (
        res_rec.returncode != 0
        or res_d1_req.returncode != 2
        or res_d1_nh.returncode != 4
        or res_d1_pending.returncode != 2
        or res_d1_plain_local.returncode != 0
        or "DRY_RUN_AUTONOMOUS_MERGE_READY" not in res_d1_plain_local.stdout
    ):
        print(
            f"FAIL D1 local receipt bypass check: rec={res_rec.returncode} req={res_d1_req.returncode} "
            f"nh={res_d1_nh.returncode} pending={res_d1_pending.returncode} plain={res_d1_plain_local.returncode}"
        )
        failures += 1
    else:
        print("PASS D1 local review receipt never overrides PR consensus objections, pending review, or human tiebreak")

    # 20. D2 + alias hygiene: Same-family or unlabeled `request_changes` / `needs_human_decision` blocks merge,
    #     and substring collisions like `pro1` / `info1` / `gemini (claude subagent)` never approve
    claude_author_mul = (
        f'<!-- paf-consensus: {{"role":"author","model_family":"claude","verdict":"open","head_sha":"{mul_sha}"}} -->'
    )
    gemini_approve_mul = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"gemini","verdict":"approve","head_sha":"{mul_sha}","round":1}} -->'
    )
    claude_req_mul = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"request_changes","head_sha":"{mul_sha}","round":1,"blocking_count":1}} -->'
    )
    unlabeled_req_mul = (
        f'<!-- paf-consensus: {{"role":"reviewer","verdict":"request_changes","head_sha":"{mul_sha}","round":1,"blocking_count":1}} -->'
    )
    claude_nh_mul = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"claude","verdict":"request_changes","head_sha":"{mul_sha}","round":1,"needs_human_decision":true}} -->'
    )
    write_pr_fixture(mul_sha, claude_author_mul, [{"body": gemini_approve_mul}, {"body": claude_req_mul}])
    res_d2_same_after = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    write_pr_fixture(mul_sha, claude_author_mul, [{"body": claude_req_mul}, {"body": gemini_approve_mul}])
    res_d2_same_before = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    write_pr_fixture(mul_sha, claude_author_mul, [{"body": gemini_approve_mul}, {"body": unlabeled_req_mul}])
    res_d2_unlabeled = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    write_pr_fixture(mul_sha, claude_author_mul, [{"body": gemini_approve_mul}, {"body": claude_nh_mul}])
    res_d2_same_nh = run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo)

    alias_bad_results = []
    for bad_fam in ("pro1", "info1", "gemini (claude subagent)"):
        bad_approve = (
            f'<!-- paf-consensus: {{"role":"reviewer","model_family":"{bad_fam}","verdict":"approve","head_sha":"{mul_sha}","round":1}} -->'
        )
        write_pr_fixture(mul_sha, gemini_author_mul, [{"body": bad_approve}])
        alias_bad_results.append(
            run_paf(["check-consensus", "--repo", str(repo), "--pr-fixture", str(pr_fixture)], cwd=repo).returncode
        )

    if (
        res_d2_same_after.returncode != 2
        or res_d2_same_before.returncode != 2
        or res_d2_unlabeled.returncode != 2
        or res_d2_same_nh.returncode != 4
        or any(rc == 0 for rc in alias_bad_results)
    ):
        print(
            f"FAIL D2 objection & alias check: same_after={res_d2_same_after.returncode} "
            f"same_before={res_d2_same_before.returncode} unlabeled={res_d2_unlabeled.returncode} "
            f"same_nh={res_d2_same_nh.returncode} aliases={alias_bad_results}"
        )
        failures += 1
    else:
        print("PASS D2 honors same-family/unlabeled objections and rejects substring family collisions")

    # 21. D3: Governance, workflow, settings, skill, and hook PRs in autonomous repos merge directly once
    #     heterogeneous model consensus approves by default, pause when `governance_requires_human` is enabled,
    #     and block when peer model requests changes
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=auto_repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=auto_repo, check=True)
    (auto_repo / ".agent-verify.json").write_text(
        json.dumps({"zone": "autonomous", "default_branch": "main", "commands": {"unit_test": ["true"]}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".agent-verify.json"], cwd=auto_repo, check=True)
    subprocess.run(["git", "commit", "-m", "base default cfg"], cwd=auto_repo, check=True, capture_output=True)
    d3_paths = (
        ".agent-verify.json",
        ".github/workflows/verify.yml",
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".claude/skills/ship/SKILL.md",
        ".gemini/settings.json",
        "framework/WORK-LOOP.md",
        "GEMINI.md",
        "skills/verify/SKILL.md",
        "framework/githooks/commit-msg",
    )
    d3_ok = True
    for gov_path in d3_paths:
        auto_pr_fix.write_text(
            json.dumps(
                {
                    "number": 115,
                    "url": "https://example.test/pr/115",
                    "state": "OPEN",
                    "headRefName": "feature/rules",
                    "baseRefName": "main",
                    "headRefOid": mul_sha,
                    "body": gemini_author_mul,
                    "comments": [{"body": claude_approve_mul}],
                    "files": [{"path": gov_path}],
                    "changedFiles": 1,
                    "reviewDecision": "",
                }
            ),
            encoding="utf-8",
        )
        r_gov = run_paf(
            ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
            cwd=auto_repo,
        )
        if (
            r_gov.returncode != 0
            or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in r_gov.stdout
            or "PAUSED_FOR_GOVERNANCE_APPROVAL" in r_gov.stdout
        ):
            d3_ok = False
            print(f"FAIL D3 default autonomous consensus merge blocked on {gov_path}: {r_gov.stdout.strip()}")

    (auto_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "zone": "autonomous",
                "default_branch": "main",
                "governance_requires_human": True,
                "commands": {"unit_test": ["true"]},
            }
        ),
        encoding="utf-8",
    )
    for gov_path in d3_paths:
        auto_pr_fix.write_text(
            json.dumps(
                {
                    "number": 115,
                    "url": "https://example.test/pr/115",
                    "state": "OPEN",
                    "headRefName": "feature/rules",
                    "baseRefName": "main",
                    "headRefOid": mul_sha,
                    "body": gemini_author_mul,
                    "comments": [{"body": claude_approve_mul}],
                    "files": [{"path": gov_path}],
                    "changedFiles": 1,
                    "reviewDecision": "",
                }
            ),
            encoding="utf-8",
        )
        r_gov_opt = run_paf(
            ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
            cwd=auto_repo,
        )
        if r_gov_opt.returncode != 0 or "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_gov_opt.stdout:
            d3_ok = False
            print(f"FAIL D3 governance_requires_human missed {gov_path}: {r_gov_opt.stdout.strip()}")

    auto_pr_fix.write_text(
        json.dumps(
            {
                "number": 115,
                "url": "https://example.test/pr/115",
                "state": "OPEN",
                "headRefName": "feature/rules",
                "baseRefName": "main",
                "headRefOid": mul_sha,
                "body": gemini_author_mul,
                "comments": [{"body": claude_req_block2}],
                "files": [{"path": "CORE.md"}],
                "changedFiles": 1,
                "reviewDecision": "",
            }
        ),
        encoding="utf-8",
    )
    r_gov_unapproved = run_paf(
        ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
        cwd=auto_repo,
    )
    if not d3_ok or r_gov_unapproved.returncode != 2:
        print(f"FAIL D3 peer-model governance check: d3_ok={d3_ok} unapproved_rc={r_gov_unapproved.returncode}")
        failures += 1
    else:
        print("PASS D3 confirms governance/hook/workflow PRs via peer model by default and honors governance_requires_human")

    # 22. Truncated or missing PR file list: the local `git diff` fallback counts only when local HEAD is the PR head
    fb_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=fb_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=fb_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=fb_repo, check=True)
    (fb_repo / ".agent-verify.json").write_text(
        json.dumps({"version": 1, "zone": "autonomous", "default_branch": "main", "governance_requires_human": True}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=fb_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=fb_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature/plain"], cwd=fb_repo, check=True, capture_output=True)
    for name in ("a.py", "b.py", "c.py"):
        (fb_repo / name).write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=fb_repo, check=True)
    subprocess.run(["git", "commit", "-m", "plain files"], cwd=fb_repo, check=True, capture_output=True)
    plain_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fb_repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    fb_fix = fb_repo / ".git" / "pr-fallback.json"
    other_sha = "d" * 40

    def write_fb_fixture(head_oid: str, files: list[str] | None, changed: int | None) -> None:
        author = "<!-- paf-consensus: " + json.dumps(
            {"v": 1, "role": "author", "model_family": "gemini", "verdict": "open", "head_sha": head_oid}
        ) + " -->"
        approve = "<!-- paf-consensus: " + json.dumps(
            {
                "v": 1,
                "role": "reviewer",
                "model_family": "claude",
                "verdict": "approve",
                "head_sha": head_oid,
                "round": 1,
                "blocking_count": 0,
            }
        ) + " -->"
        data: dict[str, object] = {
            "number": 116,
            "url": "https://example.test/pr/116",
            "state": "OPEN",
            "headRefName": "feature/remote",
            "baseRefName": "main",
            "headRefOid": head_oid,
            "body": author,
            "comments": [{"body": approve}],
            "reviewDecision": "",
        }
        if files is not None:
            data["files"] = [{"path": f} for f in files]
        if changed is not None:
            data["changedFiles"] = changed
        fb_fix.write_text(json.dumps(data), encoding="utf-8")

    def fb_dry_run() -> subprocess.CompletedProcess[str]:
        return run_paf(
            ["merge-consensus", "--repo", str(fb_repo), "--pr-fixture", str(fb_fix), "--dry-run"], cwd=fb_repo
        )

    write_fb_fixture(other_sha, ["src/app.py"], 3)
    r_fb_other = fb_dry_run()
    write_fb_fixture(other_sha, None, None)
    r_fb_nofiles = fb_dry_run()
    write_fb_fixture(plain_sha, ["a.py"], 3)
    r_fb_same_plain = fb_dry_run()
    (fb_repo / "CORE.md").write_text("rule\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=fb_repo, check=True)
    subprocess.run(["git", "commit", "-m", "rule file"], cwd=fb_repo, check=True, capture_output=True)
    gov_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fb_repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    write_fb_fixture(gov_sha, ["a.py"], 4)
    r_fb_same_gov = fb_dry_run()
    if (
        "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_fb_other.stdout
        or "truncated-pr-files" not in r_fb_other.stdout
        or "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_fb_nofiles.stdout
        or "unknown-pr-files" not in r_fb_nofiles.stdout
        or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in r_fb_same_plain.stdout
        or "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_fb_same_gov.stdout
        or "CORE.md" not in r_fb_same_gov.stdout
        or "truncated-pr-files" in r_fb_same_gov.stdout
    ):
        print(
            "FAIL file-list fallback bound to PR head: "
            f"other={r_fb_other.stdout.strip()!r} nofiles={r_fb_nofiles.stdout.strip()!r} "
            f"same_plain={r_fb_same_plain.stdout.strip()!r} same_gov={r_fb_same_gov.stdout.strip()!r}"
        )
        failures += 1
    else:
        print("PASS truncated or missing PR file list trusts local git diff only when local HEAD is the PR head")

    # 24. Deploy + Post-Deploy Verification Pipeline: if deploy is configured, execute deploy after post-merge
    #     and immediately re-test after deploy (`post_deploy_check` or fallback stage), failing closed on error
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("paf_cli_test_mod", str(PAF_BIN))
    spec = importlib.util.spec_from_loader("paf_cli_test_mod", loader)
    assert spec is not None and spec.loader is not None
    paf_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paf_mod)

    dep_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=dep_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=dep_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=dep_repo, check=True)
    step_log = dep_repo / "pipeline-steps.log"
    (dep_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "commands": {
                    "unit_test": [f"echo unit_test >> {step_log}"],
                    "post_merge_check": [f"echo post_merge >> {step_log}"],
                    "deploy": [f"echo deploy >> {step_log}"],
                    "post_deploy_check": [f"echo post_deploy >> {step_log}"],
                },
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".agent-verify.json"], cwd=dep_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init deploy repo"], cwd=dep_repo, check=True, capture_output=True)
    dep_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dep_repo, text=True, capture_output=True, check=True
    ).stdout.strip()

    ok_full, tag_full = paf_mod.run_post_merge_and_deploy_checks(
        dep_repo,
        pr_url="https://example.test/pr/118",
        base_branch="main",
        merged_sha=dep_sha,
        host_id="personal-mac",
    )
    seq_full = step_log.read_text(encoding="utf-8").splitlines() if step_log.is_file() else []

    # Fallback check: when `deploy` is set without `post_deploy_check`, `post-deploy` still re-runs `post_merge_check`
    step_log.unlink(missing_ok=True)
    (dep_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "commands": {
                    "unit_test": [f"echo unit_test >> {step_log}"],
                    "post_merge_check": [f"echo post_merge >> {step_log}"],
                    "deploy": [f"echo deploy >> {step_log}"],
                },
            }
        ),
        encoding="utf-8",
    )
    ok_fb, tag_fb = paf_mod.run_post_merge_and_deploy_checks(
        dep_repo,
        pr_url="https://example.test/pr/119",
        base_branch="main",
        merged_sha=dep_sha,
        host_id="personal-mac",
    )
    seq_fb = step_log.read_text(encoding="utf-8").splitlines() if step_log.is_file() else []

    # Host affinity check: `deploy_host` defers deploy when running on a different machine
    step_log.unlink(missing_ok=True)
    (dep_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "deploy_host": "prod-runner",
                "commands": {
                    "post_merge_check": [f"echo post_merge >> {step_log}"],
                    "deploy": [f"echo deploy >> {step_log}"],
                },
            }
        ),
        encoding="utf-8",
    )
    ok_def, tag_def = paf_mod.run_post_merge_and_deploy_checks(
        dep_repo,
        pr_url="https://example.test/pr/120",
        base_branch="main",
        merged_sha=dep_sha,
        host_id="personal-mac",
    )
    seq_def = step_log.read_text(encoding="utf-8").splitlines() if step_log.is_file() else []

    # Failure check: post-deploy failure returns False so auto-revert triggers
    (dep_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "commands": {
                    "post_merge_check": ["true"],
                    "deploy": ["true"],
                    "post_deploy_check": ["false"],
                },
            }
        ),
        encoding="utf-8",
    )
    ok_fail, msg_fail = paf_mod.run_post_merge_and_deploy_checks(
        dep_repo,
        pr_url="https://example.test/pr/121",
        base_branch="main",
        merged_sha=dep_sha,
        host_id="personal-mac",
    )

    if (
        not ok_full
        or tag_full != "shipped_and_verified"
        or seq_full != ["post_merge", "deploy", "post_deploy"]
        or not ok_fb
        or tag_fb != "shipped_and_verified"
        or seq_fb != ["post_merge", "deploy", "post_merge"]
        or not ok_def
        or tag_def != "shipped_deploy_deferred"
        or seq_def != ["post_merge"]
        or ok_fail
        or "POST_DEPLOY_VERIFICATION_FAILED" not in msg_fail
    ):
        print(
            f"FAIL deploy + post-deploy pipeline: full=({ok_full},{tag_full},{seq_full}) "
            f"fb=({ok_fb},{tag_fb},{seq_fb}) def=({ok_def},{tag_def},{seq_def}) fail=({ok_fail},{msg_fail!r})"
        )
        failures += 1
    else:
        print("PASS post-merge -> deploy -> post-deploy re-test pipeline, fallback stage, deploy_host deferral, and failure gate")

    # 23. Production merge_pr_in_process + GitHubBus.merge_pr + caller family anti-spoofing + ship/drive-consensus CLI gates
    live_root = pathlib.Path(tempfile.mkdtemp(prefix="paf-live-merge-test-"))
    bare_origin = live_root / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_origin)], check=True, capture_output=True)

    live_repo = live_root / "work"
    subprocess.run(["git", "clone", str(bare_origin), str(live_repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=live_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=live_repo, check=True)

    live_log = live_root / "live_steps.log"
    live_key_file = live_root / "consensus.key"
    sys.path.insert(0, str(FACTORY_ROOT / "framework" / "scripts"))
    import paf_watcher as pw_test  # type: ignore

    pw_test.init_consensus_key(live_key_file)
    live_secret = pw_test.load_consensus_secret(live_key_file)

    def _write_live_cfg(zone_val: str) -> None:
        (live_repo / ".agent-verify.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "zone": zone_val,
                    "default_branch": "main",
                    "staging_branches": ["staging"],
                    "consensus": {"require_hmac": True, "max_rounds": 3},
                    "commands": {
                        "unit_test": ["true"],
                        "post_merge_check": [f"echo post_merge >> {live_log}"],
                        "deploy": [f"echo deploy >> {live_log}"],
                        "post_deploy_check": [f"echo post_deploy >> {live_log}"],
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    _write_live_cfg("autonomous")
    (live_repo / "mod.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", ".agent-verify.json", "mod.py"], cwd=live_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init main"], cwd=live_repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=live_repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "feature/in-proc-merge"], cwd=live_repo, check=True, capture_output=True)
    (live_repo / "mod.py").write_text("X = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "mod.py"], cwd=live_repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature change"], cwd=live_repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "feature/in-proc-merge"], cwd=live_repo, check=True, capture_output=True)
    feat_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=live_repo, text=True, capture_output=True, check=True
    ).stdout.strip()

    # 23a. Caller family inference + anti-spoofing + `paf ship --consensus --dry-run` + NB1 `reviewDecision` gate
    r_spoof_ship = run_paf(
        ["ship", "--repo", str(live_repo), "--consensus", "--author-family", "claude", "--dry-run"],
        cwd=live_repo,
        extra_env={"AGY_CONVERSATION_ID": "agy-caller-123"},
    )
    r_spoof_post = run_paf(
        [
            "post-consensus",
            "--repo",
            str(live_repo),
            "--pr",
            "200",
            "--pr-fixture",
            str(auto_pr_fix),
            "--role",
            "reviewer",
            "--family",
            "gemini",
            "--verdict",
            "request_changes",
            "--head-sha",
            mul_sha,
            "--blocking-count",
            "1",
        ],
        cwd=live_repo,
        extra_env={"CLAUDECODE": "1"},
    )
    r_infer_ship = run_paf(
        ["ship", "--repo", str(live_repo), "--consensus", "--dry-run"],
        cwd=live_repo,
        extra_env={"AGY_CONVERSATION_ID": "agy-caller-123"},
    )

    pr_state_file = live_root / "mock_gh_pr.json"
    auth_m_200 = {
        "v": 1,
        "role": "author",
        "model_family": "gemini",
        "verdict": "open",
        "head_sha": feat_sha,
        "host": pw_test.resolve_host_id(None),
        "base_branch": "main",
        "pr_number": 200,
        "session_id": "agy-caller-123",
    }
    pw_test.sign_marker_in_place(auth_m_200, live_secret, key_file=live_key_file, require_hmac=True)
    rev_m_200 = {
        "v": 1,
        "role": "reviewer",
        "model_family": "claude",
        "verdict": "approve",
        "head_sha": feat_sha,
        "round": 1,
        "blocking_count": 0,
        "needs_human_decision": False,
        "host": pw_test.resolve_host_id(None),
        "base_branch": "main",
        "pr_number": 200,
        "session_id": "22222222-2222-2222-2222-222222222222",
    }
    pw_test.sign_marker_in_place(rev_m_200, live_secret, key_file=live_key_file, require_hmac=True)

    def _write_pr_state(rev_decision: str = "", comments_list: list[dict] | None = None) -> None:
        pr_state_file.write_text(
            json.dumps(
                {
                    "number": 200,
                    "url": "https://example.test/pr/200",
                    "state": "OPEN",
                    "headRefName": "feature/in-proc-merge",
                    "baseRefName": "main",
                    "headRefOid": feat_sha,
                    "body": f"Feature\n\n<!-- paf-consensus: {json.dumps(auth_m_200, separators=(',', ':'))} -->",
                    "comments": comments_list
                    if comments_list is not None
                    else [{"body": f"Approved.\n\n<!-- paf-consensus: {json.dumps(rev_m_200, separators=(',', ':'))} -->"}],
                    "files": [{"path": "mod.py"}],
                    "changedFiles": 1,
                    "reviewDecision": rev_decision,
                }
            ),
            encoding="utf-8",
        )

    def _set_committed_live_zone(zone_val: str) -> str:
        _write_live_cfg(zone_val)
        subprocess.run(["git", "add", ".agent-verify.json"], cwd=live_repo, check=True)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], cwd=live_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "push", "-f", "origin", "feature/in-proc-merge"],
            cwd=live_repo,
            check=True,
            capture_output=True,
        )
        sha_now = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=live_repo, text=True, capture_output=True, check=True
        ).stdout.strip()
        auth_m_200["head_sha"] = sha_now
        auth_m_200.pop("sig", None)
        auth_m_200.pop("key_id", None)
        pw_test.sign_marker_in_place(auth_m_200, live_secret, key_file=live_key_file, require_hmac=True)
        rev_m_200["head_sha"] = sha_now
        rev_m_200.pop("sig", None)
        rev_m_200.pop("key_id", None)
        pw_test.sign_marker_in_place(rev_m_200, live_secret, key_file=live_key_file, require_hmac=True)
        return sha_now

    # NB1: guarded REVIEW_REQUIRED and any CHANGES_REQUESTED block `paf ship --consensus --dry-run`
    feat_sha = _set_committed_live_zone("guarded")
    _write_pr_state(rev_decision="REVIEW_REQUIRED")
    r_guarded_rev_req = run_paf(
        [
            "ship",
            "--repo",
            str(live_repo),
            "--pr-fixture",
            str(pr_state_file),
            "--consensus",
            "--author-family",
            "gemini",
            "--dry-run",
        ],
        cwd=live_repo,
    )
    feat_sha = _set_committed_live_zone("autonomous")
    _write_pr_state(rev_decision="CHANGES_REQUESTED")
    r_changes_req = run_paf(
        [
            "ship",
            "--repo",
            str(live_repo),
            "--pr-fixture",
            str(pr_state_file),
            "--consensus",
            "--author-family",
            "gemini",
            "--dry-run",
        ],
        cwd=live_repo,
    )

    # 23b. Mock `gh` binary to exercise `merge_pr_in_process`, `GitHubBus.merge_pr`, and `paf drive-consensus`
    mock_gh_dir = live_root / "mock_bin"
    mock_gh_dir.mkdir(parents=True, exist_ok=True)
    mock_gh = mock_gh_dir / "gh"
    mock_gh.write_text(
        f"""#!/usr/bin/env python3
import json, pathlib, subprocess, sys
args = sys.argv[1:]
pr_file = pathlib.Path({str(pr_state_file)!r})
bare = pathlib.Path({str(bare_origin)!r})
slog = pathlib.Path({str(live_log)!r})
if len(args) >= 2 and args[0] == "pr" and args[1] == "checks":
    print(json.dumps([{{"name": "verify", "state": "SUCCESS", "bucket": "pass", "link": "https://example.test/c/1"}}]))
    sys.exit(0)
if len(args) >= 2 and args[0] == "pr" and args[1] == "view":
    if not pr_file.is_file():
        sys.stderr.write("no pull requests found\\n")
        sys.exit(1)
    data = json.loads(pr_file.read_text(encoding="utf-8"))
    print(json.dumps(data))
    sys.exit(0)
if len(args) >= 2 and args[0] == "pr" and args[1] == "merge":
    assert "--squash" in args and "--match-head-commit" in args, f"Unexpected gh pr merge args: {{args}}"
    exp_sha = args[args.index("--match-head-commit") + 1]
    data = json.loads(pr_file.read_text(encoding="utf-8"))
    assert data["headRefOid"] == exp_sha, f"SHA mismatch: {{data['headRefOid']}} vs {{exp_sha}}"
    subprocess.run(["/usr/bin/git", "--git-dir", str(bare), "update-ref", "refs/heads/main", exp_sha], check=True)
    data["state"] = "MERGED"
    data["mergeCommit"] = {{"oid": exp_sha}}
    pr_file.write_text(json.dumps(data), encoding="utf-8")
    with slog.open("a", encoding="utf-8") as fh:
        fh.write("gh_pr_merge\\n")
    sys.exit(0)
sys.stderr.write(f"unhandled mock gh args: {{args}}\\n")
sys.exit(1)
""",
        encoding="utf-8",
    )
    mock_gh.chmod(0o755)

    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{mock_gh_dir}:{old_path}"
    try:
        # `paf drive-consensus` missing PR -> rc == 1; caller family spoofing -> rc == 2
        pr_state_file.unlink(missing_ok=True)
        r_drv_missing = run_paf(
            ["drive-consensus", "--repo", str(live_repo), "--author-family", "gemini"],
            cwd=live_repo,
            extra_env={"PATH": os.environ["PATH"], "PAF_CONSENSUS_KEY_FILE": str(live_key_file)},
        )
        r_drv_spoof = run_paf(
            ["drive-consensus", "--repo", str(live_repo), "--pr", "200", "--author-family", "gemini"],
            cwd=live_repo,
            extra_env={
                "PATH": os.environ["PATH"],
                "CLAUDE_SESSION_ID": "11111111-1111-1111-1111-111111111111",
            },
        )

        # `merge_pr_in_process` blocks on REVIEW_REQUIRED
        _write_pr_state(rev_decision="REVIEW_REQUIRED")
        rc_rr, tag_rr = paf_mod.merge_pr_in_process(
            repo=live_repo,
            pr_number=200,
            key_file=live_key_file,
            require_hmac=True,
        )

        # `merge_pr_in_process` returns (4, "paused_tiebreak") and `paf drive-consensus` returns rc == 4 on paused_human_tiebreak
        nh_m = dict(rev_m_200, verdict="request_changes", blocking_count=1, needs_human_decision=True)
        nh_m.pop("sig", None)
        nh_m.pop("key_id", None)
        pw_test.sign_marker_in_place(nh_m, live_secret, key_file=live_key_file, require_hmac=True)
        _write_pr_state(
            rev_decision="",
            comments_list=[{"body": f"Needs decision.\n\n<!-- paf-consensus: {json.dumps(nh_m, separators=(',', ':'))} -->"}],
        )
        rc_tb, tag_tb = paf_mod.merge_pr_in_process(
            repo=live_repo,
            pr_number=200,
            key_file=live_key_file,
            require_hmac=True,
        )
        r_drv_tiebreak = run_paf(
            [
                "drive-consensus",
                "--repo",
                str(live_repo),
                "--pr",
                "200",
                "--author-family",
                "gemini",
                "--state-dir",
                str(live_root / "st_tb"),
                "--key-file",
                str(live_key_file),
            ],
            cwd=live_repo,
            extra_env={"PATH": os.environ["PATH"]},
        )

        # Guarded `main` pauses cleanly via both `GitHubBus.merge_pr` and `paf drive-consensus` (`rc == 0`)
        _write_live_cfg("guarded")
        _write_pr_state(rev_decision="")
        live_bus = pw_test.GitHubBus(live_repo, PAF_BIN, key_file=live_key_file)
        ok_g_pause, reason_g_pause = live_bus.merge_pr(
            200,
            require_hmac=True,
            key_file=live_key_file,
            on_merged_callback=lambda: None,
        )
        r_drv_paused_main = run_paf(
            [
                "drive-consensus",
                "--repo",
                str(live_repo),
                "--pr",
                "200",
                "--author-family",
                "gemini",
                "--state-dir",
                str(live_root / "st_pm"),
                "--key-file",
                str(live_key_file),
                "--json",
            ],
            cwd=live_repo,
            extra_env={"PATH": os.environ["PATH"]},
        )
        drv_pm_json = json.loads(r_drv_paused_main.stdout) if r_drv_paused_main.stdout.strip().startswith("{") else {}

        # Autonomous `main` merges in-process via `GitHubBus.merge_pr`, fires `on_merged_callback` BEFORE post-merge/deploy!
        _write_live_cfg("autonomous")
        subprocess.run(["git", "add", ".agent-verify.json"], cwd=live_repo, check=True)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], cwd=live_repo, check=True, capture_output=True)
        subprocess.run(["git", "push", "-f", "origin", "feature/in-proc-merge"], cwd=live_repo, check=True, capture_output=True)
        feat_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=live_repo, text=True, capture_output=True, check=True
        ).stdout.strip()
        auth_m_200["head_sha"] = feat_sha
        auth_m_200.pop("sig", None)
        auth_m_200.pop("key_id", None)
        pw_test.sign_marker_in_place(auth_m_200, live_secret, key_file=live_key_file, require_hmac=True)
        rev_m_200["head_sha"] = feat_sha
        rev_m_200.pop("sig", None)
        rev_m_200.pop("key_id", None)
        pw_test.sign_marker_in_place(rev_m_200, live_secret, key_file=live_key_file, require_hmac=True)
        _write_pr_state(rev_decision="")
        live_log.unlink(missing_ok=True)

        def _live_close_cb() -> None:
            with live_log.open("a", encoding="utf-8") as fh:
                fh.write("sessions_closed\n")

        ok_live_merge, reason_live_merge = live_bus.merge_pr(
            200,
            require_hmac=True,
            key_file=live_key_file,
            host_id=pw_test.resolve_host_id(None),
            on_merged_callback=_live_close_cb,
        )
        live_seq = live_log.read_text(encoding="utf-8").splitlines() if live_log.is_file() else []
    finally:
        os.environ["PATH"] = old_path

    if (
        r_spoof_ship.returncode != 2
        or "family_mismatch" not in r_spoof_ship.stderr
        or r_spoof_post.returncode != 2
        or "family_mismatch" not in r_spoof_post.stderr
        or r_infer_ship.returncode != 0
        or "DRY_RUN_DRIVE_CONSENSUS_READY" not in r_infer_ship.stdout
        or "author=gemini" not in r_infer_ship.stdout
        or r_guarded_rev_req.returncode != 2
        or r_changes_req.returncode != 2
        or r_drv_missing.returncode != 1
        or r_drv_spoof.returncode != 2
        or rc_rr != 2
        or not tag_rr.startswith("human_review_required")
        or rc_tb != 4
        or tag_tb != "paused_tiebreak"
        or r_drv_tiebreak.returncode != 4
        or ok_g_pause is not False
        or reason_g_pause != "paused"
        or r_drv_paused_main.returncode != 0
        or drv_pm_json.get("status") != "paused_main_approval"
        or ok_live_merge is not True
        or reason_live_merge != "merged"
        or live_seq != ["gh_pr_merge", "sessions_closed", "post_merge", "deploy", "post_deploy"]
    ):
        print(
            f"FAIL 24 production merge_pr_in_process / CLI gates:\n"
            f"  spoof_ship=({r_spoof_ship.returncode},{r_spoof_ship.stderr.strip()!r}) "
            f"spoof_post=({r_spoof_post.returncode},{r_spoof_post.stderr.strip()!r})\n"
            f"  infer_ship=({r_infer_ship.returncode},{r_infer_ship.stdout.strip()!r})\n"
            f"  rev_req_rc={r_guarded_rev_req.returncode} chg_req_rc={r_changes_req.returncode}\n"
            f"  drv_missing={r_drv_missing.returncode} drv_spoof={r_drv_spoof.returncode} "
            f"rc_rr=({rc_rr},{tag_rr}) rc_tb=({rc_tb},{tag_tb}) drv_tb={r_drv_tiebreak.returncode}\n"
            f"  g_pause=({ok_g_pause},{reason_g_pause}) drv_pm=({r_drv_paused_main.returncode},{drv_pm_json})\n"
            f"  live_merge=({ok_live_merge},{reason_live_merge}) seq={live_seq}"
        )
        failures += 1
    else:
        print("PASS 24 verifies merge_pr_in_process, GitHubBus.merge_pr callback order, caller anti-spoofing, and drive-consensus CLI")

    # 25. Governance gate on the `paf ship` path (Leo, 2026-11-16): consensus alone merges a governance PR; a
    #     single-model blind receipt, an author-only marker, or a PR that strips `governance_requires_human` still pauses
    gs_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=gs_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=gs_repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=gs_repo, check=True)
    (gs_repo / ".agent-verify.json").write_text(
        json.dumps({"zone": "autonomous", "default_branch": "main", "allow_lint_only": True, "commands": {}}),
        encoding="utf-8",
    )
    (gs_repo / "CORE.md").write_text("rule v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=gs_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=gs_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature/rule"], cwd=gs_repo, check=True, capture_output=True)
    (gs_repo / "CORE.md").write_text("rule v2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "rule v2"], cwd=gs_repo, check=True, capture_output=True)
    gs_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=gs_repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    gs_fix = gs_repo / ".git" / "pr-gs.json"

    def gs_fixture(head: str, body: str, comments: list[dict[str, str]], files: list[str]) -> None:
        gs_fix.write_text(
            json.dumps(
                {
                    "number": 201,
                    "url": "https://example.test/pr/201",
                    "state": "OPEN",
                    "headRefName": "feature/rule",
                    "baseRefName": "main",
                    "headRefOid": head,
                    "body": body,
                    "comments": comments,
                    "files": [{"path": f} for f in files],
                    "changedFiles": len(files),
                    "reviewDecision": "",
                }
            ),
            encoding="utf-8",
        )

    def gs_ship() -> subprocess.CompletedProcess[str]:
        return run_paf(
            ["ship", "--repo", str(gs_repo), "--base", "main", "--pr-fixture", str(gs_fix), "--dry-run"], cwd=gs_repo
        )

    gs_author = f'<!-- paf-consensus: {{"role":"author","model_family":"claude","verdict":"open","head_sha":"{gs_sha}"}} -->'
    gs_approve = (
        f'<!-- paf-consensus: {{"role":"reviewer","model_family":"gemini","verdict":"approve","head_sha":"{gs_sha}","round":1}} -->'
    )
    # (a) verified dual-model consensus on head: no governance pause
    gs_fixture(gs_sha, gs_author, [{"body": gs_approve}], ["CORE.md"])
    r_gs_cons = gs_ship()
    # (b) author-only marker: not consensus, must not slip past the gate
    gs_fixture(gs_sha, gs_author, [], ["CORE.md"])
    r_gs_author_only = gs_ship()
    # (c) single-model blind receipt only, no PR markers: pause
    run_paf(
        ["record-review", "--repo", str(gs_repo), "--verdict", "approve", "--reviewer", "blind", "--summary", "ok"],
        cwd=gs_repo,
    )
    gs_fixture(gs_sha, "plain body", [], ["CORE.md"])
    r_gs_blind = gs_ship()
    # (d) base sets governance_requires_human; the PR strips it in its own tree: still pause despite consensus
    subprocess.run(["git", "checkout", "main"], cwd=gs_repo, check=True, capture_output=True)
    (gs_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "zone": "autonomous",
                "default_branch": "main",
                "allow_lint_only": True,
                "governance_requires_human": True,
                "commands": {},
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "commit", "-am", "require human"], cwd=gs_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature/strip"], cwd=gs_repo, check=True, capture_output=True)
    (gs_repo / ".agent-verify.json").write_text(
        json.dumps({"zone": "autonomous", "default_branch": "main", "allow_lint_only": True, "commands": {}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "commit", "-am", "strip"], cwd=gs_repo, check=True, capture_output=True)
    strip_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=gs_repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    gs_fixture(
        strip_sha,
        gs_author.replace(gs_sha, strip_sha),
        [{"body": gs_approve.replace(gs_sha, strip_sha)}],
        [".agent-verify.json"],
    )
    r_gs_strip = gs_ship()
    gs_ok = (
        "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_gs_cons.stdout
        and "MERGE_READY" in r_gs_cons.stdout
        and "MERGE_READY" not in r_gs_author_only.stdout
        and "PAUSED_FOR_GOVERNANCE_APPROVAL" in r_gs_blind.stdout
        and "PAUSED_FOR_GOVERNANCE_APPROVAL" in r_gs_strip.stdout
    )
    if not gs_ok:
        print(
            "FAIL ship-path governance gate: "
            f"cons={r_gs_cons.stdout.strip()!r}/{r_gs_cons.stderr.strip()[-200:]!r} "
            f"author_only={r_gs_author_only.stdout.strip()!r}/{r_gs_author_only.stderr.strip()[-200:]!r} "
            f"blind={r_gs_blind.stdout.strip()!r}/{r_gs_blind.stderr.strip()[-200:]!r} "
            f"strip={r_gs_strip.stdout.strip()!r}/{r_gs_strip.stderr.strip()[-200:]!r}"
        )
        failures += 1
    else:
        print("PASS ship-path governance gate: consensus merges; author-only, blind-only and self-stripped flag pause")

    # 26. `paf verify` lints tracked files only: an untracked file with CJK text, left in the tree by another
    #     session, does not fail the gate; the same file once tracked does.
    repo_t = init_temp_git_repo()
    subprocess.run(["git", "add", "-A"], cwd=repo_t, check=True, capture_output=True)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=repo_t, check=True, capture_output=True)
    (repo_t / "scratch.py").write_text("x = 1  # \u6e2c\u8a66\n", encoding="utf-8")
    r_untracked = run_paf(["verify", "--repo", str(repo_t), "--mode", "pre-push"], cwd=repo_t)
    subprocess.run(["git", "add", "scratch.py"], cwd=repo_t, check=True, capture_output=True)
    r_tracked = run_paf(["verify", "--repo", str(repo_t), "--mode", "pre-push"], cwd=repo_t)
    if (
        "[PASS] dune_lint" not in r_untracked.stdout + r_untracked.stderr
        or "[FAIL] dune_lint" not in r_tracked.stdout + r_tracked.stderr
        or r_tracked.returncode == 0
    ):
        print(
            "FAIL verify tracked-only lint: "
            f"untracked rc={r_untracked.returncode} {(r_untracked.stdout + r_untracked.stderr)[-200:]!r} "
            f"tracked rc={r_tracked.returncode} {(r_tracked.stdout + r_tracked.stderr)[-200:]!r}"
        )
        failures += 1
    else:
        print("PASS verify lints tracked files only (untracked CJK file ignored, tracked one caught)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
