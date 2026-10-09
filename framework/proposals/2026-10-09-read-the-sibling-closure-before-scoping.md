# Proposal: read the sibling's closure before scoping your own ticket

**Status**: inbox (unreviewed). Fails the cross-domain bar: one strong case and two
weaker ones, all from the same employer domain (an LIS backend). Kept here until a
second domain supplies an incident.

## Lesson (candidate text)

A ticket's list of work items is a hypothesis written on the day the ticket was filed.
Before building anything, read the closing comment of every sibling ticket on the same
epic (or the same component) that reached Done after the ticket was written. A sibling's
closure frequently absorbs part of your scope, renames the thing you were told to change,
or records that the premise no longer holds. The cost is minutes; the cost of skipping it
is re-implementing something that already shipped, or shipping a second copy of it.

Discipline: Step 1 of any ticket = enumerate siblings in the done category newer than the
ticket, read their closing comments, and rewrite the ticket's work list as "still open /
already done by X / premise changed" before touching code.

## Evidence

- 2026-10-08 (strong): a migration ticket listed four call sites to move off a legacy HTTP
  API. A sibling ticket on the same epic, closed nine days earlier by another engineer,
  had already moved three of them and listed the now-dead config keys in its closing
  comment. Reading it first reduced the work to one route deletion plus config cleanup;
  not reading it would have produced a duplicate adapter.
- 2026-10-08 (weaker, same domain): a blocked story's premise ("vendors will send a
  practice id and we need to read it") was contradicted by measured production data that
  an earlier analysis had already recorded; the real fix turned out to be a different
  change in a different layer, and a further ticket had to be opened for the two entry
  points the story never named.
- 2026-10-07 (weaker, same domain, already lesson #93): a proxy-retirement ticket named a
  caller that had been commented out a year earlier; the retirement was mostly deletion.

## Why it is not yet universal

All three cases are one team's ticket queue; no evidence from a second domain (ops,
data, infra, product) that the same "sibling absorbed my scope" shape repeats there.
Lesson #93 (a reference in source is not a caller) covers the code-side half; this
proposal is the ticket-side half and should be merged with #93 or promoted only once a
non-LIS case appears.
