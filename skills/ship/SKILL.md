---
name: ship
description: Ship a verified feature branch through Cross-Model PR Consensus and the Two-Zone High-Trust loop (`paf ship` / `paf post-consensus` / `paf merge-consensus`): open PR, converge to heterogeneous model consensus, auto-merge with `--match-head-commit` upon consensus + green CI, verify post-merge health, and auto-revert on regression.
---

# High-Trust Ship & Cross-Model Consensus Loop (`paf`)

Use this skill after committing changes on a feature branch (never on `main`/`master`) with a clean working tree.

## Standing General Rule: End-to-End Persistent-Session Consensus, Merge, Teardown & Post-Deploy Test

1. Every PR runs the **five-step synchronous consensus pipeline** (`paf ship --consensus` / `paf drive-consensus`):
   - **Open PR** after passing `paf verify --mode pre-push`.
   - **Call the opposite model family (`claude` <-> `gemini`) and discuss back and forth**, keeping each of the two models in its **same persistent session** across all rounds (`--session-id` / `--resume` for `claude`, `--conversation` for `agy`).
   - **Merge immediately upon SHA-pinned `approve` consensus + green CI** (`gh pr merge --squash --match-head-commit <SHA>`) — **NEVER ask Leo to confirm governance files or press merge** in `zone: "autonomous"` repos or on `zone: "guarded"` `staging_branches` (`staging`, `stage_test`). Governance, rule, `bin/paf`, hook, and skill changes are confirmed directly by the heterogeneous peer model.
   - **Close and clean up both model sessions** immediately upon merge (removing temporary session transcripts and PR worktrees).
   - **Run `post_merge_check`, and if `deploy` (`commands.deploy` or `deploy_workflows`) is configured, execute `deploy` and then test again (`post_deploy_check`)**, auto-reverting on regression.
2. If a sibling PR landed on `main` first and `paf merge-consensus` fails closed on a base conflict, rebuild the branch onto `origin/main` and re-verify tests + CI; because the rebuilt branch has a new `headRefOid`, obtain a fresh SHA-pinned `approve` on the new SHA before merging.
3. Escalate / pause for Leo ONLY when:
   - Consensus exceeds `max_rounds` without approval (`PAUSED_FOR_CONSENSUS_TIEBREAK`),
   - A genuine product/UX preference trade-off requires his call (`--needs-human-decision`), or
   - `zone: "guarded"` targets any branch outside `staging_branches` (`main`, `master`, `release/*`) -> pauses at `PAUSED_FOR_MAIN_APPROVAL` until `--approved-main` (and always yields to required human colleague reviews and any stricter workspace `CLAUDE.md` rule).

## Trust Boundaries & Marker Semantics

- **Write-time & read-time SHA pinning**: `--head-sha <sha>` is mandatory when `--role reviewer`. `paf post-consensus` refuses to post if the PR's `headRefOid` moved while the reviewer was reading, and `evaluate_pr_consensus` ignores markers pinned to stale SHAs or missing a recognized `author` / `reviewer` `model_family` (`claude`, `gemini`, `openai`). `.agent-verify.json` must explicitly declare `"zone": "autonomous" | "guarded"` (missing `zone` fails closed).
- **Single-account operator model**: Because both machines authenticate to GitHub under Leo's single owner account, `model_family` is self-declared in the `<!-- paf-consensus: ... -->` marker rather than bound to distinct GitHub usernames. An agent must NEVER post a marker claiming a `model_family` other than its own. Sessions of one family share one label, so `paf` cannot tell them apart: the session that wrote a PR's code must NEVER post a `--role reviewer` marker on that PR, because its `approve` would clear an objection raised by another session of the same family.
- **Human colleague reviews & workspace rules**: Where GitHub branch protection enforces reviews, `reviewDecision: REVIEW_REQUIRED` or `CHANGES_REQUESTED` blocks `paf`; everywhere else (and whenever stricter), workspace `CLAUDE.md` rules govern. Once a PR carries any `paf-consensus` marker, consensus is the sole gate (`paf record-review` local receipts apply only to PRs never opened for consensus), and while only a different family can `approve`, any active-SHA `request_changes` or `needs_human_decision` blocks regardless of family.
- **Portable `post_merge_check` vs host-bound actions**: `post_merge_check` runs on whichever machine executes the merge and must contain only portable checks. Host-bound steps (daemon reloads like `launchd/install.sh`, VPN-only deploys/probes) stay out of `post_merge_check` and run on the hosting machine after `git pull`.
- **Marker source**: `paf` reads `<!-- paf-consensus: ... -->` markers from the PR body and issue comments (`gh pr view --json body,comments`). Clicking GitHub's native Pull Request Review "Approve" button does not satisfy `paf` consensus.

## Commands

1. **Primary End-to-End Pipeline (Verify -> Open PR -> Persistent 2-Session Discussion -> Merge -> Close Both Sessions -> Deploy & Post-Deploy Test)**:
   ```bash
   paf ship --repo . --consensus --author-family <claude|gemini> \
     --title "[scope] Concise English summary" --body "Context and test evidence"
   ```
2. **Drive Persistent-Session Consensus Loop on an Existing Open PR**:
   ```bash
   paf drive-consensus --repo . --pr <num> --author-family <claude|gemini>
   ```
3. **Check or Resume Merge on an Already-Approved Consensus PR**:
   ```bash
   paf check-consensus --repo . --pr <num>
   paf merge-consensus --repo . --pr <num>                  # autonomous or guarded staging
   paf merge-consensus --repo . --pr <num> --approved-main  # guarded non-staging after Leo's approve
   ```
