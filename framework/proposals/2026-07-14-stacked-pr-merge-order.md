# Proposal: Stacked PRs — merge child-first, and verify the target branch after every merge round

- **Date**: 2026-07-14
- **Source**: LIS instance dream pipeline (journal 2026-07-14, VP-17408 arc)
- **Status**: unreviewed inbox item (fails cross-domain-evidence bar: 1 incident, 1 domain)

## Lesson (de-identified)

When a child PR is based on a parent feature branch and the parent merges first,
GitHub does NOT retarget the open child PR unless the parent branch is deleted.
Merging the child afterwards lands its diff into the (already-merged, now-dead)
feature branch — the PR shows MERGED while the target branch never received the
changes.

Discipline:
1. Stacked PRs merge child-first; or delete the parent branch on merge (forces
   retarget); or manually retarget the child before merging.
2. After every merge round, verify with `git log <target-branch>` that the
   commits actually arrived. "PR state = MERGED" is not "code is on the target
   branch".

## Why not promoted to ENGINEERING-LESSONS.md yet

Single incident, single domain. Text is fully standalone and prescribes a way of
working, but the ">=2 cases from different domains" bar is not met. Promote if a
second, unrelated-domain incident occurs.

## Evidence (identifying details)

Vibrant LIS repo lis-backend-emr-v2: PR #259 (base = feature branch of #258)
merged 2 minutes after #258 (base staging); staging silently lacked three fixes
(OBR-16 name order, customMappings write, paymentMethodId). Caught by
re-verifying `git log origin/staging`; rescued via PR #261.
