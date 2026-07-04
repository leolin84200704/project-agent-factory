# Subagent Patterns

How the work loop (WORK-LOOP.md) uses Claude Code subagents. These are patterns, not runtime-registered agent types.

## explore (Work Loop Step 2)
Read-only Explore subagent: given a ticket, sweep the relevant repos for the files, configs, and existing patterns the change will touch. Returns conclusions, not file dumps. Always used before proposing a solution.

## debate pair (Work Loop Step 3 — gated)
Two subagents launched in parallel, only for non-routine work (code changes, new patterns, incidents, prod-data impact):
- **正方**: defends the recommended approach — merits, feasibility
- **反方**: attacks it — risks, edge cases, simpler alternatives

Routine config tickets that exactly follow an existing pattern skip the debate and cite the past ticket whose pattern they follow.

## scan (on request)
Scans the instance's ticket system for assigned tickets and produces a prioritized summary (priority + deadline). Read-only; does not create STM files — STM is created only when work on a ticket actually starts.

## Domain knowledge
Instance-specific business rules live in the instance's `long-term-memory/` — see the instance CLAUDE.md's LTM routing table.
