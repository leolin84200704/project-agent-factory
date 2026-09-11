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
| PASS | ≥1 `<Module> dependencies initialized` (or "successfully started") and no DI error | 0 |
| FAIL | `UnknownDependenciesException` / `can't resolve dependencies` / circular, undefined-module … | 1 |
| INCONCLUSIVE | no sign the injector ran at all — treated as FAIL; `DI_SMOKE_ALLOW_SILENT=1` accepts | 3 |

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
staging consumer-group id) a booting app would contact. A repo that needs
dummies to get past an early config check puts them in a git-ignored
`.env.di-smoke`, or sets `DI_SMOKE_ENV_FILES=".env.example"` knowingly.

Standalone, from a repo root after a build:

```bash
bash $FACTORY/framework/githooks/lib/nest-di-smoke.sh
```

Knobs: `DI_SMOKE_ENTRY`, `DI_SMOKE_TIMEOUT`, `DI_SMOKE_ENV_FILES`, `DI_SMOKE_LOG`,
`DI_SMOKE_ALLOW_SILENT`. The cheaper, per-PR habit that makes this gate
redundant: one spec per module that does
`Test.createTestingModule({ imports: [ConfigModule.forRoot(...), TheRealModule] }).compile()`.

## Escape hatch

`git commit --no-verify` / `git push --no-verify`. These guards state a rule;
they do not claim to be right about every case. The hatch is for a human at a
terminal: the agent's own PreToolUse hook
(`framework/hooks/validate-git-push.sh`) refuses `git push --no-verify`, so a
failing gate is fixed, not skipped.
