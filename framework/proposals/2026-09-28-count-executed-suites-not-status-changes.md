# Proposal: a before/after test comparison proves nothing about tests that could not run

Status: inbox (unreviewed). Source: lis instance dream 2026-09-28.

## Lesson (candidate text)

"The same tests fail before and after my change" is only evidence if the tests
actually executed in both states. A missing dependency, a broken import, or an
environment mismatch can silently prevent whole suites from running; the
before/after failure sets then match because both sides are equally blind, not
because nothing regressed.

When using a baseline comparison as regression evidence, report the number of
suites and assertions that executed on each side, not only which ones changed
status. Require the "after" count to be equal or higher. If suites are skipped,
fix the environment first (install the dependency at the locked version without
touching manifests) and rerun — the comparison before that point is void.

Discipline: a regression claim names the executed count, not just the diff of
failures.

## Why it is not promoted tonight

One instance, one domain: an uninstalled package kept seven suites from running
locally; the "identical failure sets" claim was true and worthless. After
installing it, one of the previously-unrun suites turned out to be broken by
the change. The claim had already been made. Needs a second case from a
different domain before an ENGINEERING-LESSONS PR.

## Target

ENGINEERING-LESSONS (testing / verification) if a cross-domain second case appears.
