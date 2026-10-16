# Proposal: before running a test case that should fail on permission, verify read-only that the actor lacks the permission

- Source: lis instance, VP-18801 (an A/B smoke case expected 403 from a write route; the test account actually held the role, both systems under test accepted the write, and a staging record was modified twice)
- Date: 2026-10-15
- Status: inbox (fails the cross-domain bar: one employer-domain case)

## Lesson (de-identified)

A test whose expected outcome is "rejected" is only safe if the rejection actually happens; when it does not, the request goes through and the side effect is real. Before sending a write that is supposed to be forbidden, confirm with a read-only check that the actor really lacks the permission (or use a credential that provably lacks it). A passing write cannot be taken back, so the assumption behind an expected failure has to be verified first.

## Why it is not yet universal

One case from one domain. The pattern (an expected-failure test that silently succeeds and mutates state) applies to any permissioned write surface, but the bar is two cases from different domains.

## Example

A smoke suite compared a legacy service with its replacement using one staff token. One case was meant to prove both reject an unauthorised partial-result write with 403. The token's user had the permission, both services wrote the value, and the staging sample carried a bogus result until it was noticed in the report.

## Target

ENGINEERING-LESSONS, verification discipline, once a second domain supplies a case.
