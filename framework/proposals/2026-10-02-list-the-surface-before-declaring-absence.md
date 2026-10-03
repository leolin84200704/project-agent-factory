# Proposal: list the surface before declaring "upstream does not have it"

- **Source**: lis dream 2026-10-02 (cross-ticket review of VP-18589 / VP-18593 / VP-18406)
- **Status**: inbox — single employer domain (three cases, all LIS integration work), so not a PR. Promote if a second domain produces the same shape.

## Lesson (de-identified)
Before concluding that a dependency "does not expose X", enumerate the dependency's full surface and record what was enumerated: every handler the server registers (not just the one interface file you found), every field the wire format carries (diff your local copy against the producer's), and every key the payload actually has (print the keys instead of guessing the name). A conclusion of absence that does not list what was checked is an unverified claim and must be labelled as such.

## Why it is a discipline, not a behaviour description
It prescribes a verification step before a negative claim. The failure it prevents is asking another team for something they already provide, or designing around a gap that does not exist.

## Evidence (identifying details stay here)
1. 2026-08-13: "no upstream has carrier / shippedAt" — the local proto copy had 4 of 5 fields; the loader silently dropped the 5th. Overturned 2026-09-30 (VP-18593).
2. 2026-09-30: "the shipping RPC does not expose delivery time" — the same gRPC server serves a second proto file with `GetTrackingDetails`. Overturned the same evening after the user pushed back ("don't change the other team's service").
3. 2026-09-28: an exploration agent concluded the report-release date was not in the payload because it read `o.sample`; the key is `o.samples`. It flagged its own claim as unverified, which is what saved it (VP-18406).

## Target
`framework/ENGINEERING-LESSONS.md` — verification discipline section, if a cross-domain case appears.
