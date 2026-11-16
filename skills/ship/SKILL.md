---
name: ship
description: Ship a verified feature branch through Cross-Model PR Consensus and the Two-Zone High-Trust loop (`paf ship` / `paf post-consensus` / `paf merge-consensus`): open PR, converge to heterogeneous model consensus, auto-merge with `--match-head-commit` upon consensus + green CI, verify post-merge health, and auto-revert on regression.
---

# High-Trust Ship & Cross-Model Consensus Loop (`paf`)

Use this skill after committing changes on a feature branch (never on `main`/`master`) with a clean working tree.

## Standing General Rule: Auto-Merge Upon Dual-Model Consensus

1. Every PR goes through **heterogeneous dual-model review** (`claude` <-> `gemini`) directly via SHA-pinned GitHub PR comments (`<!-- paf-consensus: {...} -->`).
2. **Once heterogeneous consensus (`verdict: "approve"` on the latest `headRefOid`) and GitHub Actions CI pass, merge immediately yourself — NEVER ask Leo to press merge** in `zone: "autonomous"` repos or on `zone: "guarded"` staging-class branches (`staging`, `stage_test`). If another PR landed on `main` first and created an adjacent conflict, rebuild cleanly onto `main`, re-verify tests + CI, and merge automatically.
3. Escalate to Leo ONLY when:
   - Consensus exceeds `max_rounds` (default `3`) without approval (`PAUSED_FOR_CONSENSUS_TIEBREAK`),
   - A product/UX preference trade-off requires his call (`--needs-human-decision`), or
   - `zone: "guarded"` targets company `main` (`PAUSED_FOR_MAIN_APPROVAL` — single `approve`, after which `--approved-main` completes the merge, verification, and self-heal/revert loop autonomously).

## Commands

1. **Author Model — Verify & Open/Update PR for Cross-Model Consensus**:
   ```bash
   paf ship --repo . --open-for-consensus --author-family <claude|gemini> \
     --title "[scope] Concise English summary" --body "Context and test evidence"
   ```
2. **Reviewer Model (Heterogeneous Family) — Post Verdict & Auto-Merge on Approve**:
   ```bash
   # Request changes during round 1..3:
   paf post-consensus --repo . --pr <num> --role reviewer --family <gemini|claude> \
     --verdict request_changes --round 1 --body-file review.md

   # Approve and immediately run CI wait + atomic --match-head-commit merge + post-merge verify:
   paf post-consensus --repo . --pr <num> --role reviewer --family <gemini|claude> \
     --verdict approve --round 2 --body-file approve.md --auto-merge
   ```
3. **Check or Resume Merge on an Already-Approved Consensus PR**:
   ```bash
   paf check-consensus --repo . --pr <num>
   paf merge-consensus --repo . --pr <num>                  # autonomous or guarded staging
   paf merge-consensus --repo . --pr <num> --approved-main  # guarded main after Leo's approve
   ```
