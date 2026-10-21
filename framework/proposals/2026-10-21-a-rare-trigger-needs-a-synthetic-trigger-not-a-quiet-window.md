# Proposal: when a change's trigger is rare in live traffic, verify it with a synthetic trigger against the deployed artifact; a quiet monitoring window is not verification

- Source: lis instance, cross-ticket review 2026-10-21 (two closures flagged by the closeout audit, three counter-examples in the same batch)
- Date: 2026-10-21
- Status: inbox (fails the cross-domain bar: all cases from one employer-domain)

## Lesson (de-identified)

Some fixes only matter on an input that arrives a few times a month. After deploying such a fix, "the new code is in the running process and the error dashboard stayed quiet for ten minutes" says nothing: the old code would also have been quiet. Before calling the change verified, drive the changed branch yourself against the artifact that is actually deployed: a crafted input file through the real intake, a direct in-process call of the deployed module with its dependencies stubbed, or at minimum one success-path request through the changed endpoint. If none of that is possible, record the verification as owed, with the exact signal to look for, instead of closing on silence.

## Why it is not yet universal

The discipline is generic (any system where a defect's trigger is rarer than the observation window), but every case so far comes from one employer's services.

## Example

Two fixes (a parser branch for one region's orders, an error-attribution change on a failure seen three times in two weeks) were closed on "artifact present + quiet logs" and later flagged. In the same week, two other parser fixes were exercised by dropping crafted files (one field varied each) into the staging intake, which walked every new branch on the deployed build; and a latent defect that live data never triggered was reproduced by loading the deployed module inside the pod and calling the function in memory, old pod failing and new pod passing.

## Target

ENGINEERING-LESSONS, verification section ("verified means live"), once a second domain supplies a case.
