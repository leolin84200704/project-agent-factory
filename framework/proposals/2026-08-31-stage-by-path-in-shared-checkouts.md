# Stage by explicit path in any checkout you do not exclusively own

- **Date**: 2026-08-31
- **Source**: LIS instance, 2026-08-27 (dream-routed; single-domain evidence, so inbox not PR)

## Lesson

Never `git add -A` (or `git add .` / `commit -a`) in a working copy that another
actor — a human or another agent session — may be editing concurrently. Stage by
explicit file path, and before committing, diff `git status -s` against the list
of files you know you changed this task. Treat anomalies ("a commit I didn't
make", "my file changed under me") as evidence of a second actor, not as
harmless help. When true isolation is needed, use a separate worktree.

## Why not yet universal

One incident, one domain: a concurrent interactive session edited the same
checkout; a whole-tree `git add -A` swept the other actor's in-progress edit for
a different ticket into this session's commit, breaking the one-ticket-one-commit
boundary and requiring a branch restructure. Warning signs (foreign commit on
HEAD, spec file modified externally) were observed and misread as assistance.
Needs a second case from a different domain before an ENGINEERING-LESSONS PR.

## Target

ENGINEERING-LESSONS (git / workspace hygiene) if a cross-domain second case appears.
