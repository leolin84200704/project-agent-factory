# Proposal: a recorded prerequisite is open until re-read live in the session that acts on it

Status: inbox (unreviewed). Source: lis dream 2026-11-03.
Fails bar: cross-domain evidence (one employer domain only). Strong enough to keep for triage.

## Lesson (de-identified)
A prerequisite written in notes or memory ("X must be configured before switching traffic") is an
open blocker, not a checked item, until it is re-read from the live system in the same session that
performs the switch. The evidence of the re-read (names, not secret values) goes to the approver
before the action. Any message the agent writes on the user's behalf must not assert that a
prerequisite holds unless it was verified in that session; a fact taken from a colleague's earlier
comment is cited as such.

## Evidence
- lis VP-18803 (2026-11-03): a config key required by the new service was recorded as a hard
  prerequisite three times in memory; the traffic cut-over went ahead with the checklist item marked
  done from a colleague's comment, and an agent-written ticket comment stated the key was present.
  ~100 minutes of 500s for real users.
- lis INCIDENT-20261013 (related, same domain): memory recorded a source email that never existed;
  two "L4 checks" verified the derived DB row instead of the originating message.

## Why not promoted
Both cases come from the same employer domain. Overlaps factory core principle 5 ("Memory References
the World") and hot lesson "Verified means live, not mock"; the new part is the *timing* (re-read in
the acting session) and the *outbound assertion* rule.
