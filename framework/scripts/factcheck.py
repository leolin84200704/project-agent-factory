#!/usr/bin/env python3
"""Fact-check runner behind `paf factcheck` (framework/FACT-CHECK.md).

A claims file lists every fact a piece of outbound text or a DB write depends
on. This script runs each claim's command itself and writes a receipt. A claim
passes only when the command actually returned data; memory, notes, reasoning,
an empty result, or a failing command never count. The receipt is what the
`require-factcheck-receipt.sh` hook looks for before an outbound send or a DB
write.

Claims file (JSON):
{
  "target": "slack:C0B8YU8JZG8",          # what the text/write goes to; the hook
                                           # matches this string against the tool call
  "claims": [
    {"id": "c1", "text": "56180 is an NPI-type account, not The Genesis Center",
     "category": "D", "label": "verified",
     "command": "python3 q.py \"SELECT ... clinic_id=56180\"",
     "cross_check_command": "python3 q.py \"SELECT ... order_info ...\""},
    {"id": "c2", "text": "No OptiMantra orders ever used 56180",
     "category": "C", "kind": "negative", "label": "verified",
     "command": "...query that returns 0 rows...",
     "control_command": "...same query for a clinic known to have orders..."},
    {"id": "c3", "text": "Use 20600 for Genesis", "label": "inferred", "from": ["c1", "c2"]},
    {"id": "c4", "text": "Khurana used to practice at Genesis", "label": "unchecked"}
  ]
}

Rules enforced (each maps to a category in FACT-CHECK.md):
- verified claim: `command` must exit 0 and print non-empty output; an optional
  `expect_regex` must match the output.
- kind=negative (category C, "none / missing / broken / zero"): the command may
  return nothing, but `control_command` (same tool, same scope, a known positive)
  must exit 0 with non-empty output, proving the probe can see data at all.
- category D (identity, ownership, still-in-use): `cross_check_command`, an
  independent source such as orders, logs or account status, must also return data.
- category F (all / every / complete): `full_set_command` must return the full
  set's count or listing so the claim is checked against the whole set.
- inferred claim: `from` must list only ids of verified claims that passed.
- unchecked claim: allowed, listed in the receipt so the text says it was not checked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CATEGORIES = set("ABCDEFGHIJ")
LABELS = ("verified", "inferred", "unchecked")
DEFAULT_TIMEOUT = 120
OUTPUT_EXCERPT = 400


def receipts_dir() -> Path:
    override = os.environ.get("PAF_FACTCHECK_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "paf" / "factcheck"


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")
    return (s or "target")[:80]


def run_command(cmd: str, timeout: int, cwd: Path | None) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"timeout after {timeout}s"
    stripped = out.strip()
    return {
        "command": cmd,
        "ran_at": started.isoformat(timespec="seconds"),
        "exit_code": code,
        "output_lines": len(stripped.splitlines()) if stripped else 0,
        "output_sha256": hashlib.sha256(out.encode()).hexdigest(),
        "output_excerpt": stripped[:OUTPUT_EXCERPT],
        "stderr_excerpt": err.strip()[:OUTPUT_EXCERPT],
        "has_data": code == 0 and bool(stripped),
        "_stdout": out,
    }


def check_claim(claim: dict[str, Any], timeout: int, cwd: Path | None) -> dict[str, Any]:
    cid = str(claim.get("id") or "").strip()
    label = claim.get("label", "verified")
    category = str(claim.get("category", "")).upper()
    kind = claim.get("kind", "positive")
    result: dict[str, Any] = {
        "id": cid,
        "text": claim.get("text", ""),
        "label": label,
        "category": category,
        "kind": kind,
        "problems": [],
        "runs": {},
    }
    problems: list[str] = result["problems"]

    if not cid:
        problems.append("claim has no id")
    if label not in LABELS:
        problems.append(f"label must be one of {LABELS}")
        return result
    if category and category not in CATEGORIES:
        problems.append(f"unknown category {category!r} (expected A-J)")

    if label == "unchecked":
        return result
    if label == "inferred":
        if not claim.get("from"):
            problems.append("inferred claim must list the verified claim ids it comes from")
        return result

    cmd = claim.get("command")
    if not cmd:
        problems.append("verified claim needs a command; memory, notes and reasoning are not receipts")
        return result

    main = run_command(cmd, timeout, cwd)
    result["runs"]["command"] = main
    if main["exit_code"] != 0:
        problems.append(f"command exited {main['exit_code']}")

    if kind == "negative":
        control = claim.get("control_command")
        if not control:
            problems.append(
                "negative claim needs control_command: the same probe on a known positive must return data"
            )
        else:
            run = run_command(control, timeout, cwd)
            result["runs"]["control_command"] = run
            if not run["has_data"]:
                problems.append("control_command returned no data, so an empty result proves nothing")
    else:
        if not main["has_data"]:
            problems.append("command returned no data; an empty result is not evidence for a positive claim")
        pattern = claim.get("expect_regex")
        if pattern and main["has_data"] and not re.search(pattern, main["_stdout"], re.M):
            problems.append(f"output does not match expect_regex {pattern!r}")

    if category == "D":
        cross = claim.get("cross_check_command")
        if not cross:
            problems.append(
                "identity/ownership/in-use claim needs cross_check_command from an independent source (orders, logs, account status)"
            )
        else:
            run = run_command(cross, timeout, cwd)
            result["runs"]["cross_check_command"] = run
            if not run["has_data"]:
                problems.append("cross_check_command returned no data")

    if category == "F":
        full = claim.get("full_set_command")
        if not full:
            problems.append("all/every claim needs full_set_command covering the whole set, not a sample")
        else:
            run = run_command(full, timeout, cwd)
            result["runs"]["full_set_command"] = run
            if not run["has_data"]:
                problems.append("full_set_command returned no data")

    return result


def run(claims_path: Path, timeout: int = DEFAULT_TIMEOUT, out_dir: Path | None = None) -> tuple[int, dict[str, Any]]:
    spec = json.loads(claims_path.read_text())
    target = str(spec.get("target") or "").strip()
    claims = spec.get("claims") or []
    cwd = claims_path.parent

    results = [check_claim(c, timeout, cwd) for c in claims]
    passed_verified = {r["id"] for r in results if r["label"] == "verified" and not r["problems"]}
    for r, c in zip(results, claims):
        if r["label"] == "inferred":
            missing = [x for x in (c.get("from") or []) if x not in passed_verified]
            if missing:
                r["problems"].append(f"inferred from claims that are not passing verified claims: {missing}")

    global_problems = []
    if not target:
        global_problems.append("claims file needs a target (what the text or write goes to)")
    if not claims:
        global_problems.append("claims file lists no claims")

    for r in results:
        for run_result in r["runs"].values():
            run_result.pop("_stdout", None)

    ok = not global_problems and all(not r["problems"] for r in results)
    receipt = {
        "version": 1,
        "target": target,
        "claims_file": str(claims_path.resolve()),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "status": "pass" if ok else "fail",
        "problems": global_problems,
        "unchecked": [r["id"] for r in results if r["label"] == "unchecked"],
        "inferred": [r["id"] for r in results if r["label"] == "inferred"],
        "claims": results,
    }
    out_dir = out_dir or receipts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}-{slug(target)}.json"
    path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    receipt["receipt_path"] = str(path)
    return (0 if ok else 1), receipt


def print_summary(receipt: dict[str, Any]) -> None:
    print(f"fact-check {receipt['status'].upper()} target={receipt['target']}")
    for p in receipt["problems"]:
        print(f"  ! {p}")
    for r in receipt["claims"]:
        mark = "ok  " if not r["problems"] else "FAIL"
        print(f"  [{mark}] {r['id']} ({r['label']}{', ' + r['category'] if r['category'] else ''}) {r['text'][:90]}")
        for p in r["problems"]:
            print(f"         - {p}")
    if receipt["inferred"]:
        print(f"  inferred (write them as inferences, naming the source): {', '.join(receipt['inferred'])}")
    if receipt["unchecked"]:
        print(f"  unchecked (say so in the text; never state as fact): {', '.join(receipt['unchecked'])}")
    print(f"receipt: {receipt['receipt_path']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="paf factcheck", description=__doc__.split("\n\n")[0])
    ap.add_argument("claims", type=Path, help="claims JSON file")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per command")
    ap.add_argument("--out-dir", type=Path, default=None, help="receipt directory (default ~/.cache/paf/factcheck)")
    ap.add_argument("--json", action="store_true", help="print the receipt as JSON")
    args = ap.parse_args(argv)
    code, receipt = run(args.claims, args.timeout, args.out_dir)
    if args.json:
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
    else:
        print_summary(receipt)
    return code


if __name__ == "__main__":
    sys.exit(main())
