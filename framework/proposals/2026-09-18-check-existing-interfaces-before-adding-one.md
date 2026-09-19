# Proposal: before adding a new interface for a capability, enumerate the interfaces that already expose it

Status: inbox (unreviewed). Source: lis instance dream 2026-09-18.

## Lesson (candidate text)

When a request reads "the current entry point cannot do X, so build one that can", the first
step is an inventory of every entry point the same system already exposes — the RPC/proto
surface, CLI, admin tooling, message consumers — and a check of whether any of them already does
X. A service that grew over years usually has a second door. Building a new door because the
front one is stuck produces code that duplicates a working path, and the duplication only becomes
visible when a reviewer asks what the change actually adds.

Discipline: a PR that introduces a new endpoint/command states, in its body, which existing
interfaces were checked for the same capability and why each was insufficient. If that list is
empty, the PR is premature.

## Why it is not promoted tonight

Single-domain evidence, one ticket:

- 2026-09-17: a synchronous HTTP endpoint timed out at a gateway on long-running work. The agent
  built an asynchronous variant (return 202, add a status endpoint; 471 insertions, 0 deletions)
  on the premise that the work could never finish inside the gateway budget. The reviewer chose
  to keep the call synchronous and raise the gateway budget instead. Once that premise was gone,
  the two things the PR still offered — trigger without waiting, poll for status — were both
  already provided by the service's existing gRPC interface, with no deployment. The reviewer
  asked "so what does this help with?" and the PR was closed unmerged. The gRPC surface had not
  been checked before the REST feature was written.

Needs a second case from a different domain (e.g. a new admin script written where a management
command already existed, or a new webhook added where a queue consumer already delivered the
same event) before it clears the cross-domain bar.

## Target if promoted

ENGINEERING-LESSONS, design / scoping section, near "Confirm Before Over-engineering".
