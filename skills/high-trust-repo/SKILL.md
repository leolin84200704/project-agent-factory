---
name: high-trust-repo
description: Scaffold a brand-new repository or audit and slim an existing repository into a High-Trust Environment using the 6 structural criteria, machine-validated FEATURE_MAP.md, layered CI guardrails, initialized boot smoke tests, and dual-model PR consensus.
---

# High-Trust Repository Architecture & Slimdown Skill

Use this skill when creating a new product repository from scratch, onboarding a repository during `BOOTSTRAP.md`, or auditing and slimming an existing repository into a High-Trust Environment.

Before making structural changes, read the full playbook in `$FACTORY/framework/HIGH-TRUST-REPO.md` (probe `~/src/project-agent-factory` then `~/personal_project/project-agent-factory` for `$FACTORY`).

## Ownership & Trust-Zone Gate

Check `.agent-verify.json` (or ask the user during initial bootstrap) before modifying any repository:
- **Personal repository (`zone: "autonomous"`)**: Execute greenfield scaffolding (Workflow A) or brownfield slimdown (Workflow B) directly on a feature branch while preserving 100% of live user-facing behavior, then ship via dual-model consensus.
- **Company or team repository (`zone: "guarded"`)**: Never add unapproved directory conventions, linters, or workflow files directly. Audit the repository against Criteria 1 through 6 and record concrete findings and proposed PRs in the instance's `long-term-memory/guardrail-gaps.md` for user approval.

## The 6 High-Trust Structural Criteria

1. **Feature Co-Location & 1:1 Naming**: Co-locate entrypoints, controllers, domain logic, schemas, and unit tests per feature with 1:1 file-to-symbol naming. Forbid direct cross-service source imports.
2. **Honest Thin Shared Primitives & Golden Example**: Keep shared packages minimal and truthfully named so the shortest path to copy is the right architecture. Forbid misleading wrapper names (such as naming a relational table wrapper after a cache or columnar engine) and pass-through network proxy services. Attach idle `'error'` listeners and explicit `max` bounds to every database pool, and serialize cold-start idempotent DDL with an advisory lock.
3. **Machine-Validated `FEATURE_MAP.md`**: Document active packages, routes/triggers, agent tools, and canonical SQL tables in `FEATURE_MAP.md`. Enforce bidirectional 1:1 parity against source decorators and SQL DDL via `scripts/check-architecture.*` with explicit minimum match count floors, verified by injecting a negative counterexample.
4. **Hard Static & CI Guardrails (`ENFORCEMENT-LADDER` Tiers 1-6)**: Wire `.agent-verify.json` (`dune_lint` enabled with file-specific `cjk_allow_globs`) and matching `.github/workflows/ci.yml`. Scan both `src/` and `test/` to block cross-service imports, retired packages, `--passWithNoTests`, and unpinned `*-latest` model aliases.
5. **Closed-Loop Execution & Initialized Boot Smoke**: Cover TTL expiry across every backed data structure, wildcard escaping, zero/negative bounds, and transient vs 4xx retry behavior in unit tests. Run full container lifecycle initialization (such as `await moduleRef.init()`) with isolated test env vars in every service's boot smoke spec.
6. **Adversarial Dual-Model Consensus & Live Channel Round-Trip**: Run `paf lint-code` and `paf verify --mode pre-push`, pass an isolated Blind Adversarial Review (`paf record-review`), converge `claude` and `gemini` consensus via `paf ship --open-for-consensus`, convert every defect caught into a permanent test or static rule, merge with `--match-head-commit`, and verify deployed image SHA plus live inbound/outbound channel round-trip before deleting retired cloud resources.

## Execution Entrypoints

- **New Repository (Workflow A)**: Follow `$FACTORY/framework/HIGH-TRUST-REPO.md` Section 2 (Steps 1-6) before opening the initial PR.
- **Existing Repository Audit & Slimdown (Workflow B)**: Follow `$FACTORY/framework/HIGH-TRUST-REPO.md` Section 3 (Phases 0-6), starting with a `git fetch` sync, baseline file/LOC snapshot, and a control-group-calibrated production traffic query before removing any service or route.
