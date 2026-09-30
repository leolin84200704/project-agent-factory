# Proposal: probe the server's reject path before adding a credential to a caller

**Status**: inbox (unreviewed) — written by the lis dream run 2026-09-30.
**Source**: one domain only (LIS-7882, an internal gRPC caller adding an OAuth2 bearer token). Fails the cross-domain bar, so it is an inbox item, not a lesson PR.

## Lesson (de-identified)

When you add an authentication header to a caller that currently works without one, first send a deliberately invalid credential to the server and observe what happens. Servers that accept "no credential" often *reject* "a credential that fails verification" — so adding the header creates a failure mode that did not exist before (empty, expired, or mis-signed tokens). Design the failure policy from that probe: if the server does not require the credential, omit the header entirely when you cannot obtain one (never send an empty value), and count the omissions so a permanently broken credential source is visible.

## Why it might be universal

The shape "opt-in credential is treated worse than no credential when malformed" recurs in HTTP bearer auth, gRPC metadata, and webhook signatures. But this run has only the one incident as evidence.

## Evidence

- LIS-7882 (2026-09-30): the results server passed token-less calls with a warning and rejected any token it could not decode. A live probe with `Bearer not.a.jwt` proved the block path on one endpoint and its absence on an older replica. The caller was shipped fail-open (omit header + warn counter + damped alert) instead of strict.

## Target

`framework/ENGINEERING-LESSONS.md`, next to "Validate the grader" / the fail-open counter lesson — only if a second, non-LIS case appears.
