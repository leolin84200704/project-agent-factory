# Proposal: a metric that improves right after a deploy is first explained by the restart, not by the change

Status: inbox (unreviewed). Source: lis instance dream 2026-09-21.

## Lesson (candidate text)

Every deploy is also a process restart, and a restart on its own resets connections, empties
caches, zeroes counters and re-elects consumers. So "errors dropped to zero after the deploy"
is, until proven otherwise, a statement about the restart. Before crediting the change with an
improvement, split the pre-deploy signal by kind and ask which kinds a plain restart would have
cleared anyway; if the bulk of the improvement is in those kinds, the change has not been
measured yet. Pick at least one metric that a restart cannot move — a request/response pairing
count on the touched path, the byte-identity of an output, a per-route status mix — and let
that carry the "no harm" claim. A clean zero also has to be shown to be a real zero: the same
window must contain ordinary traffic from the same process, or the quiet may be a dead log
pipeline.

Discipline: a post-deploy comparison is written as "metric X, chosen because a restart cannot
change it, was A before and B after", never as "errors went from N to 0".

## Why it is not promoted tonight

Single-domain evidence, one program:

- 2026-09-21: a log-only interceptor was deployed to a Node service. In the six hours before
  the deploy the service had 638 error-level lines; after it, zero. 552 of the 638 were a
  message-broker `ECONNRESET` that the pod restart cleared by reconnecting — the change had
  nothing to do with them. The evidence that finally carried the "no harm" claim was that the
  touched streaming route had 11 requests and 11 responses after the deploy, all 200, and 2,260
  info lines in the same window proved the log pipeline was alive. The same day a second
  service, redeployed with a one-line change, showed 32 error lines in its first three
  minutes; 17 were a pre-existing message mislabelled as error at 271–1,832 per hour, the rest
  were consumer-group rebalance on start — no new kind, so the deploy was clean, but counting
  would have said the opposite.

A second case from a different domain (a batch job whose failure rate "improved" after a
scheduler restart, a cache-hit ratio that "regressed" after a rollout because the cache was
cold, a queue depth that "recovered" because the consumer re-registered) would clear the
cross-domain bar.

## Target if promoted

ENGINEERING-LESSONS, verification / evidence section, next to "Verified means live, not mock"
and the quiet-window lesson.
