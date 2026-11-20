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

    # 5. Critical tier: DB schema migration / auth / HMAC / round 3 escalates to Argon (High) / Fable / Opus-XHigh
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
        or p_crit["planner"]["gemini"]["cli_model"] != "gemini-4-argon-high"
        or p_crit["reviewer"]["primary"]["cli_model"] != "gemini-4-argon-high"
        or p_crit["reviewer"]["claude"]["cli_model"] != "fable"
        or not p_crit["reasons"]
    ):
        print(f"FAIL 5 critical routing mismatch: {json.dumps(p_crit, indent=2)}")
        failures += 1
    else:
        print("PASS 5 DB schema/auth/HMAC task escalates planner & reviewer to Fable and Gemini 4 Argon (High)")

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
        or cli_crit_json.get("reviewer", {}).get("primary", {}).get("cli_model") != "gemini-4-argon-high"
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
        "resp = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'test_source_is_ground_truth': True, 'tests_prove_change': True, 'test_counterexamples': [], 'prose': 'Critical review ok.'}\n"
        "print(json.dumps({'conversation_id': 'agy-mr-1', 'status': 'DONE', 'structured_output': resp}))\n",
        encoding="utf-8",
    )
    fake_agy.chmod(0o755)
    fake_claude = mock_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(claude_args_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "resp = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'test_source_is_ground_truth': True, 'tests_prove_change': True, 'test_counterexamples': [], 'prose': 'Claude review ok.'}\n"
        "print(json.dumps({'session_id': 'claude-mr-1', 'is_error': False, 'structured_output': resp}))\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    # 7a: Claude-authored critical migration PR -> Gemini watcher auto-selects `gemini-4-argon-high`
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
        agy_model_val != "gemini-4-argon-high"
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
        "    out = {'verdict': 'request_changes', 'blocking_count': 1, 'needs_human_decision': False, 'test_source_is_ground_truth': True, 'tests_prove_change': False, 'test_counterexamples': [{'test_or_claim': 'note', 'counterexample': 'missing note', 'blocking': True}], 'prose': 'Add note.'}\n"
        "else:\n"
        "    out = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'test_source_is_ground_truth': True, 'tests_prove_change': True, 'test_counterexamples': [], 'prose': 'Approved.'}\n"
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

    # 9. Pure non-governance docs mentioning words like "Stripe" or "HMAC" stay `trivial`, whereas `PERMISSIONS.md` is `critical`
    p_doc_kw = mr.select_task_models(
        title="docs: explain Stripe webhook HMAC verification in README",
        changed_files=["README.md"],
        diff_patch="--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n+Verify Stripe webhook HMAC header.\n",
        author_family="claude",
    )
    p_perm_doc = mr.select_task_models(
        title="Update PERMISSIONS.md",
        changed_files=["PERMISSIONS.md"],
        diff_lines=10,
        author_family="claude",
    )
    if p_doc_kw["tier"] != "trivial" or p_perm_doc["tier"] != "critical":
        print(f"FAIL 9 doc keyword vs critical doc guard: doc_kw={p_doc_kw['tier']} perm={p_perm_doc['tier']}")
        failures += 1
    else:
        print("PASS 9 pure README mentioning HMAC/Stripe stays `trivial` while `PERMISSIONS.md` escalates to `critical`")

    # 10. Base-branch config isolation & non-lowering `reviewer` invariant (BLOCKING 1 regression)
    tamper_repo = _mkdtemp("paf-mr-tamper-repo-")
    subprocess.run(["git", "init", "-b", "main"], cwd=tamper_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tamper_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tamper_repo, check=True)
    (tamper_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "consensus": {"model_routing": {"min_tier": "standard"}},
                "commands": {"unit_test": ["true"]},
            }
        ),
        encoding="utf-8",
    )
    (tamper_repo / "app.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tamper_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tamper_repo, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "feat/self-downgrade-attempt"], cwd=tamper_repo, check=True, capture_output=True)
    (tamper_repo / ".agent-verify.json").write_text(
        json.dumps(
            {
                "version": 1,
                "zone": "autonomous",
                "default_branch": "main",
                "model_routing": {
                    "max_tier": "trivial",
                    "overrides": {
                        "reviewer_claude": "claude_haiku_low",
                        "reviewer_gemini": "gemini_hydrogen_low",
                    },
                },
                "commands": {"unit_test": ["true"]},
            }
        ),
        encoding="utf-8",
    )
    (tamper_repo / "src").mkdir()
    (tamper_repo / "src" / "auth_service.py").write_text("def verify_login():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tamper_repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: update auth and verify config"], cwd=tamper_repo, check=True, capture_output=True)

    base_loaded_cfg = mr.load_repo_routing_config(tamper_repo, base_branch="main")
    pr_tamper_plan = mr.select_pr_models(
        repo=tamper_repo,
        pr_data={
            "title": "Update auth service",
            "body": "Routine change",
            "baseRefName": "main",
            "files": [{"path": ".agent-verify.json"}, {"path": "src/auth_service.py"}],
        },
        author_family="gemini",
        round_num=1,
    )
    # Direct config guard: max_tier, weak reviewer overrides, cross-family overrides, and malformed min_tier
    direct_guard_plan = mr.select_task_models(
        title="Rotate crypto key",
        changed_files=["lib/crypto_utils.py"],
        author_family="gemini",
        routing_cfg={
            "min_tier": ["malformed_list"],
            "max_tier": "trivial",
            "overrides": {
                "reviewer_claude": "claude_haiku_low",
                "reviewer_gemini": "claude_opus_xhigh",
                "executor_gemini": "gemini_hydrogen_low",
            },
        },
    )
    escalate_override_plan = mr.select_task_models(
        title="Fix mapper",
        changed_files=["src/mapper.py"],
        author_family="gemini",
        routing_cfg={"overrides": {"reviewer_claude": "claude_fable_high"}},
    )
    if (
        base_loaded_cfg != {"min_tier": "standard"}
        or pr_tamper_plan["tier"] != "critical"
        or pr_tamper_plan["reviewer"]["claude"]["cli_model"] != "opus"
        or pr_tamper_plan["reviewer"]["claude"]["effort"] != "xhigh"
        or pr_tamper_plan["reviewer"]["gemini"]["cli_model"] != "gemini-4-argon-high"
        or direct_guard_plan["tier"] != "trivial"
        or direct_guard_plan["reviewer_tier"] != "critical"
        or direct_guard_plan["reviewer"]["claude"]["cli_model"] != "opus"
        or direct_guard_plan["reviewer"]["claude"]["effort"] != "xhigh"
        or direct_guard_plan["reviewer"]["gemini"]["cli_model"] != "gemini-4-argon-high"
        or direct_guard_plan["executor"]["gemini"]["cli_model"] != "gemini-4-hydrogen-low"
        or escalate_override_plan["reviewer"]["claude"]["cli_model"] != "fable"
    ):
        print(
            f"FAIL 10 base-branch & non-lowering reviewer guard:\n"
            f"  base_loaded_cfg={base_loaded_cfg}\n"
            f"  pr_tamper_plan={pr_tamper_plan}\n"
            f"  direct_guard_plan={direct_guard_plan}\n"
            f"  escalate_override_plan={escalate_override_plan}"
        )
        failures += 1
    else:
        print("PASS 10 PR-branch `.agent-verify.json` cannot downgrade review; `reviewer` enforces non-lowering invariant")

    # 11. `CRITICAL_PATH_RE` affix path matching without `author` false positives (BLOCKING 2 regression)
    must_be_critical = [
        "src/authentication/login.py",
        "src/authorization/policy.go",
        "src/auth_service.py",
        "src/AuthService.ts",
        "src/oauth2_client.py",
        "lib/crypto_utils.py",
        "app/security_helpers.ts",
        "services/billing_service.py",
        "services/payment_gateway.py",
        "db/migrations_v2/001_init.py",
    ]
    must_stay_standard = [
        "src/author_helper.py",
        "src/authors/profile.py",
        "src/coauthor.py",
        "src/authoring_tool.py",
    ]
    missed_crit = [
        p for p in must_be_critical if mr.select_task_models(changed_files=[p])["tier"] != "critical"
    ]
    false_pos_auth = [
        p for p in must_stay_standard if mr.select_task_models(changed_files=[p])["tier"] != "standard"
    ]
    if missed_crit or false_pos_auth:
        print(f"FAIL 11 critical path affix matching: missed={missed_crit} false_pos={false_pos_auth}")
        failures += 1
    else:
        print("PASS 11 `CRITICAL_PATH_RE` matches affix auth/crypto/security/billing paths without `author` false positives")

    # 12. `_extract_changed_patch_lines` counts `--` lines and blocks `+++ b/README.md` hunk spoofing
    spoof_diff = (
        "diff --git a/src/query.py b/src/query.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/query.py\n"
        "+++ b/src/query.py\n"
        "@@ -1,2 +1,3 @@\n"
        "--- legacy sql comment removed\n"
        "+++ b/README.md\n"
        "+ALTER TABLE accounts DROP COLUMN secret_hash;\n"
    )
    cnt_spoof, sample_spoof = mr._extract_changed_patch_lines(spoof_diff)
    spoof_plan = mr.select_task_models(
        title="Refactor query helper",
        changed_files=["src/query.py"],
        diff_patch=spoof_diff,
    )
    if cnt_spoof != 3 or "ALTER TABLE accounts" not in sample_spoof or spoof_plan["tier"] != "critical":
        print(f"FAIL 12 patch parser spoofing guard: cnt={cnt_spoof} sample={sample_spoof!r} tier={spoof_plan['tier']}")
        failures += 1
    else:
        print("PASS 12 `_extract_changed_patch_lines` accurately counts `--` deletions and blocks `+++` hunk header spoofing")

    # 13. Operator CLI override precedence, model-matched effort, `auto`, and invalid effort validation
    triv_claude_spec = p_triv["reviewer"]["claude"]  # sonnet / medium
    triv_gemini_spec = p_triv["reviewer"]["gemini"]  # gemini-4-hydrogen-high
    r_auto_c = pw._resolve_cli_model_and_effort("claude", triv_claude_spec, claude_model="auto", claude_effort="auto")
    r_fable_default_e = pw._resolve_cli_model_and_effort("claude", triv_claude_spec, claude_model="fable")
    r_haiku_default_e = pw._resolve_cli_model_and_effort("claude", triv_claude_spec, claude_model="haiku")
    r_explicit_both = pw._resolve_cli_model_and_effort(
        "claude", triv_claude_spec, claude_model="opus", claude_effort="max"
    )
    r_agy_override = pw._resolve_cli_model_and_effort(
        "gemini", triv_gemini_spec, agy_model="gemini-4-argon-medium", agy_effort="high"
    )
    bad_effort_caught = False
    try:
        pw._resolve_cli_model_and_effort("claude", triv_claude_spec, claude_effort="invalid_level")
    except ValueError:
        bad_effort_caught = True
    if (
        r_auto_c != ("sonnet", "medium")
        or r_fable_default_e != ("fable", "high")
        or r_haiku_default_e != ("haiku", "low")
        or r_explicit_both != ("opus", "max")
        or r_agy_override != ("gemini-4-argon-medium", "high")
        or not bad_effort_caught
    ):
        print(
            f"FAIL 13 operator override precedence: auto={r_auto_c} fable={r_fable_default_e} "
            f"haiku={r_haiku_default_e} both={r_explicit_both} agy={r_agy_override} bad_caught={bad_effort_caught}"
        )
        failures += 1
    else:
        print("PASS 13 `_resolve_cli_model_and_effort` honors explicit flags, pairs model-matched effort, and rejects bad effort")

    # 14. Lazy `bin/paf` parser isolation (`model_router` key + dest parity) + live installed CLI smoke check
    lazy_check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import argparse, importlib.machinery, importlib.util, sys; "
                f"p = {str(PAF_BIN)!r}; "
                "loader = importlib.machinery.SourceFileLoader('paf_lazy_chk', p); "
                "spec = importlib.util.spec_from_loader('paf_lazy_chk', loader); "
                "mod = importlib.util.module_from_spec(spec); "
                "loader.exec_module(mod); "
                "parser = mod.build_parser(); "
                "lazy_ok = ('model_router' not in sys.modules); "
                "rm = mod._router_mod(); "
                "loaded_ok = ('model_router' in sys.modules); "
                "sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)); "
                "paf_dests = {a.dest for a in sub_action.choices['select-model']._actions if a.dest != 'func'}; "
                "mr_dests = {a.dest for a in rm.build_arg_parser()._actions}; "
                "raise SystemExit(0 if (lazy_ok and loaded_ok and paf_dests == mr_dests) else 1)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    live_cli_errors: list[str] = []
    real_claude = "/Users/linhungfan/bin/claude" if os.path.isfile("/Users/linhungfan/bin/claude") else shutil.which("claude")
    if real_claude:
        ch = subprocess.run([real_claude, "--help"], capture_output=True, text=True, timeout=10, check=False)
        ch_out = (ch.stdout or "") + "\n" + (ch.stderr or "")
        for token in ("--model", "--effort", "xhigh"):
            if token not in ch_out:
                live_cli_errors.append(f"claude --help missing {token!r}")
    real_agy = "/Users/linhungfan/bin/agy" if os.path.isfile("/Users/linhungfan/bin/agy") else shutil.which("agy")
    if real_agy:
        ah = subprocess.run([real_agy, "--help"], capture_output=True, text=True, timeout=10, check=False)
        ah_out = (ah.stdout or "") + "\n" + (ah.stderr or "")
        for token in ("--model", "--effort"):
            if token not in ah_out:
                live_cli_errors.append(f"agy --help missing {token!r}")
        am = subprocess.run([real_agy, "models"], capture_output=True, text=True, timeout=10, check=False)
        am_out = (am.stdout or "") + "\n" + (am.stderr or "")
        if am.returncode == 0 and "gemini-" in am_out:
            for spec in mr.MODEL_CATALOG.values():
                if spec["family"] == "gemini" and spec["cli_model"] not in am_out:
                    live_cli_errors.append(f"agy models missing catalog model {spec['cli_model']!r}")
    if lazy_check.returncode != 0 or live_cli_errors:
        print(f"FAIL 14 lazy parser & live CLI smoke: lazy_rc={lazy_check.returncode} stderr={lazy_check.stderr} cli_errs={live_cli_errors}")
        failures += 1
    else:
        print("PASS 14 `bin/paf` builds parser lazily (`model_router` unimported, arg dests in sync) and live CLIs match catalog")

    # 15. Re-driven `run_consensus_dialogue` preserves contested round escalation + tail sample + CSV + bad diff-base warning
    redrive_repo = _mkdtemp("paf-mr-redrive-repo-")
    subprocess.run(["git", "init", "-b", "main"], cwd=redrive_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=redrive_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=redrive_repo, check=True)
    (redrive_repo / ".agent-verify.json").write_text(
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
    (redrive_repo / "service.py").write_text("VALUE = 10\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=redrive_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=redrive_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feat/redrive"], cwd=redrive_repo, check=True, capture_output=True)
    (redrive_repo / "service.py").write_text("VALUE = 20\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=redrive_repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat: set 20"], cwd=redrive_repo, check=True, capture_output=True)
    rd_sha1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=redrive_repo, text=True, capture_output=True, check=True
    ).stdout.strip()

    redrive_log = mock_bin / "redrive_calls.jsonl"
    redrive_claude = mock_bin / "redrive_claude"
    redrive_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"log_p = pathlib.Path({str(redrive_log)!r})\n"
        "m = args[args.index('--model') + 1] if '--model' in args else ''\n"
        "e = args[args.index('--effort') + 1] if '--effort' in args else ''\n"
        "sid = args[args.index('--resume') + 1] if '--resume' in args else args[args.index('--session-id') + 1]\n"
        "with log_p.open('a') as fh:\n"
        "    fh.write(json.dumps({'model': m, 'effort': e}) + '\\n')\n"
        "out = {'verdict': 'approve', 'blocking_count': 0, 'needs_human_decision': False, 'test_source_is_ground_truth': True, 'tests_prove_change': True, 'test_counterexamples': [], 'prose': 'Approved on re-drive.'}\n"
        "print(json.dumps({'session_id': sid, 'is_error': False, 'structured_output': out}))\n",
        encoding="utf-8",
    )
    redrive_claude.chmod(0o755)

    # Seed PR #61 with an existing Round 1 `request_changes` marker on the SAME head (`rd_sha1`)
    m_rd_auth = {
        "v": 1,
        "role": "author",
        "model_family": "gemini",
        "verdict": "open",
        "head_sha": rd_sha1,
        "host": "personal-mac",
        "base_branch": "main",
        "pr_number": 61,
        "session_id": "caller-rd-61",
    }
    pw.sign_marker_in_place(m_rd_auth, secret, key_file=key_file, require_hmac=True)
    m_rd_rev1 = {
        "v": 1,
        "role": "reviewer",
        "model_family": "claude",
        "verdict": "request_changes",
        "head_sha": rd_sha1,
        "round": 1,
        "blocking_count": 1,
        "needs_human_decision": False,
        "session_id": "prior-claude-rev-1",
        "host": "personal-mac",
        "base_branch": "main",
        "pr_number": 61,
    }
    pw.sign_marker_in_place(m_rd_rev1, secret, key_file=key_file, require_hmac=True)
    bus_rd = pw.InMemoryGitHubBus(
        repo_path=redrive_repo,
        prs={
            61: {
                "number": 61,
                "url": "https://example.test/pr/61",
                "state": "OPEN",
                "title": "Set value 20",
                "headRefName": "feat/redrive",
                "baseRefName": "main",
                "headRefOid": rd_sha1,
                "body": f"Small change\n\n<!-- paf-consensus: {json.dumps(m_rd_auth, separators=(',', ':'))} -->",
                "comments": [
                    {
                        "id": "c-rd-1",
                        "body": f"Needs fix.\n\n<!-- paf-consensus: {json.dumps(m_rd_rev1, separators=(',', ':'))} -->",
                    }
                ],
                "files": [{"path": "service.py"}],
                "reviewDecision": "",
            }
        },
        key_file=key_file,
    )
    old_home_rd = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        rd_res = pw.run_consensus_dialogue(
            repo=redrive_repo,
            pr_number=61,
            repo_slug="leolin84200704/redrive",
            author_family="gemini",
            caller_session_id="caller-rd-61",
            host_id="personal-mac",
            state_dir=_mkdtemp("paf-mr-rd-st-"),
            key_file=key_file,
            bus=bus_rd,
            claude_bin=str(redrive_claude),
            agy_bin=str(dlg_agy_bin),
        )
    finally:
        if old_home_rd is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home_rd

    rd_calls = [json.loads(ln) for ln in redrive_log.read_text().splitlines()] if redrive_log.is_file() else []
    csv_plan = mr.select_task_models(title="Update seed mapping", changed_files=["data/seeds.csv"], diff_lines=12)
    tail_patch = (
        "--- a/src/batch.py\n+++ b/src/batch.py\n@@ -1 +1,450 @@\n"
        + "".join(f"+x_{i} = {i}\n" for i in range(445))
        + "+ALTER TABLE ledger DROP COLUMN balance_cents;\n"
    )
    tail_plan = mr.select_task_models(title="Update batch script", changed_files=["src/batch.py"], diff_patch=tail_patch)
    warn_diff = subprocess.run(
        [sys.executable, str(PAF_BIN), "select-model", "--repo", str(redrive_repo), "--diff-base", "nonexistent-branch-xyz"],
        capture_output=True,
        text=True,
        check=False,
    )
    if (
        rd_res.get("status") != "merged"
        or rd_calls != [{"model": "opus", "effort": "high"}]
        or csv_plan["tier"] != "standard"
        or tail_plan["tier"] != "critical"
        or "warning: git base ref" not in warn_diff.stderr
    ):
        print(
            f"FAIL 15 re-driven dialogue escalation & polish guards:\n"
            f"  rd_res={rd_res} rd_calls={rd_calls}\n"
            f"  csv_tier={csv_plan['tier']} tail_tier={tail_plan['tier']} warn_stderr={warn_diff.stderr!r}"
        )
        failures += 1
    else:
        print("PASS 15 re-driven `run_consensus_dialogue` escalates round=2 to `opus/high`, samples diff tail, treats CSV as code/data, and warns on missing `--diff-base`")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
