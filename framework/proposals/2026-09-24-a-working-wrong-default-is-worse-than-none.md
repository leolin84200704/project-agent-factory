# Proposal: an environment-specific value must have no working default

Status: inbox (unreviewed). Source: lis instance dream 2026-09-24.

## Lesson (candidate text)

When a configuration value differs per environment (an upstream address, a bucket, a tenant,
a credential path), a built-in fallback that happens to be one environment's real value is a
trap: in every other environment the code runs, reports success, and talks to the wrong
system — usually production. A missing value should make the feature stay off or the process
refuse to start, never "work" against a hard-coded target. Enabling such a path should take
two deliberate acts (set the target, then turn the mode on), so that neither one alone can
route traffic somewhere unintended.

Discipline: grep new code for literal hosts/addresses used as defaults; each one is either
removed (fail closed) or justified in writing as environment-invariant.

## Why it is not promoted tonight

Three instances, but all from one employer domain and one class of system (service-to-service
RPC addressing):

- 2026-09-23 (a): a new direct-call client shipped with the production cluster DNS as its
  fallback address; the env keys were unset on every pod, so the fallback was the live value. On
  the staging environment a "shadow compare" would have silently compared against production
  data. Fixed by removing the default and resolving the mode to "off" whenever the address is
  empty.
- 2026-09-23 (b): the same service's older client options still hard-code production addresses
  as defaults; setting only the mode flag in any environment sends traffic to production.
- 2026-09-08 → 09-24 (c): a code default pointed at a node IP that later ceased to exist; two
  environments whose config maps omitted the keys had been falling through to the dead address
  for two weeks without an error that named the cause.

A case from another domain (a build script defaulting to the production artifact registry, a
data job defaulting to the production bucket, a payment SDK defaulting to live mode) would clear
the cross-domain bar. Adjacent canon: "repo 裡的部署設定不是 runtime 真相" covers where the
truth lives, not what a default may be.

## Target if promoted

ENGINEERING-LESSONS, configuration / deployment section.
