# Proposal: after fixing a shared dependency, sweep every consumer path, not only the one that raised the alarm

Status: inbox (unreviewed). Source: lis instance dream 2026-09-11.

## Lesson (candidate text)

When a dependency that several code paths share breaks and you fix it, the incident is not over
when the path that alarmed is healthy again. Enumerate every other consumer of the same dependency
and check each one for work that failed or got parked while it was down. A consumer that fails
quietly (retry exhausted, moved to a holding table, no alert) will surface days later as a customer
report, and by then it looks like a new incident.

Discipline: keep a list of consumers per shared dependency (or grep for the dependency's address /
client at fix time), and add "swept consumer X: N stuck items, replayed / none" lines to the
incident record before calling it resolved.

## Why it is not promoted tonight

Single-domain evidence. One incident, one system:

- 2026-09-08: a gRPC target shared by result generation and order intake died (recycled node IP).
  The alarm came from result pushes; they were fixed and 58 stuck results re-driven the same day.
  Order intake used the same target. 8 inbound orders had exhausted their retries and sat in a
  quarantine table with no alert and no auto-replay. Nobody looked there. The next morning's
  triage job found them; by then the customer had already filed a support ticket about a
  "missing" order, which became a bug ticket that was closed, reopened by the reporter, and
  closed again.

Needs a second case from a different domain (e.g. a shared auth service or message broker outage
where a secondary consumer's backlog was missed) before it clears the cross-domain bar.

## Target if promoted

ENGINEERING-LESSONS, incident-handling section, next to "Preserve evidence before restart".
