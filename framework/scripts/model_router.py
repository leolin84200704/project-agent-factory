#!/usr/bin/env python3
"""model_router — Deterministic task & PR model selection engine for Gemini 4 and Claude 5.

Single source of truth implementing the benchmark-backed routing rules from
`framework/MODEL-BENCHMARK.md`. Evaluates incoming tickets, tasks, and PR diffs
across four complexity/risk tiers (`trivial`, `standard`, `complex`, `critical`)
and selects explicit `--model` and `--effort` flags for:
  1. `router`   — Fast intake triage (1-5s)
  2. `planner`  — Architecture, root-cause analysis, and debate (Work Loop Steps 2-4)
  3. `executor` — Implementation and consensus author-fix turns (Work Loop Step 5)
  4. `reviewer` — Adversarial verification and cross-model PR consensus (Work Loop Step 6)
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

TIERS = ("trivial", "standard", "complex", "critical")
TIER_RANK = {name: idx for idx, name in enumerate(TIERS)}

MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "gemini_carbon_high": {
        "id": "gemini_carbon_high",
        "family": "gemini",
        "cli_model": "gemini-4-carbon-high",
        "effort": "high",
        "display_name": "Gemini 4 Carbon (High)",
        "scores": {"planning": 9.7, "coding": 9.3, "debugging": 9.6, "review": 9.6, "tool_chain": 9.4, "speed": 2.5},
        "ttft_sec": "120-160s",
        "task_minutes": "6-15m+",
    },
    "gemini_carbon_medium": {
        "id": "gemini_carbon_medium",
        "family": "gemini",
        "cli_model": "gemini-4-carbon-medium",
        "effort": "medium",
        "display_name": "Gemini 4 Carbon (Medium)",
        "scores": {"planning": 9.4, "coding": 9.2, "debugging": 9.4, "review": 9.4, "tool_chain": 9.2, "speed": 4.0},
        "ttft_sec": "60-100s",
        "task_minutes": "4-10m",
    },
    "gemini_argon_high": {
        "id": "gemini_argon_high",
        "family": "gemini",
        "cli_model": "gemini-4-argon-high",
        "effort": "high",
        "display_name": "Gemini 4 Argon (High)",
        "scores": {"planning": 9.1, "coding": 9.0, "debugging": 9.1, "review": 9.2, "tool_chain": 9.0, "speed": 5.0},
        "ttft_sec": "35-55s",
        "task_minutes": "3-7m",
    },
    "gemini_argon_medium": {
        "id": "gemini_argon_medium",
        "family": "gemini",
        "cli_model": "gemini-4-argon-medium",
        "effort": "medium",
        "display_name": "Gemini 4 Argon (Medium)",
        "scores": {"planning": 8.8, "coding": 8.8, "debugging": 8.8, "review": 8.9, "tool_chain": 8.8, "speed": 6.2},
        "ttft_sec": "20-35s",
        "task_minutes": "2-5m",
    },
    "gemini_hydrogen_xhigh": {
        "id": "gemini_hydrogen_xhigh",
        "family": "gemini",
        "cli_model": "gemini-4-hydrogen-xhigh",
        "effort": "xhigh",
        "display_name": "Gemini 4 Hydrogen (XHigh)",
        "scores": {"planning": 8.8, "coding": 8.9, "debugging": 8.7, "review": 8.8, "tool_chain": 8.8, "speed": 5.5},
        "ttft_sec": "20-45s",
        "task_minutes": "2.5-6m",
    },
    "gemini_hydrogen_high": {
        "id": "gemini_hydrogen_high",
        "family": "gemini",
        "cli_model": "gemini-4-hydrogen-high",
        "effort": "high",
        "display_name": "Gemini 4 Hydrogen (High)",
        "scores": {"planning": 8.2, "coding": 8.6, "debugging": 8.2, "review": 8.3, "tool_chain": 8.5, "speed": 8.0},
        "ttft_sec": "5-15s",
        "task_minutes": "1-3m",
    },
    "gemini_hydrogen_medium": {
        "id": "gemini_hydrogen_medium",
        "family": "gemini",
        "cli_model": "gemini-4-hydrogen-medium",
        "effort": "medium",
        "display_name": "Gemini 4 Hydrogen (Medium)",
        "scores": {"planning": 7.4, "coding": 8.0, "debugging": 7.4, "review": 7.3, "tool_chain": 7.9, "speed": 9.2},
        "ttft_sec": "2-6s",
        "task_minutes": "30s-1.5m",
    },
    "gemini_hydrogen_low": {
        "id": "gemini_hydrogen_low",
        "family": "gemini",
        "cli_model": "gemini-4-hydrogen-low",
        "effort": "low",
        "display_name": "Gemini 4 Hydrogen (Low)",
        "scores": {"planning": 6.8, "coding": 7.6, "debugging": 6.8, "review": 6.7, "tool_chain": 7.4, "speed": 9.5},
        "ttft_sec": "1-5s",
        "task_minutes": "20s-1m",
    },
    "claude_fable_high": {
        "id": "claude_fable_high",
        "family": "claude",
        "cli_model": "fable",
        "canonical_model": "claude-fable-5",
        "effort": "high",
        "display_name": "Claude Fable 5 (High)",
        "scores": {"planning": 9.9, "coding": 9.7, "debugging": 9.8, "review": 9.8, "tool_chain": 9.7, "speed": 3.0},
        "ttft_sec": "30-90s",
        "task_minutes": "5-12m+",
    },
    "claude_opus_xhigh": {
        "id": "claude_opus_xhigh",
        "family": "claude",
        "cli_model": "opus",
        "canonical_model": "claude-opus-5",
        "effort": "xhigh",
        "display_name": "Claude Opus 5 (XHigh)",
        "scores": {"planning": 9.6, "coding": 9.6, "debugging": 9.6, "review": 9.6, "tool_chain": 9.6, "speed": 4.2},
        "ttft_sec": "20-45s",
        "task_minutes": "3-7m",
    },
    "claude_opus_high": {
        "id": "claude_opus_high",
        "family": "claude",
        "cli_model": "opus",
        "canonical_model": "claude-opus-5",
        "effort": "high",
        "display_name": "Claude Opus 5 (High)",
        "scores": {"planning": 9.4, "coding": 9.5, "debugging": 9.4, "review": 9.4, "tool_chain": 9.5, "speed": 5.5},
        "ttft_sec": "10-30s",
        "task_minutes": "2-5m",
    },
    "claude_sonnet_high": {
        "id": "claude_sonnet_high",
        "family": "claude",
        "cli_model": "sonnet",
        "canonical_model": "claude-sonnet-5",
        "effort": "high",
        "display_name": "Claude Sonnet 5 (High)",
        "scores": {"planning": 8.4, "coding": 8.9, "debugging": 8.4, "review": 8.5, "tool_chain": 8.7, "speed": 7.8},
        "ttft_sec": "4-12s",
        "task_minutes": "1-2.5m",
    },
    "claude_sonnet_medium": {
        "id": "claude_sonnet_medium",
        "family": "claude",
        "cli_model": "sonnet",
        "canonical_model": "claude-sonnet-5",
        "effort": "medium",
        "display_name": "Claude Sonnet 5 (Medium)",
        "scores": {"planning": 8.1, "coding": 8.7, "debugging": 8.1, "review": 8.3, "tool_chain": 8.5, "speed": 8.4},
        "ttft_sec": "3-10s",
        "task_minutes": "45s-2m",
    },
    "claude_sonnet_low": {
        "id": "claude_sonnet_low",
        "family": "claude",
        "cli_model": "sonnet",
        "canonical_model": "claude-sonnet-5",
        "effort": "low",
        "display_name": "Claude Sonnet 5 (Low)",
        "scores": {"planning": 7.5, "coding": 8.2, "debugging": 7.4, "review": 7.5, "tool_chain": 8.0, "speed": 9.1},
        "ttft_sec": "2-5s",
        "task_minutes": "25s-1m",
    },
    "claude_haiku_low": {
        "id": "claude_haiku_low",
        "family": "claude",
        "cli_model": "haiku",
        "canonical_model": "claude-haiku-4-5",
        "effort": "low",
        "display_name": "Claude Haiku 4.5 (Low)",
        "scores": {"planning": 6.0, "coding": 6.8, "debugging": 5.8, "review": 5.8, "tool_chain": 6.5, "speed": 9.8},
        "ttft_sec": "1-3s",
        "task_minutes": "15-45s",
    },
}

TIER_ROUTING_TABLE: dict[str, dict[str, dict[str, str]]] = {
    "trivial": {
        "router": {"claude": "claude_haiku_low", "gemini": "gemini_hydrogen_low"},
        "planner": {"claude": "claude_sonnet_low", "gemini": "gemini_hydrogen_low"},
        "executor": {"claude": "claude_sonnet_low", "gemini": "gemini_hydrogen_medium"},
        "reviewer": {"claude": "claude_sonnet_medium", "gemini": "gemini_hydrogen_high"},
    },
    "standard": {
        "router": {"claude": "claude_haiku_low", "gemini": "gemini_hydrogen_low"},
        "planner": {"claude": "claude_sonnet_high", "gemini": "gemini_hydrogen_high"},
        "executor": {"claude": "claude_sonnet_high", "gemini": "gemini_hydrogen_high"},
        "reviewer": {"claude": "claude_sonnet_high", "gemini": "gemini_hydrogen_high"},
    },
    "complex": {
        "router": {"claude": "claude_haiku_low", "gemini": "gemini_hydrogen_low"},
        "planner": {"claude": "claude_opus_high", "gemini": "gemini_argon_high"},
        "executor": {"claude": "claude_opus_high", "gemini": "gemini_hydrogen_xhigh"},
        "reviewer": {"claude": "claude_opus_high", "gemini": "gemini_argon_high"},
    },
    "critical": {
        "router": {"claude": "claude_haiku_low", "gemini": "gemini_hydrogen_low"},
        "planner": {"claude": "claude_fable_high", "gemini": "gemini_carbon_high"},
        "executor": {"claude": "claude_opus_xhigh", "gemini": "gemini_argon_high"},
        "reviewer": {"claude": "claude_opus_xhigh", "gemini": "gemini_carbon_high"},
    },
}

TIER_ESTIMATED_MINUTES: dict[str, str] = {
    "trivial": "0.5-2 min",
    "standard": "2-5 min",
    "complex": "5-12 min",
    "critical": "10-25 min",
}

CRITICAL_PATH_RE = re.compile(
    r"(^|/)(?:db/migrate|prisma/migrations|alembic|sql/migrations)(/|$)|"
    r"(^|/)[\w.-]*(?:"
    r"migrations?|auth(?!or(?!i[zs]))|oauth(?!or)|security|crypto|hmac|billing|payments?|stripe"
    r")[\w.-]*(/|$)|"
    r"(^|/)(?:PERMISSIONS\.md|pre-tool-guard\.py|consensus\.key)$|"
    r"\.(?:key|pem|sql)$",
    re.IGNORECASE,
)

COMPLEX_GOVERNANCE_PATH_RE = re.compile(
    r"(^|/)(CORE\.md|AGENTS\.md|CLAUDE\.md|GEMINI\.md|WORK-LOOP\.md|FEEDBACK\.md|"
    r"\.agent-verify\.json|run-regression\.py|dune_lint\.py|paf_watcher\.py|model_router\.py)$|"
    r"^bin/paf$|"
    r"(^|/)(\.claude/settings[^/]*\.json|\.gemini/settings[^/]*\.json|\.gemini/config/|"
    r"\.github/workflows/[^/]+\.ya?ml|"
    r"hooks/|githooks/|framework/githooks/|framework/hooks/|"
    r"DailyJob/|launchd/|skills/)",
)

CRITICAL_TEXT_RE = re.compile(
    r"\b(alter\s+table|drop\s+(?:table|column|index)|truncate\s+table|"
    r"schema\s+migration|data\s+backfill|production\s+incident|prod\s+outage|sev[01]|"
    r"hmac|signature\s+bypass|privilege\s+escalation|rce|sql\s+injection|"
    r"oauth\s+token|rotate\s+secret|stripe|payment\s+webhook|deadlock)\b",
    re.IGNORECASE,
)

COMPLEX_TEXT_RE = re.compile(
    r"\b(cross-repo|multi-repo|state\s+machine|concurrency|race\s+condition|"
    r"architecture|refactor\s+pipeline|consensus\s+orchestration|daemon)\b",
    re.IGNORECASE,
)

TRIVIAL_DOC_EXTS = {".md", ".txt", ".rst", ".adoc"}

FAMILY_ALIASES = {
    "claude": "claude",
    "anthropic": "claude",
    "opus": "claude",
    "sonnet": "claude",
    "haiku": "claude",
    "fable": "claude",
    "gemini": "gemini",
    "google": "gemini",
    "agy": "gemini",
    "antigravity": "gemini",
}


def normalize_family(raw: str | None) -> str:
    if not raw:
        return ""
    return FAMILY_ALIASES.get(raw.strip().lower(), "")


def _clamp_tier(tier: str, min_tier: Any, max_tier: Any) -> str:
    rank = TIER_RANK.get(tier, 1)
    if isinstance(min_tier, str) and min_tier in TIER_RANK:
        rank = max(rank, TIER_RANK[min_tier])
    if isinstance(max_tier, str) and max_tier in TIER_RANK:
        rank = min(rank, TIER_RANK[max_tier])
    return TIERS[rank]


def _is_prose_doc_path(raw_target: str) -> bool:
    if not raw_target or raw_target == "/dev/null":
        return False
    return (
        Path(raw_target).suffix.lower() in TRIVIAL_DOC_EXTS
        and not CRITICAL_PATH_RE.search(raw_target)
        and not COMPLEX_GOVERNANCE_PATH_RE.search(raw_target)
    )


def _extract_changed_patch_lines(diff_patch: str) -> tuple[int, str]:
    if not diff_patch:
        return 0, ""
    head_lines: list[str] = []
    tail_lines: deque[str] = deque(maxlen=200)
    count = 0
    current_is_prose_doc = False
    pending_old_path = ""
    in_hunk = False
    for line in diff_patch.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            pending_old_path = ""
            continue
        if not in_hunk:
            if line.startswith("--- "):
                raw_old = line[4:].strip()
                if raw_old.startswith("a/"):
                    raw_old = raw_old[2:]
                pending_old_path = "" if raw_old == "/dev/null" else raw_old
                continue
            if line.startswith("+++ "):
                raw_target = line[4:].strip()
                if raw_target.startswith("b/"):
                    raw_target = raw_target[2:]
                eff_path = pending_old_path if raw_target == "/dev/null" else raw_target
                current_is_prose_doc = _is_prose_doc_path(eff_path)
                continue
            if line.startswith("@@"):
                in_hunk = True
                continue
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("\\"):
            continue
        if line.startswith(("+", "-")):
            count += 1
            if not current_is_prose_doc:
                body_line = line[1:]
                if len(head_lines) < 300:
                    head_lines.append(body_line)
                else:
                    tail_lines.append(body_line)
    return count, "\n".join(head_lines + list(tail_lines))


def _compute_intrinsic_tier(
    *,
    title: str = "",
    description: str = "",
    changed_files: list[str] | None = None,
    diff_lines: int | None = None,
    diff_patch: str = "",
    story_points: float | int | None = None,
    round_num: int = 1,
    is_incident: bool = False,
    explicit_tier: str | None = None,
) -> tuple[str, list[str]]:
    if isinstance(explicit_tier, str) and explicit_tier in TIER_RANK:
        return explicit_tier, [f"explicit_tier={explicit_tier}"]

    files = [re.sub(r"^(?:\./)+", "", str(f).strip()) for f in (changed_files or []) if str(f).strip()]
    crit_files = [f for f in files if CRITICAL_PATH_RE.search(f)]
    gov_files = [f for f in files if COMPLEX_GOVERNANCE_PATH_RE.search(f)]
    pure_non_gov_docs = (
        bool(files)
        and all(Path(f).suffix.lower() in TRIVIAL_DOC_EXTS for f in files)
        and not crit_files
        and not gov_files
    )

    patch_line_count, patch_sample = _extract_changed_patch_lines(diff_patch)
    eff_diff_lines = int(diff_lines) if diff_lines is not None else patch_line_count
    combined_text = "\n".join(part for part in (title, description[:4000], patch_sample) if part)

    critical_reasons: list[str] = []
    complex_reasons: list[str] = []

    if is_incident:
        critical_reasons.append("production_incident=true")
    if round_num >= 3:
        critical_reasons.append(f"consensus_round={round_num} (tiebreak escalation)")
    if story_points is not None and float(story_points) >= 8:
        critical_reasons.append(f"story_points={story_points}")
    if len(files) > 20:
        critical_reasons.append(f"changed_files={len(files)} (>20)")
    if eff_diff_lines > 1200:
        critical_reasons.append(f"diff_lines={eff_diff_lines} (>1200)")

    if crit_files:
        critical_reasons.append(f"critical_paths={','.join(crit_files[:3])}")

    if not pure_non_gov_docs:
        crit_kw = CRITICAL_TEXT_RE.search(combined_text)
        if crit_kw:
            critical_reasons.append(f"critical_signal='{crit_kw.group(1).lower()}'")

    if critical_reasons:
        return "critical", critical_reasons

    if round_num == 2:
        complex_reasons.append("consensus_round=2 (contested follow-up)")
    if story_points is not None and float(story_points) >= 5:
        complex_reasons.append(f"story_points={story_points}")
    if len(files) > 8:
        complex_reasons.append(f"changed_files={len(files)} (>8)")
    if eff_diff_lines > 400:
        complex_reasons.append(f"diff_lines={eff_diff_lines} (>400)")

    if gov_files:
        complex_reasons.append(f"governance_or_engine_paths={','.join(gov_files[:3])}")

    if not pure_non_gov_docs:
        cplx_kw = COMPLEX_TEXT_RE.search(combined_text)
        if cplx_kw:
            complex_reasons.append(f"complex_signal='{cplx_kw.group(1).lower()}'")

    if complex_reasons:
        return "complex", complex_reasons

    if pure_non_gov_docs and len(files) <= 3 and eff_diff_lines <= 120:
        return "trivial", [f"docs_only ({len(files)} file(s), {eff_diff_lines} diff lines)"]

    if (
        not files
        and story_points is not None
        and float(story_points) <= 1
        and eff_diff_lines <= 60
        and re.search(r"\b(typo|readme|doc|docs|comment|copy|rename\s+label)\b", combined_text, re.IGNORECASE)
    ):
        return "trivial", ["story_points<=1 trivial text/doc task"]

    std_reasons = []
    if files:
        std_reasons.append(f"standard_scope ({len(files)} file(s), {eff_diff_lines} diff lines)")
    elif story_points is not None:
        std_reasons.append(f"story_points={story_points}")
    else:
        std_reasons.append("default_standard_engineering_task")
    return "standard", std_reasons


def assess_task_complexity(
    *,
    title: str = "",
    description: str = "",
    changed_files: list[str] | None = None,
    diff_lines: int | None = None,
    diff_patch: str = "",
    story_points: float | int | None = None,
    round_num: int = 1,
    is_incident: bool = False,
    explicit_tier: str | None = None,
    routing_cfg: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    cfg = routing_cfg if isinstance(routing_cfg, dict) else {}
    raw_tier, reasons = _compute_intrinsic_tier(
        title=title,
        description=description,
        changed_files=changed_files,
        diff_lines=diff_lines,
        diff_patch=diff_patch,
        story_points=story_points,
        round_num=round_num,
        is_incident=is_incident,
        explicit_tier=explicit_tier,
    )
    return _clamp_tier(raw_tier, cfg.get("min_tier"), cfg.get("max_tier")), reasons


def _spec_view(catalog_key: str) -> dict[str, Any]:
    spec = MODEL_CATALOG[catalog_key]
    cli_flags = ["--model", spec["cli_model"]]
    if spec["family"] == "claude":
        cli_flags.extend(["--effort", spec["effort"]])
    return {
        "catalog_id": spec["id"],
        "family": spec["family"],
        "cli_model": spec["cli_model"],
        "effort": spec["effort"],
        "display_name": spec["display_name"],
        "cli_flags": cli_flags,
        "ttft_sec": spec["ttft_sec"],
        "task_minutes": spec["task_minutes"],
        "scores": dict(spec["scores"]),
    }


def select_task_models(
    *,
    title: str = "",
    description: str = "",
    changed_files: list[str] | None = None,
    diff_lines: int | None = None,
    diff_patch: str = "",
    story_points: float | int | None = None,
    round_num: int = 1,
    is_incident: bool = False,
    author_family: str | None = None,
    explicit_tier: str | None = None,
    routing_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = routing_cfg if isinstance(routing_cfg, dict) else {}
    raw_tier, reasons = _compute_intrinsic_tier(
        title=title,
        description=description,
        changed_files=changed_files,
        diff_lines=diff_lines,
        diff_patch=diff_patch,
        story_points=story_points,
        round_num=round_num,
        is_incident=is_incident,
        explicit_tier=explicit_tier,
    )
    tier = _clamp_tier(raw_tier, cfg.get("min_tier"), cfg.get("max_tier"))
    # Security invariant: repo routing config may escalate review strength (`min_tier`),
    # never downgrade `reviewer` below the diff's intrinsic risk tier (`raw_tier`).
    reviewer_tier = _clamp_tier(raw_tier, cfg.get("min_tier"), None)

    row = TIER_ROUTING_TABLE[tier]
    rev_row = TIER_ROUTING_TABLE[reviewer_tier]
    allow_apex_rev = bool(cfg.get("allow_apex_reviewer", False))

    rev_claude_key = (
        "claude_fable_high"
        if (reviewer_tier == "critical" and allow_apex_rev)
        else rev_row["reviewer"]["claude"]
    )
    rev_gemini_key = rev_row["reviewer"]["gemini"]

    overrides = cfg.get("overrides") if isinstance(cfg.get("overrides"), dict) else {}

    def _pick(role: str, fam: str, default_key: str) -> dict[str, Any]:
        ov_key = overrides.get(f"{role}_{fam}")
        if (
            isinstance(ov_key, str)
            and ov_key in MODEL_CATALOG
            and MODEL_CATALOG[ov_key]["family"] == fam
        ):
            if (
                role != "reviewer"
                or MODEL_CATALOG[ov_key]["scores"]["review"] >= MODEL_CATALOG[default_key]["scores"]["review"]
            ):
                return _spec_view(ov_key)
        return _spec_view(default_key)

    router_pair = {
        "claude": _pick("router", "claude", row["router"]["claude"]),
        "gemini": _pick("router", "gemini", row["router"]["gemini"]),
    }
    planner_pair = {
        "claude": _pick("planner", "claude", row["planner"]["claude"]),
        "gemini": _pick("planner", "gemini", row["planner"]["gemini"]),
    }
    executor_pair = {
        "claude": _pick("executor", "claude", row["executor"]["claude"]),
        "gemini": _pick("executor", "gemini", row["executor"]["gemini"]),
    }
    reviewer_claude = _pick("reviewer", "claude", rev_claude_key)
    reviewer_gemini = _pick("reviewer", "gemini", rev_gemini_key)

    norm_author = normalize_family(author_family) or "claude"
    primary_reviewer = reviewer_gemini if norm_author == "claude" else reviewer_claude
    fallback_reviewer = reviewer_claude if norm_author == "claude" else reviewer_gemini

    return {
        "tier": tier,
        "reviewer_tier": reviewer_tier,
        "estimated_minutes": TIER_ESTIMATED_MINUTES[tier],
        "author_family": norm_author,
        "round_num": int(round_num),
        "reasons": reasons,
        "router": router_pair,
        "planner": {
            **planner_pair,
            "recommended": planner_pair[norm_author],
            "inline_with_executor": tier == "trivial",
        },
        "executor": {
            **executor_pair,
            "recommended": executor_pair[norm_author],
        },
        "reviewer": {
            "tier": reviewer_tier,
            "claude": reviewer_claude,
            "gemini": reviewer_gemini,
            "primary": primary_reviewer,
            "same_family_fallback": fallback_reviewer,
        },
    }


def _git_clean_env() -> tuple[str, dict[str, str]]:
    git_bin = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else "git"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_PREFIX",
        )
    }
    return git_bin, env


def collect_git_diff_signals(repo: Path, diff_base: str | None) -> tuple[list[str], int, str, str]:
    git_bin, env = _git_clean_env()
    subject_proc = subprocess.run(
        [git_bin, "log", "-1", "--pretty=%s"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    subject = subject_proc.stdout.strip() if subject_proc.returncode == 0 else ""
    if not diff_base:
        return [], 0, "", subject

    candidates = [diff_base]
    if not diff_base.startswith("origin/"):
        candidates.append(f"origin/{diff_base}")

    for ref in candidates:
        names_p = subprocess.run(
            [git_bin, "diff", "--name-only", f"{ref}...HEAD"],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if names_p.returncode != 0:
            continue
        files = [ln.strip() for ln in names_p.stdout.splitlines() if ln.strip()]
        patch_p = subprocess.run(
            [git_bin, "diff", f"{ref}...HEAD"],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        patch = patch_p.stdout if patch_p.returncode == 0 else ""
        line_cnt, _ = _extract_changed_patch_lines(patch)
        return files, line_cnt, patch, subject
    sys.stderr.write(
        f"[paf select-model] warning: git base ref {diff_base!r} could not be resolved in {repo}; run `git fetch` first.\n"
    )
    return [], 0, "", subject


def _extract_routing_dict(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    mr = data.get("model_routing")
    if isinstance(mr, dict):
        return mr
    cons = data.get("consensus")
    if isinstance(cons, dict) and isinstance(cons.get("model_routing"), dict):
        return cons["model_routing"]
    return {}


def load_repo_routing_config(repo: Path | None, *, base_branch: str | None = None) -> dict[str, Any]:
    """Load `model_routing` config for `repo`.

    When `base_branch` is supplied (e.g. during PR review or `--diff-base` evaluation),
    reads `.agent-verify.json` strictly from the base git ref (`origin/<base>` or `<base>`)
    and never falls back to the unreviewed working tree, preventing a PR from modifying
    `.agent-verify.json` to weaken its own review routing.
    """
    if repo is None:
        return {}
    repo_resolved = repo.expanduser().resolve()
    clean_base = (base_branch or "").strip()
    if clean_base:
        if not (repo_resolved / ".git").exists():
            return {}
        git_bin, env = _git_clean_env()
        refs = (
            (clean_base, clean_base[len("origin/") :])
            if clean_base.startswith("origin/")
            else (f"origin/{clean_base}", clean_base)
        )
        for ref in refs:
            if not ref:
                continue
            rev_p = subprocess.run(
                [git_bin, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                cwd=repo_resolved,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if rev_p.returncode != 0:
                continue
            show_p = subprocess.run(
                [git_bin, "show", f"{ref}:.agent-verify.json"],
                cwd=repo_resolved,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if show_p.returncode != 0:
                return {}
            try:
                return _extract_routing_dict(json.loads(show_p.stdout))
            except json.JSONDecodeError:
                return {}
        return {}

    cfg_path = repo_resolved / ".agent-verify.json"
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _extract_routing_dict(data)


def select_pr_models(
    *,
    repo: Path | None,
    pr_data: dict[str, Any],
    diff_patch: str = "",
    author_family: str = "claude",
    round_num: int = 1,
) -> dict[str, Any]:
    files: list[str] = []
    for item in pr_data.get("files") or []:
        if isinstance(item, dict) and item.get("path"):
            files.append(str(item["path"]))
        elif isinstance(item, str) and item:
            files.append(item)
    title = str(pr_data.get("title") or "")
    raw_body = str(pr_data.get("body") or "")
    base_branch = str(pr_data.get("baseRefName") or "main").strip() or "main"
    # Strip embedded verification artifact & consensus HTML comment before keyword scan
    clean_body = re.sub(r"<!--\s*paf-consensus:.*?-->", "", raw_body, flags=re.DOTALL)
    clean_body = clean_body.split("### Verification Artifact")[0].strip()
    routing_cfg = load_repo_routing_config(repo, base_branch=base_branch)
    return select_task_models(
        title=title,
        description=clean_body,
        changed_files=files,
        diff_patch=diff_patch,
        round_num=round_num,
        author_family=author_family,
        routing_cfg=routing_cfg,
    )


def format_routing_plan(plan: dict[str, Any], role_filter: str | None = None) -> str:
    if role_filter:
        role_data = plan[role_filter]
        eff_tier = plan.get("reviewer_tier", plan["tier"]) if role_filter == "reviewer" else plan["tier"]
        if role_filter == "reviewer":
            target = role_data["primary"]
        elif "recommended" in role_data:
            target = role_data["recommended"]
        else:
            target = role_data[plan["author_family"]]
        return (
            f"[{role_filter.upper()}] tier={eff_tier} -> {target['display_name']} "
            f"({' '.join(target['cli_flags'])}) | TTFT {target['ttft_sec']} | reasons: {', '.join(plan['reasons'])}"
        )

    r_tr = plan["router"][plan["author_family"]]
    r_pl = plan["planner"]["recommended"]
    r_ex = plan["executor"]["recommended"]
    r_rv = plan["reviewer"]["primary"]
    inline_note = " (inline with executor)" if plan["planner"].get("inline_with_executor") else ""
    rev_tier = plan.get("reviewer_tier", plan["tier"])
    rev_tier_note = f", reviewer_tier={rev_tier}" if rev_tier != plan["tier"] else ""
    lines = [
        f"Model Routing Plan — tier: {plan['tier']}{rev_tier_note} (est. {plan['estimated_minutes']}, author_family={plan['author_family']}, round={plan['round_num']})",
        f"  Signals  : {', '.join(plan['reasons'])}",
        f"  Router   : {r_tr['display_name']} [{' '.join(r_tr['cli_flags'])}] (TTFT {r_tr['ttft_sec']})",
        f"  Planner  : {r_pl['display_name']} [{' '.join(r_pl['cli_flags'])}]{inline_note}",
        f"  Executor : {r_ex['display_name']} [{' '.join(r_ex['cli_flags'])}] (task {r_ex['task_minutes']})",
        f"  Reviewer : {r_rv['display_name']} [{' '.join(r_rv['cli_flags'])}] (heterogeneous peer)",
    ]
    return "\n".join(lines)


def populate_arg_parser(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--diff-base", default=None, help="Git base ref (e.g. main or origin/main) to inspect changed files & diff")
    p.add_argument("--title", default="", help="Ticket or PR title")
    p.add_argument("--body", default="", help="Ticket or PR description")
    p.add_argument("--file", dest="files", action="append", default=None, help="Changed file path (repeatable)")
    p.add_argument("--diff-lines", type=int, default=None, help="Explicit changed line count")
    p.add_argument("--story-points", type=float, default=None, help="Ticket story point estimate")
    p.add_argument("--round", dest="round_num", type=int, default=1, help="Consensus review round (1..N)")
    p.add_argument("--incident", action="store_true", help="Flag production incident / outage")
    p.add_argument("--author-family", default="claude", help="Primary/author model family (claude or gemini)")
    p.add_argument("--tier", choices=TIERS, default=None, help="Explicit complexity tier override")
    p.add_argument("--role", choices=("router", "planner", "executor", "reviewer"), default=None)
    p.add_argument("--json", action="store_true", help="Emit structured JSON routing plan")
    return p


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="model_router", description=__doc__)
    return populate_arg_parser(p)


def run_from_namespace(args: argparse.Namespace) -> int:
    repo = args.repo.expanduser().resolve()
    git_files: list[str] = []
    git_lines: int | None = None
    git_patch = ""
    git_subject = ""
    if args.diff_base and (repo / ".git").exists():
        git_files, git_lines, git_patch, git_subject = collect_git_diff_signals(repo, args.diff_base)

    merged_files = list(dict.fromkeys((args.files or []) + git_files))
    eff_title = args.title or git_subject
    eff_lines = args.diff_lines if args.diff_lines is not None else git_lines

    plan = select_task_models(
        title=eff_title,
        description=args.body,
        changed_files=merged_files,
        diff_lines=eff_lines,
        diff_patch=git_patch,
        story_points=args.story_points,
        round_num=args.round_num,
        is_incident=args.incident,
        author_family=args.author_family,
        explicit_tier=args.tier,
        routing_cfg=load_repo_routing_config(repo, base_branch=args.diff_base),
    )
    if args.json:
        out = plan[args.role] if args.role else plan
        print(json.dumps(out, indent=2))
    else:
        print(format_routing_plan(plan, role_filter=args.role))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_from_namespace(args)


if __name__ == "__main__":
    raise SystemExit(main())
