#!/usr/bin/env python3
"""Behavior-first tests for paf HMAC consensus signing, host affinity, claim locks,
same-family fresh-session fallback, ETag/state-driven watch-prs, and launchd plist generation."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PAF_BIN = REPO_ROOT / "bin" / "paf"


def run_paf(
    args: list[str],
    cwd: pathlib.Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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


def init_git_repo(
    zone: str = "autonomous",
    consensus_cfg: dict[str, object] | None = None,
) -> tuple[pathlib.Path, str]:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="paf-watch-test-"))
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    cfg: dict[str, object] = {
        "version": 1,
        "zone": zone,
        "default_branch": "main",
        "commands": {"unit_test": ["true"], "post_merge_check": ["true"]},
    }
    if consensus_cfg is not None:
        cfg["consensus"] = consensus_cfg
    (tmp / ".agent-verify.json").write_text(json.dumps(cfg), encoding="utf-8")
    (tmp / "app.py").write_text("def hello() -> str:\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(["git", "add", ".agent-verify.json", "app.py"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature/work"], cwd=tmp, check=True, capture_output=True)
    (tmp / "app.py").write_text("def hello() -> str:\n    return 'v2'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-m", "feat: v2"], cwd=tmp, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp, text=True, capture_output=True, check=True
    ).stdout.strip()
    return tmp, sha


def make_pr_json(
    head_sha: str,
    body: str,
    comments: list[dict[str, object]],
    *,
    number: int = 42,
    files: list[str] | None = None,
) -> dict[str, object]:
    file_list = ["app.py"] if files is None else files
    return {
        "number": number,
        "url": f"https://example.test/pr/{number}",
        "state": "OPEN",
        "headRefName": "feature/work",
        "baseRefName": "main",
        "headRefOid": head_sha,
        "body": body,
        "comments": comments,
        "files": [{"path": p} for p in file_list],
        "changedFiles": len(file_list),
        "reviewDecision": "",
    }


def main() -> int:
    failures = 0

    # 1. `init-consensus-key` + HMAC-SHA256 signing & fail-closed verification
    key_dir = pathlib.Path(tempfile.mkdtemp(prefix="paf-key-"))
    key_file = key_dir / "consensus.key"
    repo1, sha1 = init_git_repo(consensus_cfg={"require_hmac": True})
    r_key1 = run_paf(["init-consensus-key", "--key-file", str(key_file)], cwd=repo1)
    first_secret = key_file.read_text(encoding="utf-8").strip() if key_file.is_file() else ""
    mode_bits = stat.S_IMODE(key_file.stat().st_mode) if key_file.is_file() else 0
    r_key2 = run_paf(["init-consensus-key", "--key-file", str(key_file)], cwd=repo1)
    second_secret = key_file.read_text(encoding="utf-8").strip() if key_file.is_file() else ""

    fix1 = repo1 / ".git" / "pr1.json"
    fix1.write_text(json.dumps(make_pr_json(sha1, "", [])), encoding="utf-8")
    env_key = {"PAF_CONSENSUS_KEY_FILE": str(key_file), "PAF_HOST_ID": "work-mac"}

    r_author_post = run_paf(
        [
            "post-consensus",
            "--repo",
            str(repo1),
            "--pr",
            "42",
            "--pr-fixture",
            str(fix1),
            "--role",
            "author",
            "--family",
            "claude",
            "--verdict",
            "open",
            "--head-sha",
            sha1,
            "--session-id",
            "sess-claude-1",
        ],
        cwd=repo1,
        extra_env=env_key,
    )
    author_comment = r_author_post.stdout.strip()

    r_rev_post = run_paf(
        [
            "post-consensus",
            "--repo",
            str(repo1),
            "--pr",
            "42",
            "--pr-fixture",
            str(fix1),
            "--role",
            "reviewer",
            "--family",
            "gemini",
            "--verdict",
            "approve",
            "--head-sha",
            sha1,
            "--round",
            "1",
            "--session-id",
            "sess-gemini-1",
        ],
        cwd=repo1,
        extra_env={"PAF_CONSENSUS_KEY_FILE": str(key_file), "PAF_HOST_ID": "personal-mac"},
    )
    signed_approve = r_rev_post.stdout.strip()
    unsigned_approve = (
        f'<!-- paf-consensus: {{"v":1,"role":"reviewer","model_family":"gemini","verdict":"approve","head_sha":"{sha1}","round":1}} -->'
    )
    tampered_approve = signed_approve.replace('"gemini"', '"openai"')
    unsigned_objection = (
        f'<!-- paf-consensus: {{"v":1,"role":"reviewer","model_family":"gemini","verdict":"request_changes","head_sha":"{sha1}","round":2,"blocking_count":1}} -->'
    )

    # Signed author + unsigned approve under require_hmac=true -> MUST fail closed
    fix1.write_text(json.dumps(make_pr_json(sha1, author_comment, [{"body": unsigned_approve}])), encoding="utf-8")
    r_hmac_missing = run_paf(
        ["check-consensus", "--repo", str(repo1), "--pr-fixture", str(fix1)],
        cwd=repo1,
        extra_env=env_key,
    )

    # Signed author + tampered sig -> MUST fail closed EVEN on a repo with require_hmac=false
    repo1_opt, _ = init_git_repo(consensus_cfg={"require_hmac": False})
    fix1_opt = repo1_opt / ".git" / "pr1_opt.json"
    fix1_opt.write_text(
        json.dumps(make_pr_json(sha1, author_comment, [{"body": tampered_approve}])),
        encoding="utf-8",
    )
    r_hmac_tampered = run_paf(
        ["check-consensus", "--repo", str(repo1_opt), "--pr-fixture", str(fix1_opt)],
        cwd=repo1_opt,
        extra_env=env_key,
    )

    # Valid signed author + valid signed heterogeneous approve -> MUST pass
    fix1.write_text(json.dumps(make_pr_json(sha1, author_comment, [{"body": signed_approve}])), encoding="utf-8")
    r_hmac_ok = run_paf(
        ["check-consensus", "--repo", str(repo1), "--pr-fixture", str(fix1)],
        cwd=repo1,
        extra_env=env_key,
    )

    # Valid signed approve followed by unsigned objection -> objection MUST still block (D2 preserved under HMAC)
    fix1.write_text(
        json.dumps(make_pr_json(sha1, author_comment, [{"body": signed_approve}, {"body": unsigned_objection}])),
        encoding="utf-8",
    )
    r_hmac_obj = run_paf(
        ["check-consensus", "--repo", str(repo1), "--pr-fixture", str(fix1)],
        cwd=repo1,
        extra_env=env_key,
    )

    if (
        r_key1.returncode != 0
        or len(first_secret) != 64
        or mode_bits != 0o600
        or r_key2.returncode != 0
        or second_secret != first_secret
        or '"sig":' not in signed_approve
        or '"host":"work-mac"' not in author_comment
        or r_hmac_missing.returncode == 0
        or r_hmac_tampered.returncode == 0
        or r_hmac_ok.returncode != 0
        or r_hmac_obj.returncode != 2
    ):
        print(
            f"FAIL 1 HMAC signing/verification: key_len={len(first_secret)} mode={oct(mode_bits)} "
            f"missing_rc={r_hmac_missing.returncode} tampered_rc={r_hmac_tampered.returncode} "
            f"ok_rc={r_hmac_ok.returncode} obj_rc={r_hmac_obj.returncode}\n"
            f"ok_out={r_hmac_ok.stdout} {r_hmac_ok.stderr}"
        )
        failures += 1
    else:
        print("PASS 1 init-consensus-key (0600) + HMAC-SHA256 prevents forged/unsigned approve while keeping objections live")

    # 2. `session_id` anti-self-review + Same-family fresh-session fallback
    repo_fb, sha_fb = init_git_repo(consensus_cfg={"allow_same_family_fallback": True})
    fix_fb = repo_fb / ".git" / "pr_fb.json"
    author_s1 = json.dumps(
        {
            "v": 1,
            "role": "author",
            "model_family": "claude",
            "verdict": "open",
            "head_sha": sha_fb,
            "session_id": "sess-author-A",
        }
    )
    peer_claude_obj = json.dumps(
        {
            "v": 1,
            "role": "reviewer",
            "model_family": "claude",
            "verdict": "request_changes",
            "head_sha": sha_fb,
            "round": 1,
            "blocking_count": 1,
            "session_id": "sess-peer-B",
        }
    )
    self_session_approve = json.dumps(
        {
            "v": 1,
            "role": "reviewer",
            "model_family": "claude",
            "verdict": "approve",
            "head_sha": sha_fb,
            "round": 2,
            "blocking_count": 0,
            "session_id": "sess-author-A",
            "fallback_same_family": True,
        }
    )
    fresh_session_approve = json.dumps(
        {
            "v": 1,
            "role": "reviewer",
            "model_family": "claude",
            "verdict": "approve",
            "head_sha": sha_fb,
            "round": 2,
            "blocking_count": 0,
            "session_id": "sess-fresh-C",
            "fallback_same_family": True,
        }
    )
    gemini_approve_fb = json.dumps(
        {
            "v": 1,
            "role": "reviewer",
            "model_family": "gemini",
            "verdict": "approve",
            "head_sha": sha_fb,
            "round": 1,
            "blocking_count": 0,
        }
    )

    # Author's own session_id trying to clear peer Claude's objection (even with Gemini approve present!) -> MUST stay blocked!
    fix_fb.write_text(
        json.dumps(
            make_pr_json(
                sha_fb,
                f"<!-- paf-consensus: {author_s1} -->",
                [
                    {"body": f"<!-- paf-consensus: {gemini_approve_fb} -->"},
                    {"body": f"<!-- paf-consensus: {peer_claude_obj} -->"},
                    {"body": f"<!-- paf-consensus: {self_session_approve} -->"},
                ],
            )
        ),
        encoding="utf-8",
    )
    r_self_clear = run_paf(["check-consensus", "--repo", str(repo_fb), "--pr-fixture", str(fix_fb)], cwd=repo_fb)

    # Same-family fallback with SAME session_id -> MUST fail closed
    fix_fb.write_text(
        json.dumps(
            make_pr_json(
                sha_fb,
                f"<!-- paf-consensus: {author_s1} -->",
                [{"body": f"<!-- paf-consensus: {self_session_approve} -->"}],
            )
        ),
        encoding="utf-8",
    )
    r_fb_same_sess = run_paf(["check-consensus", "--repo", str(repo_fb), "--pr-fixture", str(fix_fb)], cwd=repo_fb)

    # Same-family fallback with DISTINCT fresh session_id on repo where allow_same_family_fallback=true -> PASSES
    fix_fb.write_text(
        json.dumps(
            make_pr_json(
                sha_fb,
                f"<!-- paf-consensus: {author_s1} -->",
                [{"body": f"<!-- paf-consensus: {fresh_session_approve} -->"}],
            )
        ),
        encoding="utf-8",
    )
    r_fb_fresh_ok = run_paf(["check-consensus", "--repo", str(repo_fb), "--pr-fixture", str(fix_fb)], cwd=repo_fb)

    # Same marker on default repo (allow_same_family_fallback=false) -> FAILS CLOSED
    r_fb_disabled = run_paf(["check-consensus", "--repo", str(repo1_opt), "--pr-fixture", str(fix_fb)], cwd=repo1_opt)

    # Distinct peer session (`sess-fresh-C` != `sess-author-A`) CAN clear peer Claude's objection so Gemini's approve wins,
    # whereas an unlabeled/sessionless Claude approve CANNOT clear peer Claude's objection!
    sessionless_claude_approve = json.dumps(
        {
            "v": 1,
            "role": "reviewer",
            "model_family": "claude",
            "verdict": "approve",
            "head_sha": sha_fb,
            "round": 2,
            "blocking_count": 0,
        }
    )
    fix_fb.write_text(
        json.dumps(
            make_pr_json(
                sha_fb,
                f"<!-- paf-consensus: {author_s1} -->",
                [
                    {"body": f"<!-- paf-consensus: {gemini_approve_fb} -->"},
                    {"body": f"<!-- paf-consensus: {peer_claude_obj} -->"},
                    {"body": f"<!-- paf-consensus: {sessionless_claude_approve} -->"},
                ],
            )
        ),
        encoding="utf-8",
    )
    r_sessionless_clear = run_paf(["check-consensus", "--repo", str(repo_fb), "--pr-fixture", str(fix_fb)], cwd=repo_fb)

    fix_fb.write_text(
        json.dumps(
            make_pr_json(
                sha_fb,
                f"<!-- paf-consensus: {author_s1} -->",
                [
                    {"body": f"<!-- paf-consensus: {gemini_approve_fb} -->"},
                    {"body": f"<!-- paf-consensus: {peer_claude_obj} -->"},
                    {"body": f"<!-- paf-consensus: {fresh_session_approve} -->"},
                ],
            )
        ),
        encoding="utf-8",
    )
    r_peer_clear_ok = run_paf(["check-consensus", "--repo", str(repo_fb), "--pr-fixture", str(fix_fb)], cwd=repo_fb)

    if (
        r_self_clear.returncode == 0
        or r_sessionless_clear.returncode == 0
        or r_peer_clear_ok.returncode != 0
        or r_fb_same_sess.returncode == 0
        or r_fb_fresh_ok.returncode != 0
        or r_fb_disabled.returncode == 0
    ):
        print(
            f"FAIL 2 session_id / fallback: self_clear={r_self_clear.returncode} "
            f"same_sess={r_fb_same_sess.returncode} fresh_ok={r_fb_fresh_ok.returncode} "
            f"disabled={r_fb_disabled.returncode}"
        )
        failures += 1
    else:
        print("PASS 2 enforces distinct session_id on same-family fallback and blocks authoring-session self-clearance")

    # 3, 4, 5, 6. `paf_watcher` unit & integration tests (ETag 304, claim locks, author_host affinity, headless CLI wrapper)
    try:
        sys.path.insert(0, str(REPO_ROOT / "framework" / "scripts"))
        import paf_watcher  # type: ignore
    except Exception as exc:
        print(f"FAIL 3-6 import paf_watcher: {exc}")
        return 1

    # 3 & 4: Claim lock resolution (active claim by other host blocks; expired claim yields; earliest claim wins tie-break)
    now_utc = dt.datetime(2026, 11, 16, 8, 0, 0, tzinfo=dt.timezone.utc)
    future_iso = (now_utc + dt.timedelta(minutes=10)).isoformat()
    past_iso = (now_utc - dt.timedelta(minutes=2)).isoformat()
    c_other_active = {
        "body": "Claiming review\n\n" + paf_watcher.format_claim_marker(
            head_sha=sha1, model_family="gemini", host="work-mac", expires_at=future_iso
        )
    }
    c_other_expired = {
        "body": paf_watcher.format_claim_marker(
            head_sha=sha1, model_family="gemini", host="work-mac", expires_at=past_iso
        )
    }
    c_mine_active = {
        "body": paf_watcher.format_claim_marker(
            head_sha=sha1, model_family="gemini", host="personal-mac", expires_at=future_iso
        )
    }
    holder_active = paf_watcher.active_claim_holder([c_other_active], sha1, "gemini", now=now_utc)
    holder_expired = paf_watcher.active_claim_holder([c_other_expired], sha1, "gemini", now=now_utc)
    holder_tie = paf_watcher.active_claim_holder([c_other_active, c_mine_active], sha1, "gemini", now=now_utc)
    holder_after_exp = paf_watcher.active_claim_holder([c_other_expired, c_mine_active], sha1, "gemini", now=now_utc)
    if (
        holder_active != "work-mac"
        or holder_expired is not None
        or holder_tie != "work-mac"
        or holder_after_exp != "personal-mac"
    ):
        print(
            f"FAIL 3 claim lock resolution: active={holder_active} exp={holder_expired} "
            f"tie={holder_tie} after_exp={holder_after_exp}"
        )
        failures += 1
    else:
        print("PASS 3 paf-claim lock prevents cross-Mac duplicate reviews and recovers cleanly after TTL expiry")

    # 4 & 5 & 6: End-to-end `watch-prs --once` with mocked GitHub bus + mocked `claude` and `agy` binaries:
    #   - ETag 304 idle tick makes 0 PR detail calls and 0 LLM calls
    #   - `personal-mac` skips `request_changes` fix & `merge-consensus` on a `work-mac`-authored PR (no git push fight!)
    #   - `personal-mac` claims & runs `agy` (`gemini`) read-only sandbox review on a `work-mac` `claude` PR,
    #     stamps HMAC signature outside the model subprocess (with PAF_CONSENSUS_KEY_FILE scrubbed from model env!),
    #     and does NOT re-review the same SHA on the next tick
    #   - `work-mac` sees the valid HMAC-signed `gemini` approve + green CI and triggers `merge-consensus`
    mock_bin = pathlib.Path(tempfile.mkdtemp(prefix="paf-mock-cli-"))
    env_dump = mock_bin / "agy_env.json"
    args_dump = mock_bin / "agy_args.json"
    fake_agy = mock_bin / "agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(env_dump)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
        f"open({str(args_dump)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('[agy] booting sandbox...')\n"
        "resp = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'prose': 'All invariants hold.'}\n"
        "print(json.dumps({'conversation_id': 'agy-conv-777', 'status': 'DONE', 'response': json.dumps(resp)}))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)

    claude_env_dump = mock_bin / "claude_env.json"
    claude_args_dump = mock_bin / "claude_args.json"
    fake_claude = mock_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(claude_env_dump)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
        f"open({str(claude_args_dump)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "resp = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'prose': 'Claude fallback review ok.'}\n"
        "print(json.dumps({'session_id': 'claude-sess-888', 'is_error': False, 'structured_output': resp}))\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    state_dir = pathlib.Path(tempfile.mkdtemp(prefix="paf-watch-state-"))
    bus = paf_watcher.InMemoryGitHubBus(
        repo_path=repo1,
        prs={
            42: make_pr_json(
                sha1,
                author_comment,  # claude author on host="work-mac", signed with key_file
                [],
            )
        },
        etag="W/\"etag-v1\"",
    )

    # Tick A: personal-mac reviews PR #42 as `gemini` via `fake_agy`, stamps HMAC, posts review, does NOT merge (author_host=work-mac)
    act_a = paf_watcher.watch_repo_once(
        repo=repo1,
        repo_slug="leolin84200704/demo",
        host_id="personal-mac",
        families=("claude", "gemini"),
        state_dir=state_dir,
        key_file=key_file,
        bus=bus,
        claude_bin=str(fake_claude),
        agy_bin=str(fake_agy),
        claim_settle_sec=0.0,
    )
    agy_seen_env = json.loads(env_dump.read_text(encoding="utf-8")) if env_dump.is_file() else {}
    agy_seen_args = json.loads(args_dump.read_text(encoding="utf-8")) if args_dump.is_file() else []

    # Tick B: ETag unchanged -> 304 Not Modified, 0 detail calls, 0 actions
    detail_before = bus.detail_calls
    act_b_304 = paf_watcher.watch_repo_once(
        repo=repo1,
        repo_slug="leolin84200704/demo",
        host_id="personal-mac",
        families=("claude", "gemini"),
        state_dir=state_dir,
        key_file=key_file,
        bus=bus,
        claude_bin=str(fake_claude),
        agy_bin=str(fake_agy),
        claim_settle_sec=0.0,
    )
    detail_after = bus.detail_calls

    # Tick C: Even when ETag bumps on PR #42 (because our review comment was added), personal-mac neither re-reviews nor merges `work-mac`'s PR!
    act_c_host_guard = paf_watcher.watch_repo_once(
        repo=repo1,
        repo_slug="leolin84200704/demo",
        host_id="personal-mac",
        families=("claude", "gemini"),
        state_dir=pathlib.Path(tempfile.mkdtemp(prefix="paf-watch-state2-")),
        key_file=key_file,
        bus=bus,
        claude_bin=str(fake_claude),
        agy_bin=str(fake_agy),
        claim_settle_sec=0.0,
    )

    # Tick D: `work-mac` (`author_host`) polls PR #42, verifies HMAC-signed `gemini` approve, and executes merge!
    act_d_merge = paf_watcher.watch_repo_once(
        repo=repo1,
        repo_slug="leolin84200704/demo",
        host_id="work-mac",
        families=("claude", "gemini"),
        state_dir=pathlib.Path(tempfile.mkdtemp(prefix="paf-watch-state3-")),
        key_file=key_file,
        bus=bus,
        claude_bin=str(fake_claude),
        agy_bin=str(fake_agy),
        claim_settle_sec=0.0,
    )

    if (
        act_a.get("reviewed") != [42]
        or act_a.get("merged") != []
        or "PAF_CONSENSUS_KEY_FILE" in agy_seen_env
        or "CLAUDECODE" in agy_seen_env
        or "--sandbox" not in agy_seen_args
        or not any(a.startswith("-p=") for a in agy_seen_args)
        or act_b_304.get("not_modified") is not True
        or detail_after != detail_before
        or act_c_host_guard.get("reviewed") != []
        or act_c_host_guard.get("merged") != []
        or act_d_merge.get("merged") != [42]
        or bus.prs[42]["state"] != "MERGED"
    ):
        print(
            f"FAIL 4-6 watch_repo_once flow:\n"
            f"  act_a={act_a}\n  agy_args={agy_seen_args}\n  key_in_env={'PAF_CONSENSUS_KEY_FILE' in agy_seen_env}\n"
            f"  act_b_304={act_b_304} detail={detail_before}->{detail_after}\n"
            f"  act_c={act_c_host_guard}\n  act_d={act_d_merge} state={bus.prs[42]['state']}"
        )
        failures += 1
    else:
        print("PASS 4-6 watch-prs runs sandboxed agy/claude, scrubs HMAC env, honors ETag 304 + author_host, and auto-merges on author host")

    # 7. Outage fallback: when `agy` fails (quota/outage) and `allow_same_family_fallback=true`, `watch-prs` falls back to
    #    a fresh blank `claude` session (`claude-sess-888` != `sess-claude-1`) with `fallback_same_family=true`
    broken_agy = mock_bin / "agy_broken"
    broken_agy.write_text("#!/bin/sh\necho 'quota exhausted 429' >&2\nexit 1\n", encoding="utf-8")
    broken_agy.chmod(0o755)
    repo_fb_live, sha_fb_live = init_git_repo(consensus_cfg={"allow_same_family_fallback": True, "require_hmac": True})
    fix_fb_live = repo_fb_live / ".git" / "pr_fb_live.json"
    fix_fb_live.write_text(json.dumps(make_pr_json(sha_fb_live, "", [], number=77)), encoding="utf-8")
    author_fb_comment = run_paf(
        [
            "post-consensus",
            "--repo",
            str(repo_fb_live),
            "--pr",
            "77",
            "--pr-fixture",
            str(fix_fb_live),
            "--role",
            "author",
            "--family",
            "claude",
            "--verdict",
            "open",
            "--head-sha",
            sha_fb_live,
            "--session-id",
            "sess-claude-1",
        ],
        cwd=repo_fb_live,
        extra_env=env_key,
    ).stdout.strip()
    bus_fb = paf_watcher.InMemoryGitHubBus(
        repo_path=repo_fb_live,
        prs={77: make_pr_json(sha_fb_live, author_fb_comment, [], number=77)},
        etag="W/\"etag-fb\"",
    )
    act_fb = paf_watcher.watch_repo_once(
        repo=repo_fb_live,
        repo_slug="leolin84200704/demo",
        host_id="work-mac",
        families=("claude", "gemini"),
        state_dir=pathlib.Path(tempfile.mkdtemp(prefix="paf-watch-fb-")),
        key_file=key_file,
        bus=bus_fb,
        claude_bin=str(fake_claude),
        agy_bin=str(broken_agy),
        allow_same_family_fallback=True,
        claim_settle_sec=0.0,
    )
    claude_seen_args = json.loads(claude_args_dump.read_text(encoding="utf-8")) if claude_args_dump.is_file() else []
    if (
        act_fb.get("reviewed") != [77]
        or act_fb.get("merged") != [77]
        or "--resume" in claude_seen_args
        or "--strict-mcp-config" not in claude_seen_args
        or "Read,Grep,Glob" not in claude_seen_args
    ):
        print(f"FAIL 7 same-family outage fallback: act_fb={act_fb} claude_args={claude_seen_args}")
        failures += 1
    else:
        print("PASS 7 outage fallback runs fresh blank read-only same-family session, stamps HMAC, and merges")

    # 8. `install-watcher --dry-run` generates valid macOS launchd plist (`plutil -lint`)
    plist_path = pathlib.Path(tempfile.mkdtemp(prefix="paf-plist-")) / "com.leo.paf-watcher.plist"
    r_plist = run_paf(
        [
            "install-watcher",
            "--dry-run",
            "--plist-out",
            str(plist_path),
            "--repo-dir",
            str(repo1),
            "--interval",
            "45",
            "--host",
            "personal-mac",
        ],
        cwd=repo1,
    )
    r_plutil = subprocess.run(
        ["plutil", "-lint", str(plist_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if r_plist.returncode != 0 or not plist_path.is_file() or r_plutil.returncode != 0:
        print(
            f"FAIL 8 launchd plist generation: rc={r_plist.returncode} plutil={r_plutil.returncode} "
            f"{r_plist.stderr} {r_plutil.stdout} {r_plutil.stderr}"
        )
        failures += 1
    else:
        print("PASS 8 install-watcher generates valid macOS launchd plist verified by plutil -lint")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
