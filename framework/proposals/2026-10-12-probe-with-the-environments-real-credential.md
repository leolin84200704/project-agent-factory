# Proposal: verify an auth path with the target environment's real credential, not a self-minted one

**Status**: inbox (unreviewed). Fails the cross-domain bar: one strong case plus one
supporting case, both from the same employer domain (an LIS backend). Kept here until
another domain supplies an incident.

## Lesson (candidate text)

A unit test that signs its own token with a key pair it generated proves only that the
verifier code is wired. It cannot prove that the deployed environment holds the key that
matches the issuer it will actually see. Before declaring an auth path verified on an
environment, send one request carrying a credential minted by that environment's real
issuer, and one carrying a credential from the wrong issuer, and read the verifier's own
log line for each. Key material drifts between environments silently: a public key that
belongs to a different issuer still parses, still loads, and still fails every real
token.

Discipline: for every environment where an auth change lands, the acceptance evidence
includes (a) a real credential from the issuer that environment is configured to trust,
(b) its verification outcome as logged by the verifier, and (c) the key fingerprint
compared against the issuer's. A green self-signed test is a precondition, not evidence.

## Evidence

- 2026-10-12 (strong): a gRPC server gained a token guard whose spec generated its own
  RSA pair and passed. The staging probe used a real OAuth2 RS256 token instead and got
  `invalid signature`: the staging deployment's configured public key (fingerprint
  a0cca41f…) differed from production's (4cbc4d8f…) and from the one the results service
  uses (745603b3…); staging mints tokens from the production issuer but trusts a key from
  somewhere else. In log-only mode nothing broke, but the planned flip to enforce on
  staging would have rejected every caller. Nothing in the test suite could have seen it.
- 2026-10-12 (supporting, same domain): the outbound half of the same change was
  accepted only after the downstream service's own `token_check` log showed the caller
  flipping from `token_class=none` to `service`; the client-side "token attached" log
  line alone was not treated as proof.

## Why it is not yet universal

Both cases are the same subsystem on the same day. The discipline reads standalone
("verify with the target environment's real credential and read the verifier's log"),
but the cross-domain bar needs a second domain — a CI secret, a cloud IAM role, a
payment-provider webhook signature — before it goes into ENGINEERING-LESSONS.

## Target

ENGINEERING-LESSONS.md, verification section (next to "Verified means live, not mock").
