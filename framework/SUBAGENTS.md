# Subagent Patterns

How the work loop (WORK-LOOP.md) uses Claude Code subagents. These are patterns, not runtime-registered agent types.

## explore (Work Loop Step 2)
Read-only Explore subagent: given a ticket, sweep the relevant repos for the files, configs, and existing patterns the change will touch. Returns conclusions, not file dumps. Always used before proposing a solution.

## debate trio (Work Loop Step 3 — gated)
Three subagents, only for non-routine work (code changes, new patterns, incidents, prod-data impact). Each gets a **different input** and must fetch its own evidence — same-model agents on the same input make correlated errors, so more voters does not add confidence:
- **正方** (input: ticket + plan): defends the approach; every feasibility claim cites `file:line` or data it read itself
- **反方** (input: plan + code, not the drafter's reasoning): attacks it — risks, edge cases, simpler alternatives, testability; makes at least one real falsification attempt (query, repro, counter-example row)
- **裁判** (input: the two reports + their evidence only): tags each claim `evidenced` / `inferred`; only `evidenced` claims count, `inferred` ones go to Step 4 as open items. Verdict is appended to the instance's debate ledger (see WORK-LOOP Step 3)

Routine config tickets that exactly follow an existing pattern skip the debate and cite the past ticket whose pattern they follow.

## scan (on request)
Scans the instance's ticket system for assigned tickets and produces a prioritized summary (priority + deadline). Read-only; does not create STM files — STM is created only when work on a ticket actually starts.

## Domain knowledge
Instance-specific business rules live in the instance's `long-term-memory/` — see the instance CLAUDE.md's LTM routing table.
