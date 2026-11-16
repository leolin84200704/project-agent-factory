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

    # 18. C3 & C4: Autonomous repo pauses once on governance/permission file edits (`CORE.md`, `.agent-verify.json`, etc.)
    #     until `--approved-governance`, and blocks when GitHub `reviewDecision` requires human review
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
    res_c3_pause = run_paf(
        ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
        cwd=auto_repo,
    )
    res_c3_approved = run_paf(
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
        or res_c3_approved.returncode != 0
        or "CONSENSUS_AUTONOMOUS_MERGE_READY" not in res_c3_approved.stdout
        or res_c4_human_req.returncode == 0
    ):
        print(
            f"FAIL C3/C4 governance & human-review gates: c3_pause={res_c3_pause.stdout.strip()} "
            f"c3_approved={res_c3_approved.stdout.strip()} c4_rc={res_c4_human_req.returncode}"
        )
        failures += 1
    else:
        print("PASS C3/C4 pause governance-file PRs for Leo and honor required human GitHub reviews")

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

    # 21. D3 + truncated files guard: `.claude/settings*.json`, `.gemini/settings.json`, `framework/WORK-LOOP.md`,
    #     `GEMINI.md`, `skills/verify/SKILL.md`, `framework/githooks/commit-msg`, and truncated `changedFiles > len(files)` all pause
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
        if r_gov.returncode != 0 or "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_gov.stdout:
            d3_ok = False
            print(f"FAIL D3 governance path missed: {gov_path} -> {r_gov.stdout.strip()}")

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
                "files": [{"path": "src/app.py"}],
                "changedFiles": 250,
                "reviewDecision": "",
            }
        ),
        encoding="utf-8",
    )
    r_trunc = run_paf(
        ["merge-consensus", "--repo", str(auto_repo), "--pr-fixture", str(auto_pr_fix), "--dry-run"],
        cwd=auto_repo,
    )
    if not d3_ok or r_trunc.returncode != 0 or "PAUSED_FOR_GOVERNANCE_APPROVAL" not in r_trunc.stdout:
        print(f"FAIL D3 / truncated changedFiles guard: d3_ok={d3_ok} trunc={r_trunc.stdout.strip()}")
        failures += 1
    else:
        print("PASS D3 expands governance-file coverage and pauses when PR file list is truncated")

    # 22. Truncated or missing PR file list: the local `git diff` fallback counts only when local HEAD is the PR head
    fb_repo = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-b", "main"], cwd=fb_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=fb_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=fb_repo, check=True)
    (fb_repo / ".agent-verify.json").write_text(
        json.dumps({"version": 1, "zone": "autonomous", "default_branch": "main"}), encoding="utf-8"
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
        author = '<!-- paf-consensus: ' + json.dumps(
            {"v": 1, "role": "author", "model_family": "gemini", "verdict": "open", "head_sha": head_oid}
        ) + ' -->'
        approve = '<!-- paf-consensus: ' + json.dumps(
            {
                "v": 1,
                "role": "reviewer",
                "model_family": "claude",
                "verdict": "approve",
                "head_sha": head_oid,
                "round": 1,
                "blocking_count": 0,
            }
        ) + ' -->'
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

    # local checkout sits on an unrelated branch with enough files to cover the count: must stay truncated
    write_fb_fixture(other_sha, ["src/app.py"], 3)
    r_fb_other = fb_dry_run()
    # no file list at all and the local checkout is not the PR head: must pause, not pass as "no governance files"
    write_fb_fixture(other_sha, None, None)
    r_fb_nofiles = fb_dry_run()
    # local HEAD is the PR head and holds no governance file: the fallback completes the list, no pause
    write_fb_fixture(plain_sha, ["a.py"], 3)
    r_fb_same_plain = fb_dry_run()
    # local HEAD is the PR head and the truncated-away part holds a governance file: pause and name it
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

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
