# Proposal: a behaviour gated on an environment variable must be re-verified against the target's runtime, not copied from the source

- Source: lis instance, VP-18800 (a module ported from service A to service B kept its "only in production" check on the same variable; B sets that variable to the production value in every environment, so the check would have fired in staging too)
- Date: 2026-10-19
- Status: inbox (fails the cross-domain bar: one employer-domain case)

## Lesson (de-identified)

When code that branches on an environment variable is moved between services, the branch condition is not portable even if the variable name is. Each service decides what that variable means by how it is launched. Before the port is considered equivalent, read the variable's actual value from a running process in every environment of the target (the process's own environment or launch command, not the config file or manifest), and pick the gate that reproduces the source's behaviour per environment. Then prove the gate from the outside with a request that must be refused in production and accepted elsewhere.

## Why it is not yet universal

One case from one domain. The failure mode is generic (any twelve-factor service that encodes "mode" in an environment variable, any port between repositories that grew up with different launch conventions), and the same discipline already exists for config values in this instance (read the live value back), but the bar is two cases from different domains.

## Example

Service A enforced fourteen permission checks only when `NODE_ENV === 'prod'`, because its staging launch script sets `dev`. Service B launches with `NODE_ENV=prod` in both staging and production and distinguishes them with a second variable. The port switched the gate to the second variable; a production read-only comparison then showed every protected route returning 403 for an unprivileged token, which was the first proof the gate was live.

## Target

ENGINEERING-LESSONS, verification section ("verified means live"), once a second domain supplies a case.
