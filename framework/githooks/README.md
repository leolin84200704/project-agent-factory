# Shared git hooks

Version-controlled `pre-commit` / `pre-push`, wired into repos through
`core.hooksPath`.

```bash
bash framework/githooks/install.sh            # global (recommended)
bash framework/githooks/install.sh <repo-dir> # one repo only
bash framework/githooks/tests/githooks.test.sh
```

## Why not `.git/hooks/`

That is where these guards used to live. `.git/hooks/` cannot be committed, is
invisible when browsing the repo, and does not survive a fresh clone — so rules
that had each already been broken in production were being protected by files
nobody could see and nobody would receive. A per-repo `core.hooksPath` has the
same defect, because it lives in `.git/config`. Only the **global** setting is
clone-proof, and that is what shapes everything below.

## Two properties that make global wiring safe

**They chain.** Setting `core.hooksPath` makes git ignore `.git/hooks/`
entirely. git-lfs installs `pre-push`, `post-commit`, `post-merge` and
`post-checkout` there, and silencing them corrupts LFS pointers. Each hook here
runs the repo's own hook first and honours its exit code. (Resolving that path
needs `--git-dir`, not `--git-path hooks` — the latter returns `core.hooksPath`
once set, i.e. this directory, which would recurse.)

**They are repo-aware.** One file is wired from many repos, so every guard
decides for itself whether the repo is in scope and does nothing when it is not.
A guard that fires where it was never agreed is a guard that gets turned off.

| guard | scope | how scope is decided |
|---|---|---|
| config coupled with code | repos shipping a ConfigMap pair at the root | by shape — `*-config.yaml` + `*-config-prod.yaml` exist |
| no CJK in code | everywhere | `.ts` / `.js` / `.sql` only; markdown exempt |
| build before push | opt-in list | origin repo name matches `BUILD_GATE_REPOS` |
| Nest DI boot smoke | opt-in list, Nest repos only | in `BUILD_GATE_REPOS` **and** `package.json` depends on `@nestjs/core` |
| related unit tests before push | opt-in list | origin repo name matches `TEST_GATE_REPOS` **and** `package.json` mentions jest |

The build gate is a list rather than "any repo with a build script" on purpose:
forcing a build on every push in every JS repo is a workflow change nobody
agreed to.

## Local overrides win

A repo's **local** `core.hooksPath` beats the global one. Check for stale ones:

```bash
git -C <repo> config --local core.hooksPath
```

One was found pointing at a directory that a rename had deleted months earlier,
which had silently disabled that repo's hooks the entire time. That is the same
failure this directory exists to prevent, one level up.

## Nest DI boot smoke (`lib/nest-di-smoke.sh`)

Runs right after the build gate, for gated repos that depend on `@nestjs/core`.
It boots the compiled entry (`dist/main.js`) for at most 30 s with an **empty**
environment (`env -i` + PATH, HOME, `NODE_ENV=test`, a random PORT) and reads
the output:

| verdict | rule | exit |
|---|---|---|
| PASS | no DI error, and evidence the injector finished: the preload's `NestFactory.create resolved` marker (logger-independent), or ≥1 `<Module> dependencies initialized`, or "successfully started" / "running on", or the process is listening on a TCP port | 0 |
| FAIL | `UnknownDependenciesException` / `can't resolve dependencies` / circular, undefined-module … | 1 |
| INCONCLUSIVE | no sign the injector ran at all — treated as FAIL; `DI_SMOKE_ALLOW_SILENT=1` accepts | 3 |

The app is started with `node -r lib/nest-di-smoke-preload.js`, which wraps the
app's own `NestFactory.create` and prints a marker when its promise resolves —
Nest returns from `create()` only after every provider is instantiated, so this
is the DI verdict itself, independent of `logger: false` / `logger: ["error"]`
and of how long the app then takes to `listen()`. A rejection is printed too,
even when `bootstrap()` swallows it.

Whether the app finishes booting is *not* the criterion: with no database it
will die on a config check or wait on a connection, and both are fine — Nest
resolves the whole graph before either. Measured on the reference repo: the
crashing commit fails in ~3 s after 6 modules; the fix passes in ~2 s after 40.

Why it exists: on 2026-09-10 the build gate passed and both new pods
crash-looped, because an `@Injectable` constructor param typed as an interface
(`client?: SomeInterface`) is, to Nest, a *required* provider registered under
`Object`. `tsc` cannot see DI metadata; every unit spec either `new`ed the
service by hand or stubbed the new provider in the one `TestingModule`. The
injector is the only thing that can tell, and it tells within a second.

`.env.example` is deliberately not read: it added zero modules on the reference
repo, and such files carry real internal hosts (gRPC targets, Kafka brokers, a
staging consumer-group id) a booting app would contact. An app that validates
config at module load (`ConfigModule.forRoot` + Joi: `"JWT_SECRET" is required`)
dies before the injector and comes out INCONCLUSIVE; give it dummies in the
**per-machine** file `~/.config/nest-di-smoke/<origin-repo-name>.env` — one
file per repo per machine, shared by all its clones and worktrees, never inside
a product repo. The INCONCLUSIVE report prints the exact path and the keys it
saw demanded. A repo-local git-ignored `.env.di-smoke` is also read (wins on
conflicts); `DI_SMOKE_ENV_FILES` overrides the whole list. The same file may
carry `DI_SMOKE_ALLOW_SILENT=1` / `DI_SMOKE_TIMEOUT=…` for an app whose DI phase
awaits real infrastructure (an async provider connecting to Redis/Kafka before
the graph completes): the silent verdict is then accepted, while a printed DI
error still fails. Measured 2026-09-11: one repo needed exactly this.

The prisma step before the build prefers the repo's own `prisma:generate` /
`generate:prisma` script when it has one: multi-schema repos generate every
client there, and a bare `npx prisma generate` refreshes only the default
schema.

Standalone, from a repo root after a build:

```bash
bash $FACTORY/framework/githooks/lib/nest-di-smoke.sh
```

Knobs: `DI_SMOKE_ENTRY`, `DI_SMOKE_TIMEOUT`, `DI_SMOKE_ENV_FILES`, `DI_SMOKE_LOG`,
`DI_SMOKE_ALLOW_SILENT`. The cheaper, per-PR habit that makes this gate
redundant: one spec per module that does
`Test.createTestingModule({ imports: [ConfigModule.forRoot(...), TheRealModule] }).compile()`.

## Related unit tests before push (pre-push, after the DI smoke)

The build gate proves the code compiles; the DI smoke proves the graph resolves;
neither proves one branch of the new logic behaves. **Test before push** in
ENGINEERING-LESSONS sat at ladder step 7 (prose) until 2026-09-18 — the canary
that motivates the ladder measured prose lessons at 0/3 compliance.

For repos in `TEST_GATE_REPOS` the hook diffs `HEAD` against where the branch
left `origin/main` (falls back to `origin/master`, then `@{upstream}`; with none
of the three it skips and says so), keeps the changed `.ts` under `src/ apps/
libs/` that still exist — dropping `.d.ts`, `*.integration.spec.ts` and e2e
specs, which need live infrastructure — and hands them to
`jest --findRelatedTests`. Jest runs every suite that imports those files; a
changed spec selects itself. Nothing changed → skipped, one line says so. The
repo's `pretest` script runs first when it has one (emr-v2 regenerates its test
prisma client there), and `node_modules/.bin/jest` is preferred over `npx`.

Measured 2026-09-18: one emr-v2 service file → 7 suites in 6 s; the
transformer-v2 hub `src/tool.ts` → 33 suites in 60 s; both full suites take
minutes. A spec that no longer compiles fails the gate on purpose — it means the
contract it pinned moved and nothing re-pinned it (found the same day on an
emr-v2 branch: a spec passing 9 constructor arguments to a 7–8 argument
constructor).

Why a git hook and not a Claude PreToolUse hook: this one fires for every
pusher, Leo's terminal included, and it already sits in the pipeline that the
agent cannot `--no-verify` past. Scope is a list for the same reason the build
gate is: a gate that fires where nobody agreed to it gets turned off. Pre-
existing red suites on `main` would block every push in that repo — check with
`npx jest` on a fresh `main` before adding a repo. (2026-09-18: transformer-v2
looked red locally because the clone was 24 commits behind; fresh `main` was
green.)

## Escape hatch

`git commit --no-verify` / `git push --no-verify`. These guards state a rule;
they do not claim to be right about every case. The hatch is for a human at a
terminal: the agent's own PreToolUse hook
(`framework/hooks/validate-git-push.sh`) refuses `git push --no-verify`, so a
failing gate is fixed, not skipped.
