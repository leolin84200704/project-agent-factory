#!/usr/bin/env python3
"""
Private regression suite runner. Spec: framework/REGRESSION.md.

Runs each task in eval-tasks/ as a headless `claude -p` call with cwd at the
instance root (memory indexes + CLAUDE.md load normally), grades the final
output with the task's regexes, and records pass/fail + tokens + turns.

--ablation additionally runs every task in a git-worktree copy of the
instance with all memory tiers emptied (CLAUDE.md kept), so the delta is the
measured contribution of the memory system itself.

Usage:
  python3 scripts/run-regression.py                      # all tasks, once
  python3 scripts/run-regression.py --k 3                # pass^3
  python3 scripts/run-regression.py --ablation
  python3 scripts/run-regression.py --tasks id1,id2
  python3 scripts/run-regression.py --label monthly-2026-09 --diff monthly-2026-08
Env:
  REGRESSION_MODEL   model for all runs (default: opus)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "eval-tasks"
EVAL_OUT = ROOT / "eval-output"
EVAL_OUT.mkdir(exist_ok=True)

MODEL = os.environ.get("REGRESSION_MODEL", "opus")
MEMORY_DIRS = ["storage", "long-term-memory", "journal", "archive"]
ALLOWED_TOOLS = "Read,Glob,Grep"
RUN_WARN_THRESHOLD = 60


def load_tasks(subset: set[str] | None) -> list[dict]:
    tasks = []
    if not TASKS_DIR.is_dir():
        return tasks
    for f in sorted(TASKS_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        end = text.index("\n---", 3)
        meta: dict = {}
        try:
            import yaml  # type: ignore
            meta = yaml.safe_load(text[4:end]) or {}
        except ModuleNotFoundError:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    v = v.strip().strip("'\"")
                    if v.startswith("[") and v.endswith("]"):
                        meta[k.strip()] = [x.strip().strip("'\"")
                                           for x in v[1:-1].split(",") if x.strip()]
                    else:
                        meta[k.strip()] = v
        if meta.get("retired"):
            continue
        tid = meta.get("id", f.stem)
        if subset and tid not in subset:
            continue
        tasks.append({
            "id": tid,
            "prompt": text[end + 4:].strip(),
            "expect": meta.get("expect"),
            "expect_all": meta.get("expect_all") or [],
            "forbid": meta.get("forbid"),
            "type": meta.get("type", "recall"),
        })
    return tasks


def grade(task: dict, output: str) -> tuple[bool, str]:
    checks = []
    if task["expect"]:
        checks.append((task["expect"], True))
    for pat in task["expect_all"]:
        checks.append((pat, True))
    for pat, want in checks:
        if bool(re.search(str(pat), output, re.I | re.S)) is not want:
            return False, f"missing: {pat}"
    if task["forbid"] and re.search(str(task["forbid"]), output, re.I | re.S):
        return False, f"forbidden matched: {task['forbid']}"
    if not checks and not task["forbid"]:
        return False, "task has no grader"
    return True, "ok"


def run_claude(prompt: str, cwd: Path, timeout: int) -> dict:
    env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
    t0 = time.time()
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", MODEL,
             "--allowedTools", ALLOWED_TOOLS, "--output-format", "json"],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"output": "", "error": "timeout", "seconds": timeout}
    seconds = round(time.time() - t0, 1)
    if proc.returncode != 0:
        return {"output": proc.stdout or proc.stderr,
                "error": f"exit {proc.returncode}", "seconds": seconds}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"output": proc.stdout, "error": "bad json", "seconds": seconds}
    usage = payload.get("usage", {}) or {}
    tokens = sum(v for k, v in usage.items()
                 if isinstance(v, (int, float)) and "tokens" in k)
    return {
        "output": payload.get("result", "") or "",
        "tokens": int(tokens),
        "turns": payload.get("num_turns"),
        "cost_usd": payload.get("total_cost_usd"),
        "seconds": seconds,
        "error": None,
    }


def make_ablated_copy() -> tuple[Path, str]:
    """git worktree copy of the instance with memory tiers emptied."""
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        sys.exit("[ablation] instance repo is dirty — commit first so the "
                 "worktree copy is faithful")
    tmp = Path(tempfile.mkdtemp(prefix="regression-ablated-"))
    dest = tmp / "instance"
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach",
                    str(dest)], check=True, capture_output=True)
    for d in MEMORY_DIRS:
        target = dest / d
        if target.exists():
            shutil.rmtree(target)
        (target).mkdir(parents=True)
        sub = target / "short_term_memory" if d == "storage" else target
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "_index.md").write_text(
            "# Index\n\n(ablation arm — memory emptied)\n", encoding="utf-8")
    return dest, str(tmp)


def cleanup_ablated(dest: Path, tmp: str) -> None:
    subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force",
                    str(dest)], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)


def run_arm(tasks: list[dict], cwd: Path, k: int, timeout: int, arm: str) -> dict:
    rows = []
    for t in tasks:
        attempts = []
        for _ in range(k):
            r = run_claude(t["prompt"], cwd, timeout)
            ok, why = (False, r["error"]) if r.get("error") else grade(t, r["output"])
            attempts.append({"pass": ok, "why": why,
                             "tokens": r.get("tokens"), "turns": r.get("turns"),
                             "seconds": r.get("seconds")})
            print(f"  [{arm}] {t['id']}: {'PASS' if ok else 'FAIL — ' + str(why)}"
                  f" ({r.get('tokens', '?')} tok, {r.get('turns', '?')} turns,"
                  f" {r.get('seconds')}s)")
        rows.append({
            "id": t["id"], "type": t["type"],
            "pass_all_k": all(a["pass"] for a in attempts),
            "pass_any_k": any(a["pass"] for a in attempts),
            "attempts": attempts,
        })
    n = len(rows) or 1
    toks = [a["tokens"] for r in rows for a in r["attempts"] if a["tokens"]]
    turns = [a["turns"] for r in rows for a in r["attempts"] if a["turns"]]
    return {
        "tasks": rows,
        "n": len(rows),
        "pass_hat_k_pct": round(100 * sum(r["pass_all_k"] for r in rows) / n, 1),
        "pass_at_k_pct": round(100 * sum(r["pass_any_k"] for r in rows) / n, 1),
        "median_tokens": int(sorted(toks)[len(toks) // 2]) if toks else None,
        "median_turns": sorted(turns)[len(turns) // 2] if turns else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--tasks", help="comma-separated task ids")
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--label")
    ap.add_argument("--diff")
    args = ap.parse_args()

    subset = set(args.tasks.split(",")) if args.tasks else None
    tasks = load_tasks(subset)
    if not tasks:
        sys.exit(f"no tasks found in {TASKS_DIR}")
    total_runs = len(tasks) * args.k * (2 if args.ablation else 1)
    print(f"# Regression — {len(tasks)} tasks, k={args.k}, model={MODEL}"
          f"{', +ablation arm' if args.ablation else ''} ({total_runs} runs)")
    if total_runs > RUN_WARN_THRESHOLD:
        print(f"WARNING: {total_runs} runs exceeds {RUN_WARN_THRESHOLD}; "
              f"consider --tasks subset", file=sys.stderr)

    result: dict = {"date": date.today().isoformat(), "model": MODEL, "k": args.k}
    result["memory"] = run_arm(tasks, ROOT, args.k, args.timeout, "memory")

    if args.ablation:
        dest, tmp = make_ablated_copy()
        try:
            result["ablated"] = run_arm(tasks, dest, args.k, args.timeout, "ablated")
        finally:
            cleanup_ablated(dest, tmp)
        result["ablation_delta"] = {
            "pass_hat_k_delta": round(result["memory"]["pass_hat_k_pct"]
                                      - result["ablated"]["pass_hat_k_pct"], 1),
            "median_tokens_ratio": round(
                result["ablated"]["median_tokens"] / result["memory"]["median_tokens"], 2)
            if result["memory"]["median_tokens"] and result["ablated"]["median_tokens"]
            else None,
        }

    print()
    m = result["memory"]
    print(f"memory arm : pass^{args.k}={m['pass_hat_k_pct']}% pass@{args.k}="
          f"{m['pass_at_k_pct']}% median_tokens={m['median_tokens']} "
          f"median_turns={m['median_turns']}")
    if args.ablation:
        a = result["ablated"]
        print(f"ablated arm: pass^{args.k}={a['pass_hat_k_pct']}% pass@{args.k}="
              f"{a['pass_at_k_pct']}% median_tokens={a['median_tokens']} "
              f"median_turns={a['median_turns']}")
        print(f"delta      : pass {result['ablation_delta']['pass_hat_k_delta']:+}pp, "
              f"ablated/memory token ratio "
              f"{result['ablation_delta']['median_tokens_ratio']}")

    if args.label:
        out = EVAL_OUT / f"{result['date']}-regression-{args.label}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n→ Saved snapshot: {out}")

    if args.diff:
        matches = sorted(EVAL_OUT.glob(f"*-regression-{args.diff}.json"))
        if not matches:
            sys.exit(f"[diff] no snapshot matching '{args.diff}'")
        base = json.loads(matches[-1].read_text(encoding="utf-8"))
        print(f"\n## Diff vs {matches[-1].name}")
        for arm in ("memory", "ablated"):
            if arm in result and arm in base:
                for key in ("pass_hat_k_pct", "median_tokens", "median_turns"):
                    a, b = base[arm].get(key), result[arm].get(key)
                    if a != b and a is not None and b is not None:
                        print(f"  {arm}.{key}: {a} -> {b} ({b - a:+})")
        curr_fail = {r["id"] for r in result["memory"]["tasks"] if not r["pass_all_k"]}
        base_fail = {r["id"] for r in base["memory"]["tasks"] if not r["pass_all_k"]}
        for tid in sorted(curr_fail - base_fail):
            print(f"  REGRESSED: {tid}")
        for tid in sorted(base_fail - curr_fail):
            print(f"  fixed: {tid}")


if __name__ == "__main__":
    main()
