---
name: ship
description: Ship a verified feature branch through Cross-Model PR Consensus and the Two-Zone High-Trust loop (`paf ship` / `paf post-consensus` / `paf merge-consensus`): open PR, converge to heterogeneous model consensus, auto-merge with `--match-head-commit` upon consensus + green CI, verify post-merge health, and auto-revert on regression.
---

# High-Trust Ship & Cross-Model Consensus Loop (`paf`)

Use this skill after committing changes on a feature branch (never on `main`/`master`) with a clean working tree.

## Standing General Rule: Auto-Merge Upon Dual-Model Consensus

1. Every PR goes through **heterogeneous dual-model review** (`claude` <-> `gemini`) directly via SHA-pinned GitHub PR comments (`<!-- paf-consensus: {...} -->`).
2. **Once heterogeneous consensus (`verdict: "approve"` on the latest `headRefOid`) and GitHub Actions CI pass, merge immediately yourself — NEVER ask Leo to press merge** for normal code PRs in `zone: "autonomous"` repos or on `zone: "guarded"` branches explicitly allowlisted in `staging_branches` (`staging`, `stage_test`).
   - If a sibling PR landed on `main` first and `paf merge-consensus` fails closed on a base conflict, rebuild the branch onto `origin/main` and re-verify tests + CI; because the rebuilt branch has a new `headRefOid`, obtain a fresh SHA-pinned `approve` on the new SHA (e.g. via `git range-diff`) before merging.
3. Escalate / pause for Leo ONLY when:
   - Consensus exceeds `max_rounds` (default `3`) without approval (`PAUSED_FOR_CONSENSUS_TIEBREAK`),
   - A product/UX preference trade-off requires his call (`--needs-human-decision`),
   - A PR modifies governance or permission-granting files (`CORE.md`, `AGENTS.md`, `CLAUDE.md`, `PERMISSIONS.md`, `FEEDBACK.md`, `.agent-verify.json`, `bin/paf`, hooks, workflows, `launchd/`, `DailyJob/`, skills) -> pauses at `PAUSED_FOR_GOVERNANCE_APPROVAL` until `--approved-governance`, or
   - `zone: "guarded"` targets any branch outside `staging_branches` (`main`, `master`, `release/*`) -> pauses at `PAUSED_FOR_MAIN_APPROVAL` until `--approved-main` (and always yields to required human colleague reviews and any stricter workspace `CLAUDE.md` rule).

## Trust Boundaries & Marker Semantics

- **Write-time & read-time SHA pinning**: `--head-sha <sha>` is mandatory when `--role reviewer`. `paf post-consensus` refuses to post if the PR's `headRefOid` moved while the reviewer was reading, and `evaluate_pr_consensus` ignores markers pinned to stale SHAs or missing a recognized `author` / `reviewer` `model_family` (`claude`, `gemini`, `openai`). `.agent-verify.json` must explicitly declare `"zone": "autonomous" | "guarded"` (missing `zone` fails closed).
- **Single-account operator model**: Because both machines authenticate to GitHub under Leo's single owner account, `model_family` is self-declared in the `<!-- paf-consensus: ... -->` marker rather than bound to distinct GitHub usernames. An agent must NEVER post a marker claiming a `model_family` other than its own. Sessions of one family share one label, so `paf` cannot tell them apart: the session that wrote a PR's code must NEVER post a `--role reviewer` marker on that PR, because its `approve` would clear an objection raised by another session of the same family.
- **Human colleague reviews & workspace rules**: Where GitHub branch protection enforces reviews, `reviewDecision: REVIEW_REQUIRED` or `CHANGES_REQUESTED` blocks `paf`; everywhere else (and whenever stricter), workspace `CLAUDE.md` rules govern. Once a PR carries any `paf-consensus` marker, consensus is the sole gate (`paf record-review` local receipts apply only to PRs never opened for consensus), and while only a different family can `approve`, any active-SHA `request_changes` or `needs_human_decision` blocks regardless of family.
- **Portable `post_merge_check` vs host-bound actions**: `post_merge_check` runs on whichever machine executes the merge and must contain only portable checks. Host-bound steps (daemon reloads like `launchd/install.sh`, VPN-only deploys/probes) stay out of `post_merge_check` and run on the hosting machine after `git pull`.
- **Marker source**: `paf` reads `<!-- paf-consensus: ... -->` markers from the PR body and issue comments (`gh pr view --json body,comments`). Clicking GitHub's native Pull Request Review "Approve" button does not satisfy `paf` consensus.

## Commands

1. **Author Model — Verify & Open/Update PR for Cross-Model Consensus**:
   ```bash
   paf ship --repo . --open-for-consensus --author-family <claude|gemini> \
     --title "[scope] Concise English summary" --body "Context and test evidence"
   ```
2. **Reviewer Model (Heterogeneous Family) — Post SHA-Pinned Verdict & Auto-Merge on Approve**:
   ```bash
   # Request changes during round 1..3 (--head-sha is required for reviewers):
   paf post-consensus --repo . --pr <num> --role reviewer --family <gemini|claude> \
     --head-sha <reviewed_sha> --verdict request_changes --round 1 --body-file review.md

   # Approve and immediately run CI wait + atomic --match-head-commit merge + post-merge verify:
   paf post-consensus --repo . --pr <num> --role reviewer --family <gemini|claude> \
     --head-sha <reviewed_sha> --verdict approve --round 2 --body-file approve.md --auto-merge
   ```
3. **Check or Resume Merge on an Already-Approved Consensus PR**:
   ```bash
   paf check-consensus --repo . --pr <num>
   paf merge-consensus --repo . --pr <num>                        # autonomous or guarded staging
   paf merge-consensus --repo . --pr <num> --approved-governance  # governance/rule PR after Leo's ok
   paf merge-consensus --repo . --pr <num> --approved-main        # guarded non-staging after Leo's approve
   ```
