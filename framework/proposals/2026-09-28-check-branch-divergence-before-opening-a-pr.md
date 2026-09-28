# Proposal: a PR between branches that have diverged in both directions does not show your change

Status: inbox (unreviewed). Source: lis instance dream 2026-09-28.

## Lesson (candidate text)

Two long-lived branches can each be ahead of the other. A branch cut from one of
them and opened as a pull request against the other renders the entire
two-directional delta as "the change" — hundreds of files, conflicts, and your
actual edit buried inside. The diff is not wrong; the base is.

Before opening a pull request, count commits in both directions between the
intended base and your head (`git rev-list --count base..head` and
`head..base`). If the base is ahead of your head at all, either rebase or cut
the branch from the branch you intend to target. Cheapest of all: always cut
from the target. When a repository keeps two such lines deliberately, expect a
"one change, two branches, two PRs" convention and follow it.

Discipline: never open a pull request without knowing both counts.

## Why it is not promoted tonight

One instance, one domain, one investigator: a branch cut from `main` was opened
against a release-test branch that was 105 commits ahead of `main` while `main`
was 165 ahead of it; the PR rendered 137 files / +18,580 lines / conflicting for
a 5-file change. Fixed in one command by retargeting the base and opening a
sibling PR from the other line. Needs a second case from a different domain
(any repo with two long-lived lines: docs sites, infra, mobile release trains)
before an ENGINEERING-LESSONS PR.

## Target

ENGINEERING-LESSONS (git / PR hygiene) if a cross-domain second case appears.
