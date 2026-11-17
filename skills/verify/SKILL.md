---
name: verify
description: Execute the declarative `.agent-verify.json` High-Trust verification contract (`paf verify`) and record a SHA-pinned Blind Adversarial Review receipt (`paf record-review`).
---

# High-Trust Verification Skill (`paf verify`)

Use this skill before opening or merging any Pull Request in a repository governed by `.agent-verify.json`.

## Step 1: Run Deterministic Gate (`paf verify`)

Run the unified verification engine from the repository root:

```bash
paf verify --repo . --mode pre-push
```

What `paf verify` enforces automatically:
1. **Dune Architecture Static Checks (`paf lint-code`)**:
   - `DUNE001`: No unhandled floating `void fn()` promises in TypeScript/JavaScript.
   - `DUNE002`: No hardcoded URL fallbacks on `process.env` / `os.getenv` outside tests.
   - `DUNE003`: No NestJS `@Injectable` optional `?` constructor parameters missing `@Optional() @Inject(TOKEN)`.
   - `DUNE004`: No CJK characters in source code files (unless explicitly allowlisted in `dune_lint.cjk_allow_globs`).
   - `DUNE005`: No hardcoded API keys, tokens, or connection passwords.
2. **Configured Stages**: `lint` -> `typecheck` -> `unit_test` -> `boot_smoke` -> `replay`.
3. **Fail-Loud Guarantee**: If zero checks match or execute, `paf verify` fails loudly rather than passing silently.
4. **Artifact Output**: Writes `<git-common-dir>/paf-verify-<short_sha>-pre-push.{json,md}` pinned to `HEAD`.

## Step 2: Self-Assess Diff Risk & Spawn Blind Adversarial Reviewer

Before invoking any review model or subagent, self-assess the branch complexity and select the reviewer model explicitly (`framework/MODEL-BENCHMARK.md`):

```bash
paf select-model --repo . --diff-base main --author-family <claude|gemini> --role reviewer
```

To prevent reward hacking and author confirmation bias, spawn an independent read-only `research` subagent matched to the assessed tier (`flash` / `sonnet` for `trivial`/`standard`; `pro` / `opus` for `complex`/`critical`):
- **Inputs allowed**: `git diff <base>...HEAD`, changed file paths, and `paf verify` output.
- **Inputs forbidden**: Your reasoning, justification, or design narrative.
- **Mandate**: Attempt to break the patch — look for tautological mocks, untested failure branches, cross-environment defaults, silent data loss, or boundary violations. Return `approve` or `request_changes` with concrete findings.

## Step 3: Record SHA-Pinned Receipt

Once the independent subagent returns `approve` on the exact `HEAD` commit:

```bash
paf record-review \
  --repo . \
  --verdict approve \
  --reviewer blind-subagent \
  --summary "Concise one-line summary of adversarial checks verified"
```

Any subsequent commit changes `HEAD` and automatically invalidates the prior receipt.
