# Tool Watchlist — cross-instance

> Third-party skills/tools that passed a scan's quality bar but are not yet adopted.
> Any instance's periodic tool scan may append here (via lesson PR). One entry per PR.
> Each entry must state the adoption trigger — a watchlist without triggers is a graveyard.
> Remove entries when adopted (move the how-to into the relevant instance docs) or when
> re-checked twice with no progress.

| Added | Instance | Tool | What it is | Why not adopted yet | Adoption trigger |
|---|---|---|---|---|---|
| 2026-08-18 | lis | [Hainrixz/claude-db](https://github.com/Hainrixz/claude-db) | DB schema audit skill: 23 audit modules with reproducible verification commands, read-only by default, plus lock-aware migration generation (concurrent index builds, `NOT VALID`+`VALIDATE` constraint splits, expand/contract column changes). Substantive implementation, not a prompt wrapper. | Techniques are Postgres idioms — limited value for manually-DDL'd MySQL services; project is early-stage (5 commits / 18 stars at review time). | Next large schema change on a Postgres-backed service: run its audit as a pre-review pass and evaluate signal-to-noise before deciding on adoption. |
