# Proposal: a green deploy run proves the job finished, not that your artifact is the one running

- Source: lis instance, LBS-1842 / VP-18889 (two pull requests merged to the same staging branch seconds apart; both deploy runs finished green in the same second, and the older image was applied last, so the pod that came up had the first change and not the second)
- Date: 2026-10-17
- Status: inbox (fails the cross-domain bar: one employer-domain case)

## Lesson (de-identified)

A deployment pipeline's success status describes the run, not the target. When two runs deploy to the same target and overlap, the one that finishes last wins regardless of which commit it carries, so a green check on the newer run can coexist with the older artifact being live. After any merge that triggers a deploy, read the version actually running on the target (image tag, build id, commit hash in a health endpoint) and compare it with the commit you merged. If they differ, re-run only the deploy step for the intended commit; do not re-run the whole pipeline and do not push an empty commit to "trigger" it.

## Why it is not yet universal

One case from one domain. The failure mode applies to any push-to-deploy setup without serialisation (GitHub Actions, Jenkins, GitLab CI, Argo without sync waves), and the same discipline already appears in this instance for config changes (read the live value back, not the apply's exit code), but the bar is two cases from different domains.

## Example

Two merges to a staging branch at 18:34:45 and 18:34:50. Two deploy runs, both `success`. The deployment's image was the first commit's; the second commit's routes returned 404 on the new pod. Fix: re-run the deploy job of the second run only; the pod then carried both. On production the same two commits deployed a few minutes apart without racing, and the image was checked anyway before the ticket was closed.

## Target

ENGINEERING-LESSONS, verification section ("verified means live"), once a second domain supplies a case.
