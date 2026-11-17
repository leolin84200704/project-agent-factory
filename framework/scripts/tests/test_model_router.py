#!/usr/bin/env python3
"""Behavioral unit tests for deterministic task/PR model routing (`model_router.py` & `paf select-model`)."""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

FACTORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
ROUTER_PATH = FACTORY_ROOT / "framework" / "scripts" / "model_router.py"
WATCHER_PATH = FACTORY_ROOT / "framework" / "scripts" / "paf_watcher.py"
PAF_BIN = FACTORY_ROOT / "bin" / "paf"
_TEMP_DIRS: list[pathlib.Path] = []
for _env_k in ("PAF_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDECODE", "AGY_CONVERSATION_ID", "PAF_AUTHOR_FAMILY"):
    os.environ.pop(_env_k, None)


def _mkdtemp(prefix: str) -> pathlib.Path:
    p = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_DIRS.append(p)
    return p


@atexit.register
def _cleanup() -> None:
    for p in reversed(_TEMP_DIRS):
        shutil.rmtree(p, ignore_errors=True)

VALID_AGY_MODELS = {
    "gemini-4-carbon-high",
    "gemini-4-carbon-medium",
    "gemini-4-carbon-low",
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
    "gemini-4-argon-low",
    "gemini-4-hydrogen-xhigh",
    "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-medium",
    "gemini-4-hydrogen-low",
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
}
VALID_CLAUDE_MODELS = {"haiku", "sonnet", "opus", "fable"}
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _load_mod(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures = 0
    mr = _load_mod("model_router", ROUTER_PATH)
    pw = _load_mod("paf_watcher", WATCHER_PATH)

    # 1. Catalog entries map strictly to verified live CLI identifiers
    bad_catalog = []
    for key, spec in mr.MODEL_CATALOG.items():
        fam = spec.get("family")
        cli_m = spec.get("cli_model")
        eff = spec.get("effort")
        if fam == "gemini" and cli_m not in VALID_AGY_MODELS:
            bad_catalog.append((key, cli_m))
        elif fam == "claude" and (cli_m not in VALID_CLAUDE_MODELS or eff not in VALID_EFFORTS):
            bad_catalog.append((key, cli_m, eff))
        elif fam not in ("gemini", "claude"):
            bad_catalog.append((key, fam))
    if bad_catalog:
        print(f"FAIL 1 invalid catalog entries: {bad_catalog}")
        failures += 1
    else:
        print("PASS 1 MODEL_CATALOG uses verified live agy and claude CLI model identifiers")

    # 2. Trivial tier: pure docs/markdown small change avoids slow/expensive Carbon/Fable/Opus
    p_triv = mr.select_task_models(
        title="docs: fix typo in onboarding README",
        changed_files=["README.md", "docs/setup.md"],
        diff_lines=18,
        author_family="gemini",
    )
    if (
        p_triv["tier"] != "trivial"
        or p_triv["router"]["claude"]["cli_model"] != "haiku"
        or p_triv["router"]["gemini"]["cli_model"] != "gemini-4-hydrogen-low"
        or p_triv["planner"]["gemini"]["cli_model"] != "gemini-4-hydrogen-low"
        or p_triv["executor"]["claude"]["cli_model"] != "sonnet"
        or p_triv["reviewer"]["primary"]["family"] != "claude"
        or p_triv["reviewer"]["primary"]["cli_model"] != "sonnet"
        or p_triv["reviewer"]["gemini"]["cli_model"] != "gemini-4-hydrogen-high"
    ):
        print(f"FAIL 2 trivial routing mismatch: {json.dumps(p_triv, indent=2)}")
        failures += 1
    else:
        print("PASS 2 trivial markdown task routes to fast Hydrogen/Sonnet/Haiku tiers without Carbon/Fable wait")

    # 3. Standard tier: normal single-service bug fix with unit test
    p_std = mr.select_task_models(
        title="[VP-19401] Handle empty middle name in patient mapper",
        changed_files=["src/mapper.py", "tests/test_mapper.py"],
        diff_lines=95,
        story_points=2,
        author_family="claude",
    )
    if (
        p_std["tier"] != "standard"
        or p_std["planner"]["claude"]["cli_model"] != "sonnet"
        or p_std["executor"]["gemini"]["cli_model"] != "gemini-4-hydrogen-high"
        or p_std["reviewer"]["primary"]["family"] != "gemini"
        or p_std["reviewer"]["primary"]["cli_model"] != "gemini-4-hydrogen-high"
    ):
        print(f"FAIL 3 standard routing mismatch: {json.dumps(p_std, indent=2)}")
        failures += 1
    else:
        print("PASS 3 standard code fix routes to Sonnet (high) and Gemini 4 Hydrogen (high)")

    # 4. Complex tier: governance / orchestration engine files (`bin/paf`, `CORE.md`) or round 2 escalation
    p_cplx = mr.select_task_models(
        title="[paf] Update persistent consensus orchestration",
        changed_files=["bin/paf", "CORE.md", "framework/scripts/tests/test_paf_cli.py"],
        diff_lines=220,
        author_family="gemini",
    )
    p_round2 = mr.select_task_models(
        title="Fix mapper edge case",
        changed_files=["src/mapper.py"],
        diff_lines=40,
        round_num=2,
        author_family="claude",
    )
    if (
        p_cplx["tier"] != "complex"
        or p_cplx["planner"]["claude"]["cli_model"] != "opus"
        or p_cplx["planner"]["gemini"]["cli_model"] != "gemini-4-argon-high"
        or p_cplx["executor"]["gemini"]["cli_model"] != "gemini-4-hydrogen-xhigh"
        or p_cplx["reviewer"]["primary"]["cli_model"] != "opus"
        or p_cplx["reviewer"]["gemini"]["cli_model"] != "gemini-4-argon-high"
        or p_round2["tier"] != "complex"
    ):
        print(f"FAIL 4 complex routing mismatch: cplx={p_cplx} round2={p_round2}")
        failures += 1
    else:
        print("PASS 4 governance/engine changes and contested round 2 escalate to Opus / Argon / Hydrogen-XHigh")

    # 5. Critical tier: DB schema migration / auth / HMAC / round 3 escalates to Carbon / Fable / Opus-XHigh
    p_crit = mr.select_task_models(
        title="[DB] Drop legacy token column and rotate OAuth HMAC verification",
        description="ALTER TABLE users DROP COLUMN legacy_oauth_token; update payment auth guard",
        changed_files=["migrations/20261116_drop_legacy_token.sql", "src/auth/hmac_guard.py"],
        diff_lines=310,
        author_family="claude",
        routing_cfg={"allow_apex_reviewer": True},
    )
    if (
        p_crit["tier"] != "critical"
        or p_crit["planner"]["claude"]["cli_model"] != "fable"
        or p_crit["planner"]["gemini"]["cli_model"] != "gemini-4-carbon-high"
        or p_crit["reviewer"]["primary"]["cli_model"] != "gemini-4-carbon-high"
        or p_crit["reviewer"]["claude"]["cli_model"] != "fable"
        or not p_crit["reasons"]
    ):
        print(f"FAIL 5 critical routing mismatch: {json.dumps(p_crit, indent=2)}")
        failures += 1
    else:
        print("PASS 5 DB schema/auth/HMAC task escalates planner & reviewer to Fable and Gemini 4 Carbon (High)")

    # 6. `paf select-model` CLI inspects live git diff on a temporary repository
    tmp_repo = _mkdtemp("paf-model-router-repo-")
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_repo, check=True)
    (tmp_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "commands": {"unit_test": ["true"]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "docs/readme-update"], cwd=tmp_repo, check=True, capture_output=True)
    (tmp_repo / "README.md").write_text("# Demo\n\nUpdated quickstart line.\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs: update quickstart"], cwd=tmp_repo, check=True, capture_output=True)

    cli_triv = subprocess.run(
        [
            sys.executable,
            str(PAF_BIN),
            "select-model",
            "--repo",
            str(tmp_repo),
            "--diff-base",
            "main",
            "--author-family",
            "gemini",
            "--json",
        ],
        cwd=tmp_repo,
        text=True,
        capture_output=True,
        check=False,
    )
    cli_triv_json = json.loads(cli_triv.stdout) if cli_triv.returncode == 0 and cli_triv.stdout.strip().startswith("{") else {}

    # Add a critical SQL migration on top and verify `paf select-model` escalates automatically
    (tmp_repo / "migrations").mkdir()
    (tmp_repo / "migrations" / "001_alter_orders.sql").write_text(
        "ALTER TABLE orders DROP COLUMN legacy_secret_token;\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "migrations/001_alter_orders.sql"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: drop legacy token column"], cwd=tmp_repo, check=True, capture_output=True)

    cli_crit = subprocess.run(
        [
            sys.executable,
            str(PAF_BIN),
            "select-model",
            "--repo",
            str(tmp_repo),
            "--diff-base",
            "main",
            "--author-family",
            "claude",
            "--json",
        ],
        cwd=tmp_repo,
        text=True,
        capture_output=True,
        check=False,
    )
    cli_crit_json = json.loads(cli_crit.stdout) if cli_crit.returncode == 0 and cli_crit.stdout.strip().startswith("{") else {}

    if (
        cli_triv.returncode != 0
        or cli_triv_json.get("tier") != "trivial"
        or cli_crit.returncode != 0
        or cli_crit_json.get("tier") != "critical"
        or cli_crit_json.get("reviewer", {}).get("primary", {}).get("cli_model") != "gemini-4-carbon-high"
    ):
        print(
            f"FAIL 6 paf select-model git diff detection:\n"
            f"  triv=({cli_triv.returncode}) {cli_triv.stdout} {cli_triv.stderr}\n"
            f"  crit=({cli_crit.returncode}) {cli_crit.stdout} {cli_crit.stderr}"
        )
        failures += 1
    else:
        print("PASS 6 `paf select-model --diff-base` inspects real git diffs and escalates from trivial to critical")

    # 7. `paf_watcher.watch_repo_once` self-assesses and passes explicit `--model` + `--effort` when unset
    key_dir = _mkdtemp("paf-mr-key-")
    key_file = key_dir / "consensus.key"
    pw.init_consensus_key(key_file)
    secret = pw.load_consensus_secret(key_file)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_repo, text=True, capture_output=True, check=True
    ).stdout.strip()

    mock_bin = _mkdtemp("paf-mr-bin-")
    agy_args_file = mock_bin / "agy_args.json"
    claude_args_file = mock_bin / "claude_args.json"
    fake_agy = mock_bin / "agy"
    fake_agy.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(agy_args_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "resp = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'prose': 'Critical review ok.'}\n"
        "print(json.dumps({'conversation_id': 'agy-mr-1', 'status': 'DONE', 'structured_output': resp}))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)
    fake_claude = mock_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(claude_args_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "resp = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'prose': 'Claude review ok.'}\n"
        "print(json.dumps({'session_id': 'claude-mr-1', 'is_error': False, 'structured_output': resp}))\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    # 7a: Claude-authored critical migration PR -> Gemini watcher auto-selects `gemini-4-carbon-high`
    m_claude_auth = {
        "v": 1,
        "role": "author",
        "model_family": "claude",
        "verdict": "open",
        "head_sha": head_sha,
        "host": "work-mac",
        "base_branch": "main",
        "pr_number": 51,
        "session_id": "claude-auth-51",
    }
    pw.sign_marker_in_place(m_claude_auth, secret, key_file=key_file, require_hmac=True)
    bus_crit = pw.InMemoryGitHubBus(
        repo_path=tmp_repo,
        prs={
            51: {
                "number": 51,
                "url": "https://example.test/pr/51",
                "state": "OPEN",
                "title": "Drop legacy token column",
                "headRefName": "docs/readme-update",
                "baseRefName": "main",
                "headRefOid": head_sha,
                "body": f"Migration\n\n<!-- paf-consensus: {json.dumps(m_claude_auth, separators=(',', ':'))} -->",
                "comments": [],
                "files": [{"path": "README.md"}, {"path": "migrations/001_alter_orders.sql"}],
                "reviewDecision": "",
            }
        },
        key_file=key_file,
    )
    pw.watch_repo_once(
        repo=tmp_repo,
        repo_slug="leolin84200704/tmp",
        host_id="personal-mac",
        families=("gemini",),
        state_dir=_mkdtemp("paf-mr-st1-"),
        key_file=key_file,
        bus=bus_crit,
        claude_bin=str(fake_claude),
        agy_bin=str(fake_agy),
        claim_settle_sec=0.0,
    )
    agy_seen = json.loads(agy_args_file.read_text(encoding="utf-8")) if agy_args_file.is_file() else []

    # 7b: Gemini-authored critical migration PR -> Claude watcher auto-selects `--model opus --effort xhigh`
    m_gem_auth = dict(m_claude_auth, model_family="gemini", pr_number=52, session_id="gem-auth-52")
    m_gem_auth.pop("sig", None)
    m_gem_auth.pop("key_id", None)
    pw.sign_marker_in_place(m_gem_auth, secret, key_file=key_file, require_hmac=True)
    bus_crit_c = pw.InMemoryGitHubBus(
        repo_path=tmp_repo,
        prs={
            52: {
                "number": 52,
                "url": "https://example.test/pr/52",
                "state": "OPEN",
                "title": "Drop legacy token column",
                "headRefName": "docs/readme-update",
                "baseRefName": "main",
                "headRefOid": head_sha,
                "body": f"Migration\n\n<!-- paf-consensus: {json.dumps(m_gem_auth, separators=(',', ':'))} -->",
                "comments": [],
                "files": [{"path": "README.md"}, {"path": "migrations/001_alter_orders.sql"}],
                "reviewDecision": "",
            }
        },
        key_file=key_file,
    )
    pw.watch_repo_once(
        repo=tmp_repo,
        repo_slug="leolin84200704/tmp",
        host_id="personal-mac",
        families=("claude",),
        state_dir=_mkdtemp("paf-mr-st2-"),
        key_file=key_file,
        bus=bus_crit_c,
        claude_bin=str(fake_claude),
        agy_bin=str(fake_agy),
        claim_settle_sec=0.0,
    )
    claude_seen = json.loads(claude_args_file.read_text(encoding="utf-8")) if claude_args_file.is_file() else []

    agy_model_val = agy_seen[agy_seen.index("--model") + 1] if "--model" in agy_seen else ""
    claude_model_val = claude_seen[claude_seen.index("--model") + 1] if "--model" in claude_seen else ""
    claude_effort_val = claude_seen[claude_seen.index("--effort") + 1] if "--effort" in claude_seen else ""

    if (
        agy_model_val != "gemini-4-carbon-high"
        or claude_model_val != "opus"
        or claude_effort_val != "xhigh"
    ):
        print(
            f"FAIL 7 watcher automatic model routing:\n"
            f"  agy_seen={agy_seen}\n  claude_seen={claude_seen}"
        )
        failures += 1
    else:
        print("PASS 7 `paf_watcher` automatically self-assesses PR risk and pins `--model` and `--effort`")

    # 8. `run_consensus_dialogue` starts fast on standard PRs (Round 1: sonnet/high) and escalates on Round 2 (opus/high)
    std_repo = _mkdtemp("paf-mr-dlg-repo-")
    subprocess.run(["git", "init", "-b", "main"], cwd=std_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=std_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=std_repo, check=True)
    (std_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "consensus": {"require_hmac": True, "max_rounds": 3},
                "commands": {"unit_test": ["true"], "post_merge_check": ["true"]},
            }
        ),
        encoding="utf-8",
    )
    (std_repo / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=std_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=std_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feat/std"], cwd=std_repo, check=True, capture_output=True)
    (std_repo / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=std_repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: bump value"], cwd=std_repo, check=True, capture_output=True)
    std_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=std_repo, text=True, capture_output=True, check=True
    ).stdout.strip()

    dlg_calls_log = mock_bin / "dlg_calls.jsonl"
    dlg_claude_bin = mock_bin / "dlg_claude"
    dlg_claude_bin.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"log_p = pathlib.Path({str(dlg_calls_log)!r})\n"
        "lines = log_p.read_text().splitlines() if log_p.is_file() else []\n"
        "rev_n = sum(1 for ln in lines if json.loads(ln)['cli'] == 'claude') + 1\n"
        "m = args[args.index('--model') + 1] if '--model' in args else ''\n"
        "e = args[args.index('--effort') + 1] if '--effort' in args else ''\n"
        "sid = args[args.index('--resume') + 1] if '--resume' in args else args[args.index('--session-id') + 1]\n"
        "with log_p.open('a') as fh:\n"
        "    fh.write(json.dumps({'cli': 'claude', 'round': rev_n, 'model': m, 'effort': e}) + '\\n')\n"
        "if rev_n == 1:\n"
        "    out = {'verdict': 'request_changes', 'blocking_count': 1, 'needs_human_decision': False, 'prose': 'Add note.'}\n"
        "else:\n"
        "    out = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'prose': 'Approved.'}\n"
        "print(json.dumps({'session_id': sid, 'is_error': False, 'structured_output': out}))\n",
        encoding="utf-8",
    )
    dlg_claude_bin.chmod(0o755)

    dlg_agy_bin = mock_bin / "dlg_agy"
    dlg_agy_bin.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"log_p = pathlib.Path({str(dlg_calls_log)!r})\n"
        "m = args[args.index('--model') + 1] if '--model' in args else ''\n"
        "with log_p.open('a') as fh:\n"
        "    fh.write(json.dumps({'cli': 'agy', 'model': m}) + '\\n')\n"
        "(pathlib.Path.cwd() / 'service.py').write_text('VALUE = 3\\n', encoding='utf-8')\n"
        "out = {'commit_message': 'fix: update value', 'reply_prose': 'Updated.', 'needs_human_decision': False}\n"
        "print(json.dumps({'status': 'DONE', 'conversation_id': 'agy-dlg-99', 'structured_output': out}))\n",
        encoding="utf-8",
    )
    dlg_agy_bin.chmod(0o755)

    m_dlg = {
        "v": 1,
        "role": "author",
        "model_family": "gemini",
        "verdict": "open",
        "head_sha": std_sha,
        "host": "personal-mac",
        "base_branch": "main",
        "pr_number": 60,
        "session_id": "caller-gem-60",
    }
    pw.sign_marker_in_place(m_dlg, secret, key_file=key_file, require_hmac=True)
    bus_dlg = pw.InMemoryGitHubBus(
        repo_path=std_repo,
        prs={
            60: {
                "number": 60,
                "url": "https://example.test/pr/60",
                "state": "OPEN",
                "title": "Bump value in service",
                "headRefName": "feat/std",
                "baseRefName": "main",
                "headRefOid": std_sha,
                "body": f"Small change\n\n<!-- paf-consensus: {json.dumps(m_dlg, separators=(',', ':'))} -->",
                "comments": [],
                "files": [{"path": "service.py"}],
                "reviewDecision": "",
            }
        },
        key_file=key_file,
    )
    fake_home = _mkdtemp("paf-mr-home-")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        dlg_res = pw.run_consensus_dialogue(
            repo=std_repo,
            pr_number=60,
            repo_slug="leolin84200704/std",
            author_family="gemini",
            caller_session_id="caller-gem-60",
            host_id="personal-mac",
            state_dir=_mkdtemp("paf-mr-dlg-st-"),
            key_file=key_file,
            bus=bus_dlg,
            claude_bin=str(dlg_claude_bin),
            agy_bin=str(dlg_agy_bin),
        )
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

    recorded = [json.loads(ln) for ln in dlg_calls_log.read_text().splitlines()] if dlg_calls_log.is_file() else []
    expected_recorded = [
        {"cli": "claude", "round": 1, "model": "sonnet", "effort": "high"},
        {"cli": "agy", "model": "gemini-4-hydrogen-high"},
        {"cli": "claude", "round": 2, "model": "opus", "effort": "high"},
    ]
    if (
        dlg_res.get("status") != "merged"
        or recorded != expected_recorded
        or dlg_res.get("model_routing", {}).get("tier") != "complex"
    ):
        print(f"FAIL 8 dialogue round escalation: res={dlg_res} recorded={recorded}")
        failures += 1
    else:
        print("PASS 8 `run_consensus_dialogue` starts on fast standard tier and escalates model on contested Round 2")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
