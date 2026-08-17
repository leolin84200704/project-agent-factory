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

## Escape hatch

`git commit --no-verify` / `git push --no-verify`. These guards state a rule;
they do not claim to be right about every case.
