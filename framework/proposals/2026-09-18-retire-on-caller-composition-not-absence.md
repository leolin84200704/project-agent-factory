# Proposal: retire an interface on evidence of who calls it, not on a window where nobody did

Status: inbox (unreviewed). Source: lis instance dream 2026-09-18.

## Lesson (candidate text)

A quiet window is evidence that nobody called during the window. It is not evidence about who
the callers are, and a retirement decision is a claim about callers. Before removing an endpoint,
queue, table or file drop, characterise the caller population over the longest window your
telemetry keeps: daily counts per route, ratios between routes, and whether those ratios hold
day after day. A fixed ratio that never breaks is one caller with a fixed pattern; a ratio that
breaks on some days is at least two. Then make the removal loud in advance (announcement with a
deadline) precisely because the callers your telemetry cannot see — monthly batches, scheduled
jobs, other teams' scripts — are the ones a quiet window misses.

Discipline: "no traffic for N hours" never appears in a retirement decision on its own. It is
replaced by "over the full retention window the route shows pattern P, consistent with caller
population C; announced on D with deadline D+14".

## Why it is not promoted tonight

Single-domain evidence, one program:

- 2026-09-18: after cutting an internal service over to direct gRPC, two proxy routes showed
  zero hits for 46.5 hours and were about to be declared retirable. The reviewer asked for the
  14-day view. Over 15 days the two routes moved in lockstep every single day (difference ≤ 1)
  with a third route at exactly twice their count — one caller, one per-sample pattern — and a
  fourth route the agent had grouped with them was flat at 1–5/day and untouched by the cutover,
  so it belonged to a different, still-unidentified caller. The 46.5-hour zeros were the same
  inference with a larger sample; the 15-day pattern was a different kind of evidence. The same
  session's own risk table had listed "rare callers (monthly batch, scheduled job)" and not
  applied it.

A second case from another domain (a feature flag, a config key, a database column, a message
topic removed after a quiet period and then found to have a periodic consumer) would clear the
cross-domain bar. Related but distinct: `2026-08-10-calibrate-a-query-before-trusting-its-zero.md`
(the query may be wrong) and lesson #84 (validate a zero-result query against known-present data)
— this proposal is about a *correct* zero that still answers the wrong question.

## Target if promoted

ENGINEERING-LESSONS, verification / evidence section, next to "Verified means live, not mock".
