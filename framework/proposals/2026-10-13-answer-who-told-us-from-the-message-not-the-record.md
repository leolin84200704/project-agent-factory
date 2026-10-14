# Proposal: answer "who told us X" from the original message, never from the record we wrote from it

- Source: lis instance, INCIDENT-20261013 (a practice was attached to the wrong vendor for 78 days; two later "verify against ground truth" checks re-read the database row the agent had written from the misread email, and never the email)
- Date: 2026-10-13
- Status: inbox (fails the cross-domain bar: one employer-domain incident; a second case from another domain is needed before it can be a lesson PR)

## Lesson (de-identified)

When a question is about provenance (who said it, which thread it came from, what the sender actually asked) the only ground truth is the original message. A record derived from that message (a database row, a config entry, a memory note) inherits whatever misreading produced it, so re-reading the record "verifies" the error against itself. Before acting on "X told us Y", open the message, check the sender's domain and the thread it belongs to, and cite that message in the record.

## Why it is not yet universal

Only one incident supports it, and it comes from one domain. The pattern (a derived artifact standing in for its source during verification) is plausibly general, the provenance form of "memory references the world", but the bar is two cases from different domains.

## Example

An "integration details" email arrived in a thread about partner A while the agent was waiting for partner B to name a practice. The agent recorded "B gave us these details", created a live mapping, and on two later occasions verified the mapping by re-reading the row it had created. The customer named in the row complained; only a search of the mailbox showed the email had never come from B.

## Target

ENGINEERING-LESSONS, verification / ground-truth section (next to "Memory References the World"), once a second domain supplies a case.
