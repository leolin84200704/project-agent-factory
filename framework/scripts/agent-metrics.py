#!/usr/bin/env python3
"""
Longitudinal agent-behavior metrics from Claude Code session transcripts.

Answers "is the agent needing me less over time?" with signals that are immune
to LLM-judge bias and self-perception (METR 2025: devs self-estimated +20%
while actually -19%):

  - interventions/session  — user text prompts after the first one
  - interrupts/session     — "[Request interrupted by user]" markers
  - longest autonomous streak — max consecutive tool calls between user prompts
  - tool calls per user prompt
  - auto-approve share     — share of prompts sent under bypassPermissions/acceptEdits

Optionally (--repo) git outcome metrics for a target repo:
  - agent-authored commits (Co-Authored-By: Claude) in the window
  - line survival: % of lines added by agent commits (>7d old) still present
    at HEAD (git blame) — GitClear-style churn signal
  - reverts touching agent commits

Sessions are read from ~/.claude/projects/{slug}/*.jsonl for every path in
WATCH_PATHS (ADAPT PER INSTANCE). Sidechain (subagent) lines are excluded.

Usage:
  python3 scripts/agent-metrics.py                      # last 90 days, stdout
  python3 scripts/agent-metrics.py --days 30
  python3 scripts/agent-metrics.py --label weekly-2026-08-15   # save snapshot
  python3 scripts/agent-metrics.py --diff weekly-2026-08-08    # compare
  python3 scripts/agent-metrics.py --repo ~/personal_project/industry-intel
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_OUT = ROOT / "eval-output"
EVAL_OUT.mkdir(exist_ok=True)
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# ── ADAPT PER INSTANCE ───────────────────────────────────────────
# Absolute paths whose Claude Code sessions belong to this agent's work:
# the instance repo itself plus its workspace/work repos.
WATCH_PATHS = [ROOT]
# Git repos for outcome metrics when --repo is not given (may be empty).
DEFAULT_REPOS: list[Path] = []
# ─────────────────────────────────────────────────────────────────

AUTO_MODES = {"bypassPermissions", "acceptEdits"}
INTERRUPT_RE = re.compile(r"Request interrupted by user")
# Harness-injected user-role lines that are not real human prompts.
NOISE_RE = re.compile(
    r"^\s*<(?:command-name|command-message|local-command|task-notification|system-reminder)"
)


def slugify(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(path))


def user_text(obj: dict) -> str:
    content = obj.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def is_tool_result_only(obj: dict) -> bool:
    content = obj.get("message", {}).get("content")
    if not isinstance(content, list):
        return False
    types = {b.get("type") for b in content if isinstance(b, dict)}
    return types <= {"tool_result"} and bool(types)


def parse_session(path: Path, since: datetime) -> dict | None:
    prompts = 0
    interrupts = 0
    tool_calls = 0
    auto_prompts = 0
    streak = 0
    longest_streak = 0
    first_ts = None
    last_ts = None

    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t not in ("user", "assistant") or obj.get("isSidechain"):
                continue
            # Headless/SDK sessions (claude -p pipelines, dream runs) are not
            # interactive work — excluding them keeps intervention metrics honest.
            if str(obj.get("entrypoint", "")).startswith("sdk"):
                return None
            ts = obj.get("timestamp")
            when = None
            if isinstance(ts, str):
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    when = None
            if when:
                first_ts = first_ts or when
                last_ts = when
            if t == "user":
                if is_tool_result_only(obj):
                    continue
                text = user_text(obj)
                if not text.strip() or NOISE_RE.match(text):
                    continue
                if INTERRUPT_RE.search(text):
                    interrupts += 1
                    continue
                prompts += 1
                if obj.get("permissionMode") in AUTO_MODES:
                    auto_prompts += 1
                streak = 0
            else:
                content = obj.get("message", {}).get("content")
                if isinstance(content, list):
                    n = sum(1 for b in content
                            if isinstance(b, dict) and b.get("type") == "tool_use")
                    tool_calls += n
                    streak += n
                    longest_streak = max(longest_streak, streak)

    if prompts == 0:
        return None
    if last_ts and last_ts < since:
        return None
    return {
        "prompts": prompts,
        "interventions": prompts - 1,
        "interrupts": interrupts,
        "tool_calls": tool_calls,
        "longest_streak": longest_streak,
        "auto_prompts": auto_prompts,
        "start": first_ts.isoformat() if first_ts else None,
        "minutes": round((last_ts - first_ts).total_seconds() / 60, 1)
        if first_ts and last_ts else None,
    }


def collect_sessions(days: int) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = []
    for wp in WATCH_PATHS:
        proj_dir = CLAUDE_PROJECTS / slugify(Path(wp).resolve())
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            s = parse_session(f, since)
            if s:
                s["project"] = Path(wp).name
                sessions.append(s)

    result: dict = {"window_days": days, "sessions": len(sessions)}
    if not sessions:
        return result

    def med(key):
        vals = [s[key] for s in sessions if s.get(key) is not None]
        return round(statistics.median(vals), 2) if vals else None

    total_prompts = sum(s["prompts"] for s in sessions)
    result.update({
        "total_prompts": total_prompts,
        "total_interventions": sum(s["interventions"] for s in sessions),
        "total_interrupts": sum(s["interrupts"] for s in sessions),
        "total_tool_calls": sum(s["tool_calls"] for s in sessions),
        "interventions_per_session_median": med("interventions"),
        "interrupts_per_session_median": med("interrupts"),
        "longest_streak_median": med("longest_streak"),
        "longest_streak_max": max(s["longest_streak"] for s in sessions),
        "tool_calls_per_prompt": round(
            sum(s["tool_calls"] for s in sessions) / max(total_prompts, 1), 2),
        "auto_approve_share_pct": round(
            100 * sum(s["auto_prompts"] for s in sessions) / max(total_prompts, 1), 1),
        "by_project": {},
    })
    for s in sessions:
        p = result["by_project"].setdefault(
            s["project"], {"sessions": 0, "interventions": 0, "interrupts": 0})
        p["sessions"] += 1
        p["interventions"] += s["interventions"]
        p["interrupts"] += s["interrupts"]
    return result


def _git(repo: Path, *args) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, timeout=120)
    return out.stdout if out.returncode == 0 else ""


def collect_git(repo: Path, days: int) -> dict:
    since = f"--since={days} days ago"
    log = _git(repo, "log", since, "--pretty=%H%x09%ci%x09%s",
               "--grep=Co-Authored-By: Claude", "-i", "--all")
    commits = [l.split("\t") for l in log.strip().splitlines() if l.strip()]
    reverts = _git(repo, "log", since, "--oneline", "--grep=^Revert", "--all")
    n_reverts = len([l for l in reverts.strip().splitlines() if l.strip()])

    # Line survival: agent commits older than 7 days; count their added lines
    # still attributed to them at HEAD.
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    added_total = 0
    surviving_total = 0
    sampled = 0
    for sha, ci, _subject in commits:
        try:
            when = datetime.fromisoformat(ci.strip().replace(" +", "+").replace(" -", "-"))
        except ValueError:
            continue
        if when.astimezone(timezone.utc) > cutoff:
            continue
        if sampled >= 20:
            break
        sampled += 1
        numstat = _git(repo, "show", "--numstat", "--pretty=format:", sha)
        files = []
        for row in numstat.strip().splitlines():
            parts = row.split("\t")
            if len(parts) == 3 and parts[0].isdigit():
                added_total += int(parts[0])
                files.append(parts[2])
        for f in files[:20]:
            blame = _git(repo, "blame", "-w", "--line-porcelain", "HEAD", "--", f)
            surviving_total += sum(1 for l in blame.splitlines()
                                   if l.startswith(sha[:40]))
    return {
        "repo": repo.name,
        "agent_commits": len(commits),
        "reverts_in_window": n_reverts,
        "survival_sampled_commits": sampled,
        "lines_added_sampled": added_total,
        "lines_surviving": surviving_total,
        "line_survival_pct": round(100 * surviving_total / added_total, 1)
        if added_total else None,
    }


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = obj
    return out


def diff(curr: dict, baseline: dict) -> list[str]:
    cf, bf = _flatten(curr), _flatten(baseline)
    lines = []
    for k in sorted(set(cf) | set(bf)):
        a, b = bf.get(k), cf.get(k)
        if a == b:
            continue
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            d = round(b - a, 2)
            lines.append(f"  {k}: {a} -> {b} ({'+' if d >= 0 else ''}{d})")
        else:
            lines.append(f"  {k}: {a} -> {b}")
    return lines


def print_report(r: dict) -> None:
    print(f"# Agent Metrics — {r['date']} (window {r['sessions_data']['window_days']}d)")
    s = r["sessions_data"]
    print()
    print(f"- Sessions: {s.get('sessions', 0)}")
    if s.get("sessions"):
        print(f"- Interventions/session (median): {s['interventions_per_session_median']}"
              f"  | total {s['total_interventions']} over {s['total_prompts']} prompts")
        print(f"- Interrupts/session (median): {s['interrupts_per_session_median']}"
              f"  | total {s['total_interrupts']}")
        print(f"- Longest autonomous streak: median {s['longest_streak_median']}"
              f", max {s['longest_streak_max']} tool calls")
        print(f"- Tool calls per prompt: {s['tool_calls_per_prompt']}")
        print(f"- Auto-approve share: {s['auto_approve_share_pct']}%")
        for name, p in s.get("by_project", {}).items():
            print(f"  - {name}: {p['sessions']} sessions, "
                  f"{p['interventions']} interventions, {p['interrupts']} interrupts")
    for g in r.get("git", []):
        print(f"- Repo {g['repo']}: {g['agent_commits']} agent commits, "
              f"{g['reverts_in_window']} reverts, "
              f"line survival {g['line_survival_pct']}% "
              f"({g['lines_surviving']}/{g['lines_added_sampled']} lines, "
              f"{g['survival_sampled_commits']} commits sampled)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--label")
    ap.add_argument("--diff")
    ap.add_argument("--repo", action="append", type=Path, default=None)
    args = ap.parse_args()

    result = {
        "date": date.today().isoformat(),
        "sessions_data": collect_sessions(args.days),
        "git": [collect_git(r.expanduser().resolve(), args.days)
                for r in (args.repo or DEFAULT_REPOS)
                if (r.expanduser() / ".git").exists()],
    }
    print_report(result)

    if args.label:
        out = EVAL_OUT / f"{result['date']}-agent-metrics-{args.label}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n→ Saved snapshot: {out}")

    if args.diff:
        target = Path(args.diff)
        if not target.exists():
            matches = sorted(EVAL_OUT.glob(f"*-agent-metrics-{args.diff}.json"))
            if not matches:
                print(f"\n[diff] No snapshot matching '{args.diff}'", file=sys.stderr)
                sys.exit(2)
            target = matches[-1]
        baseline = json.loads(target.read_text(encoding="utf-8"))
        print(f"\n## Diff vs {target.name}")
        lines = diff(result, baseline)
        print("\n".join(lines) if lines else "- (no changes)")


if __name__ == "__main__":
    main()
