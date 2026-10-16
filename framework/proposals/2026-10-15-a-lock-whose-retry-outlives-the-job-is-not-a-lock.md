# Proposal: a distributed lock whose retry window outlives the job gives no exclusivity across the schedule

- Source: lis instance, VP-18802 (a daily import guarded by Redlock ran three times a day on three replicas for months; each replica's lock client retried for 2–4 s while the job itself finished in under a second, so every loser acquired the lock right after the winner released it)
- Date: 2026-10-15
- Status: inbox (fails the cross-domain bar: one employer-domain case)

## Lesson (de-identified)

A lock with retries only provides mutual exclusion *at an instant*. If the protected job finishes before the losers stop retrying, each loser acquires the lock after the release and runs the job again, so the "once per schedule" guarantee silently becomes "once per replica". Before trusting a lock to make a scheduled job run once, compare the lock client's total retry window with the job's duration; either make the losers give up without retrying, or hold the lock for the whole schedule window, and verify with a count of starts per schedule tick, not with the absence of lock errors.

## Why it is not yet universal

One case from one domain. The failure mode is generic to any retry-on-acquire lock (Redlock, advisory DB locks with polling, leader election with short leases), but the bar is two cases from different domains.

## Example

Three replicas, one cron at the same minute, a lock client configured to retry ten times with ~200 ms spacing, a job that takes 0.5–1 s. Logs showed three "start" lines at the same minute every day; the data layer survived because the writes were idempotent upserts, but every run also emitted a duplicate downstream event per row.

## Target

ENGINEERING-LESSONS, design-judgement section, once a second domain supplies a case.
