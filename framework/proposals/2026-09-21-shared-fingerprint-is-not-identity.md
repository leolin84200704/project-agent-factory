# Proposal: attribute a caller by something only it has, never by a feature it shares

Status: inbox (unreviewed). Source: lis instance dream 2026-09-21.

## Lesson (candidate text)

A client library version, a user-agent string, a language runtime or a network address are
features, not identities: a second party with the same feature makes the attribution silently
wrong while the evidence still looks tidy. When you must say "these calls come from service X",
use a property that X alone has — a route only X calls, an environment value only X carries, a
token subject issued only to X — or a shared property whose sharing you have ruled out for the
exact time window (an address is usable only while you can show it was bound to X for the
whole period the calls span; addresses are recycled). Treat a perfect match on a shared
feature as a shortlist, and write the attribution down only once a unique property confirms it.

Discipline: an attribution claim names the unique property it rests on. "UA matches" and "IP
resolves to" are not allowed to stand alone in a conclusion.

## Why it is not promoted tonight

Two instances, but from the same session, the same domain and the same investigator:

- 2026-09-21 (a): a pod IP seen in an upstream's access log was resolved to a pod by name; the
  calls predated that pod's start time — the IP had belonged to something else and been
  recycled. Fixed by re-querying only the window after every pod in the namespace existed.
- 2026-09-21 (b): the HTTP-client versions declared by two repositories matched the two
  user-agents in a log exactly (`1.13.6` / `1.16.0`), so the traffic was about to be attributed
  to them. A route neither repository references had 121 hits with one of those user-agents —
  another service ran the same client version. Attribution had to fall back to the caller's
  own address, with the lifetime check from (a).

A case from another domain (a browser fingerprint mistaken for a user, a `From:` header
mistaken for a sender, a MAC/OUI mistaken for a device model) would clear the cross-domain bar.

## Target if promoted

ENGINEERING-LESSONS, evidence / attribution section (near the "Verified means live" family).
