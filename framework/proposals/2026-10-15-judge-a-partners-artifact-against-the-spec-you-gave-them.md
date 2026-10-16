# Proposal: judge a partner's artifact against the specification you handed them, not against your implementation

- Source: lis instance, BIOINSIGHTS-onboarding 10-15 retrospective (two field audits told an integrator that six header values were invalid; all six were mandated by the specification document the team had sent them three weeks earlier, and the integrator pushed back with the document)
- Date: 2026-10-15
- Status: inbox (fails the cross-domain bar: one employer-domain case; related to the 2026-10-13 provenance proposal but a different rule)

## Lesson (de-identified)

When a counterparty builds to a document you gave them, that document is the contract. Auditing their output against your parser, your tests or your memory of the format judges them by a standard they never received, and any gap between your document and your code is your defect, not theirs. Record what was shared with the partner at the moment it is shared, diff their artifact against that, and keep a separate list of spec-versus-implementation gaps that you own.

## Why it is not yet universal

One case from one domain. The shape (reviewing someone's deliverable against an internal reference instead of the agreed one) plausibly recurs in API contracts, vendor onboarding, data-exchange formats and procurement, but the bar is two cases from different domains.

## Example

A specification PDF and a technical reference were emailed to an integrator. Weeks later two audits of their sample file flagged the sending-facility, receiving-facility, version, priority, action-code and fasting fields as wrong. Every flagged value matched the PDF. The team had never stored the PDF alongside its notes, so the audits only had the parser to compare against.

## Target

ENGINEERING-LESSONS, verification discipline (next to "Memory References the World"), once a second domain supplies a case.
