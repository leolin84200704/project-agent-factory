# Proposal: a rehearsal environment only rehearses what it actually shares with production

Status: inbox (unreviewed). Source: lis instance dream 2026-09-29.

## Lesson (candidate text)

"We tried it on staging first" is evidence only for the parts of staging that
are a faithful twin of production: same code revision, same dependency targets,
same configuration shape. Any dimension where the rehearsal environment
diverges — a branch that never received the change, a config key pointing at a
different backend, an environment variable set directly on the deployment that
overrides the file you edited — turns a green rehearsal into no information at
all, while still feeling like a safety step.

Before treating a staging run as evidence for a production change, check the
specific dimension the change exercises:

- the rehearsal build actually contains the code path (`git merge-base
  --is-ancestor <commit> <rehearsal-branch>`, or read the running image tag);
- the configuration value you are about to change is the one the process reads
  (no higher-precedence override);
- the dependencies the change touches point at the rehearsal twins, not at
  production or at something third.

If any check fails, say so and get the production evidence another way (a probe
run from inside the production process against both old and new targets, with
byte-level comparison), instead of reporting the rehearsal as done.

Discipline: a rehearsal is evidence only along the dimensions where the
rehearsal environment is verified to match production.

## Why it is in the inbox, not a PR

Single-domain evidence so far (one engineering employer; three instances on
one day: a staging branch 53 commits behind the production branch on the
changed path, 8 of 28 staging config keys pointing at non-twin backends, and a
deployment-level env override making a config edit a no-op). The text reads
standalone and prescribes a discipline; it needs a second, unrelated domain
before it clears the promotion bar.

## Evidence (identifying details, not for the lesson text)

- VP-18466 (2026-09-29): LIS-transformer-v2 `stage_test` never received PR #629;
  the staging image's proto lacked the new RPC, so staging could not be flipped
  to the mode being verified.
- VP-18460 (2026-09-29): `lis-trans-config-st` had 8/28 public-URL keys pointing
  at on-prem dev/prod services rather than AKS staging twins; production
  evidence came from a probe run inside the production pod instead.
- VP-18460 (2026-09-29): `OAUTH2_TOKEN_ENDPOINT` on the staging deployment is an
  explicit `env` entry overriding the ConfigMap, so a ConfigMap edit there is a
  no-op.
