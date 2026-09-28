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

## Second case (2026-09-25, same instance — still single-domain)

A different mechanism, same command: an `npm install` of one missing package
re-ran a code generator whose output tree is committed to the repository. The
following `git add -A` during an amend swept 12 regenerated files, including
three 100 MB+ binaries, into the pull request. No gate caught it: the pre-push
hooks check build and dependency injection, not diff contents. It was found only
when a reviewer asked "is this right?" and the author re-read `git diff --stat`.

Sharpened lesson text: **whole-tree staging is unsafe whenever anything other
than you writes into the working copy — another actor, or a build step.**
Stage by name, and read the full `git status` immediately before committing,
especially after any install or build. Mechanisable: a pre-commit hook that
refuses staged paths under known generated directories.

Still one instance and one engineering domain, so it stays in the inbox.
