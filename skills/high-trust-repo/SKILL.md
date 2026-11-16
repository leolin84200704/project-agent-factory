---
name: high-trust-repo
description: Scaffold a brand-new personal repository or audit and slim an existing repository into a High-Trust Environment using the 6 structural criteria, machine-validated FEATURE_MAP.md, layered CI guardrails, initialized boot smoke tests, and dual-model PR consensus.
---

# High-Trust Repository Architecture & Slimdown Skill

Use this skill when creating a new product repository from scratch, auditing repositories during `BOOTSTRAP.md`, or slimming and upgrading an existing repository into a High-Trust Environment.

Before making structural changes, read `$FACTORY/framework/HIGH-TRUST-REPO.md` (probe `~/src/project-agent-factory` then `~/personal_project/project-agent-factory` for `$FACTORY`).

## Ownership & Trust-Zone Gate

Check `.agent-verify.json` (or confirm repository ownership with the user during initial bootstrap) before modifying any repository:
- **Personal repository (`zone: "autonomous"`)**: Execute greenfield scaffolding (Workflow A) when creating a new repository, or execute brownfield slimdown (Workflow B) when assigned to upgrade an existing repository while preserving 100% of live user-facing behavior. Note that adding or modifying `.agent-verify.json`, `.github/workflows/ci.yml`, hooks, or skills triggers `PAUSED_FOR_GOVERNANCE_APPROVAL` in `paf merge-consensus`, requiring one user confirmation (`--approved-governance`) before merging.
- **Company or team repository (`zone: "guarded"`)**: Never add unapproved directory conventions, architecture scripts, CI workflows, or branch-protection settings directly. Perform a read-only audit against Criteria 1 through 6 and record concrete gaps and proposed PRs in the instance's `long-term-memory/guardrail-gaps.md` for user approval; any approved PR targeting `main` pauses at `PAUSED_FOR_MAIN_APPROVAL` (`--approved-main`) and yields to human colleague reviews.

## The 6 High-Trust Structural Criteria

1. **Single-Directory Feature Co-Location & 1:1 Naming**: Place entrypoints, controllers, domain logic, schemas, and unit tests for each feature in one directory with 1:1 file-to-symbol naming. Forbid direct cross-service source imports.
2. **Truthfully Named Thin Shared Primitives & Golden Example**: Keep shared packages minimal and named after their actual backing technology so copying the shortest path produces the intended architecture. Forbid misleading wrapper names (such as naming a relational table wrapper after a cache or columnar engine) and pass-through single-call proxy services. Attach idle `'error'` listeners and explicit `max` bounds to every database pool, and serialize cold-start idempotent DDL with an advisory lock.
3. **Machine-Validated `FEATURE_MAP.md`**: Document active packages, routes/triggers, agent tools, and canonical SQL tables in `FEATURE_MAP.md`. Enforce bidirectional 1:1 parity against source decorators and SQL DDL via `scripts/check-architecture.*` (wired into `.agent-verify.json` `commands.lint`) with explicit minimum match count floors, verified by injecting a negative counterexample.
4. **Hard Static & CI Guardrails (`ENFORCEMENT-LADDER` Tiers 1-6)**: Configure `.agent-verify.json` using valid `commands` keys (`lint`, `typecheck`, `unit_test`, `boot_smoke`, `replay`, `post_merge_check`), `dune_lint` enabled with file-specific `cjk_allow_globs`, and matching `.github/workflows/ci.yml` (`paf verify --repo . --mode ci`). Scan both `src/` and `test/` to block cross-service imports, retired packages, `--passWithNoTests`, and unpinned `*-latest` model aliases.
5. **Closed-Loop Execution & Initialized Boot Smoke**: Cover TTL expiry across every backed data structure, wildcard escaping, zero/negative bounds, and transient vs 4xx retry behavior in unit tests. Run full container lifecycle initialization (such as `await moduleRef.init()`) with isolated test env vars in every service's boot smoke spec.
6. **Adversarial Dual-Model Consensus & Live Channel Round-Trip**: Commit explicit file paths first, run `paf lint-code` and `paf verify --repo . --mode pre-push`, pass an isolated Blind Adversarial Review and record `paf record-review --repo . --verdict approve` on that exact `HEAD` SHA, open the PR via `paf ship --repo . --open-for-consensus --author-family <claude|gemini>` (adding `paf post-consensus --role author` when opening a new PR under `require_hmac: true`), obtain heterogeneous family approval via `paf watch-prs` or `paf post-consensus --role reviewer`, convert every defect caught into a permanent test or static rule, merge via `paf merge-consensus --repo . --pr <num>`, verify deployed image SHA plus live inbound/outbound channel round-trip, and prune merged local branches via `paf garden --repo . --prune-merged-branches`.

## Execution Entrypoints

- **New Personal Repository (Workflow A)**: Follow `$FACTORY/framework/HIGH-TRUST-REPO.md` Section 2 (Steps 1-6) from the initial commit.
- **Existing Repository Audit & Slimdown (Workflow B)**: Follow `$FACTORY/framework/HIGH-TRUST-REPO.md` Section 3 (Phases 0-6), starting with a `git fetch` sync, baseline file/LOC snapshot, and a control-group-calibrated production traffic query before removing any service or route.
