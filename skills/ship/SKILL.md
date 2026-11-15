---
name: ship
description: Ship a verified feature branch through the Two-Zone High-Trust loop (`paf ship`): push, open PR with Verification Artifact, pass GitHub Actions CI, squash-merge with `--match-head-commit`, verify post-merge health, and auto-revert on regression.
---

# High-Trust Ship & Post-Merge Loop (`paf ship`)

Use this skill after committing changes on a feature branch (never on `main`/`master`) with a clean working tree.

## Trust Zones (`.agent-verify.json`)

1. **`zone: "autonomous"` (Personal Repositories)**
   - **Zero human calls required for merging to `main`.**
   - Prerequisites:
     1. Clean working tree (`git status --porcelain` empty; stage files by explicit path, never `git add -A`).
     2. Passing `paf verify --repo . --mode pre-push`.
     3. Passing Blind Adversarial Review receipt (`paf record-review --verdict approve`) pinned to `HEAD`.
   - Command:
     ```bash
     paf ship --repo . --title "[scope] Concise English summary" --body "Context and test evidence"
     ```
   - Lifecycle executed automatically:
     1. Pushes feature branch (honoring `pre-push` hook without duplicate test execution on the already-verified SHA).
     2. Creates or updates the PR with the SHA-pinned Verification Artifact and Blind Review receipt.
     3. Waits for GitHub Actions CI checks to turn green; on CI failure, captures `gh run view --log-failed` into `<git-common-dir>/paf-ci-failure-<sha>.log` so you can debug, open a clean commit/PR, and re-verify.
     4. Executes atomic `gh pr merge --squash --delete-branch --match-head-commit <HEAD_SHA>`.
     5. Syncs `main` and runs `paf verify --mode post-merge`.
     6. If `post-merge` fails, automatically creates and merges a `revert/auto-<sha>` PR to restore `main` health immediately.

2. **`zone: "guarded"` (Company / Team Repositories)**
   - Staging/test branches ship autonomously.
   - Targeting `main` runs Steps 1–2 above and pauses cleanly with `PAUSED_FOR_MAIN_APPROVAL`, returning the PR URL and verified SHA for Leo's single `approve`.
   - Once Leo replies `approve`, immediately resume without further prompts:
     ```bash
     paf ship --repo . --base main --approved-main
     ```
   - `paf ship --approved-main` completes CI check verification, atomic `--match-head-commit` squash-merge, post-deploy verification, and automatic debug/fix or auto-revert to closure.
