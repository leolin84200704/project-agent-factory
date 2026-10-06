# Proposal: two sources that agree prove nothing until you know they are independent

- **Source**: lis dream 2026-10-06 (VP-18666 reversal; evidence from the 10-02 → 10-05 sequence)
- **Status**: inbox — single employer domain (one LIS incident), so not a PR. Promote if a second domain produces the same shape.

## Lesson (de-identified)
When two sources disagree about a fact and you resolve it by finding a third that agrees with one of them, first establish where each source gets its data. Two sources that read the same upstream will agree with each other whatever the truth is; their agreement is one vote, not two. Before declaring "A is right, B is misconfigured", write down, for every source you consulted, which store or environment it is backed by, and use as the deciding probe a datum that exists in only one of the candidate upstreams — if every datum you used exists in both, the test cannot distinguish them.

## Why it is a discipline, not a behaviour description
It prescribes a provenance check before counting agreement as confirmation. The failure it prevents is "fixing" the correct component because the broken one happened to agree with a clone of itself — and then publishing that conclusion to other teams, which is what makes the later reversal expensive.

## Evidence (identifying details stay here)
1. 2026-10-02: a staging list endpoint (reading a core DB) and a staging report service agreed that an order had a final report; a second report service said it did not. The agent concluded the config pointing at the second service was wrong and changed it. In fact the core DB on staging is a clone of production and the "agreeing" report service is deliberately wired to production data; the dissenting service was the only one reading staging. A colleague reversed the change on 2026-10-05 (PR #460). The agent had already posted the wrong root cause to the PM; the comment had to be rewritten twice.
2. The datum that would have exposed it existed: one order seeded by the report team on staging only (GUT5 Final) — the agreeing pair said "awaiting samples", the dissenter said "final".

## Target
`framework/ENGINEERING-LESSONS.md` — verification discipline section, if a cross-domain case appears.
