# Proposal: When a new incident matches a past one's signature, re-verify the mechanism — never inherit the old diagnosis

- **Status**: inbox (unreviewed) — below the cross-domain bar (single-domain evidence), filed for triage
- **Source**: LIS instance, VP-17421 (2026-07-15), near-miss against VP-16921's prior diagnosis
- **Target section (if promoted)**: Debugging / Incident response

## Lesson (standalone text)

When a new incident looks exactly like a past one, the past incident's write-up is the right
thing to READ and the wrong thing to CONCLUDE. Treat the old root cause as one hypothesis,
and require the same standard of proof you'd demand for a fresh diagnosis: demonstrate the
suspected component actually possesses the failing mechanism (owns the queue, holds the
connection, runs the code path), not just that it matches on symptoms or timing.

Corollary: an event that correlates in time with the failure (a restart, a deploy) is a lead,
not a culprit. Before attributing, prove the correlated component *can* produce the failure —
if it can't even reach the resource involved, the correlation is coincidence.

## Why below the bar

Discipline-shaped and reads standalone, but evidence is two incidents from the same
subsystem in one domain (stale-reminder emails: VP-16921 rogue-environment producer vs
VP-17421 delayed-job replay). Needs a second, unrelated-domain case before promotion to
ENGINEERING-LESSONS.md.

## Evidence (identifying details stay here, not in the lesson)

- VP-16921 (2026-06): stale consult reminders — root cause was a rogue test-cluster instance
  wired to the prod notification bus.
- VP-17421 (2026-07-15): same symptom class (reminder for a long-past appointment). Agent
  initially leaned on the VP-16921 conclusion, then on a pod-restart timing correlation
  (pods that restarted at the burst minute could not even connect to the queue's Redis).
  Actual mechanism was different: overdue Bull delayed jobs replayed on Redis reconnect,
  fired by an unguarded processor path in a different service. Settled only by proving which
  Redis actually held the reminder queues and which pod consumed them.
