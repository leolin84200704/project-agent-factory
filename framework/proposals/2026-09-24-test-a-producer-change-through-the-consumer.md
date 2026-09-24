# Proposal: when you change what a producer emits, the test must read it the way the consumer does

Status: inbox (unreviewed). Source: lis instance dream 2026-09-24.

## Lesson (candidate text)

A change to the shape of something one component produces (a mapped record, a message, a
config value, a file) is only safe if the components that read it still find what they read.
A test that asserts the producer's new output against the producer's own expectation proves
nothing about that: it pins the shape you just invented. Write the assertion from the
consumer's side — call the consumer's parsing/reading function on the produced value and check
the fields the consumer actually reads — or run the consumer end to end. If you cannot name
which fields the consumers read, you are not yet ready to change the producer.

Discipline: a producer-side change ships with at least one test that fails when a consumer's
read of the new shape comes back empty.

## Why it is not promoted tonight

One instance, one domain, one investigator:

- 2026-09-22: a patient lookup was switched to a different upstream client whose lists were
  camelCase; the readers downstream used snake_case keys. The unit test asserted
  `toMatchObject({patientContact: found.patientContactList})` — the producer's own list — and
  passed. On the staging environment every order for a returning patient lost phone, email and
  address and fired a spurious update call. Caught by a live check hours later; fixed by adding
  two tests that push the produced record through the reader function.

A second case from a different domain (a serializer change that broke a downstream job, an
event-schema rename passing its own contract test while a subscriber silently dropped it) would
clear the cross-domain bar. The existing lesson "verified means live, not mock" is adjacent but
does not name the direction of the assertion.

## Target if promoted

ENGINEERING-LESSONS, testing / verification section (near "Test before push").
