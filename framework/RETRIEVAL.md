---
title: Memory Retrieval Protocol (framework)
audience: Any LLM acting as one of Leo's work agents (Claude Code sessions, dream agent)
purpose: One protocol for "which layer to read, how deep to drill, when to stop, what to write back". Instances adapt L4 to their world (e.g. Jira / GitHub / prod for a job; GitHub issues for a side project).
---

# Memory Retrieval Protocol

## Why this file exists

Without a shared discipline for *which layer answers which question*, an LLM either:
- **over-reads** (loads every file every turn → token waste, slow), or
- **under-reads** (answers from stale text when a deeper layer holds ground truth → wrong answers)

Case-specific "always check X" rules do not scale. This protocol replaces them with one principle: *question shape determines depth*.

## The hierarchy

| Layer | Path (per instance) | Role | Mutation owner |
|---|---|---|---|
| **L1** | instance `CLAUDE.md` + this protocol | Role + work loop + this protocol | Leo |
| **L2** | `storage/short_term_memory/_index.md`, `long-term-memory/_index.md`, `archive/_index.md`, `journal/_index.md` | Scored routing tables — file lists, not bodies | Dream pipeline |
| **L3a** | `journal/YYYY-MM-DD-{slug}.md` | **Episodic** — what happened in a session: explored, ruled out, decided, why | Agent at end of non-trivial session |
| **L3b** | `storage/short_term_memory/{ticket}.md` | **Semantic / task** — current state + next actions for one ticket | Agent during work; dream during consolidation |
| **L3b** | `long-term-memory/{topic}.md` | **Semantic / rules** — distilled patterns, domain knowledge, repo gotchas | Dream (via L3a/STM → LTM distillation) |
| **L4** | Ticket system, GitHub (PR/branch state), repo working trees, prod systems | **Ground truth** — the world itself | External — agent reads only |

## Question → depth mapping

| Question shape | Stop at | Examples |
|---|---|---|
| Behavioral / preference | L1 (CLAUDE.md / AGENTS.md) | "How should I report?", "Branch naming?" |
| Background / pattern | L3b (LTM) | "How does subsystem X route?", "What's the convention for Y?" |
| **Current state of a ticket** | **L3b (STM) + L4 verification + `relations` sweep** | "What's the status of {ticket}?", "Did we push the fix?", "What's left?" |
| Why / how-decided | L3a (journal) → STM Decisions section | "Why Option A over B?", "What did we rule out?" |
| Designing next iteration | L3a + L3b + L4 | "Redesign the retry logic — what failed before?" |
| One-off chat | None | "ok", "got it" |

### Recall questions: grep before you answer（2026-08-15）

事實型 recall（模型名、id、label、排程時間、設定值、閾值）**不可憑印象回答，也不可只靠
index 路由直覺**：先 `grep -ri {問題的關鍵名詞} long-term-memory/`（連同 instance
CLAUDE.md），讀命中的段落並引用來源；沒命中才走 index 路由。LTM 檔的 frontmatter
`summary:` 與 `tags:` 就是為 grep 設計的 retrieval trigger（寫法見 dream.md Rules）。

動機：regression baseline（2026-08-15）抓到檢索非確定性 — 兩題 LTM 有明確答案的題目
k=1 時 fail、重跑即過。「有時會去查」對紀律類知識不夠，此規則把它變成「必查」。

### The L3→L4 rule

**Memory references the world; it does not contain the world.** When an STM file references an external artifact, the artifact is authoritative — never the STM text. Concretely, before answering any "current state" question:

1. **Ticket system**: fetch the ticket — status, assignee, latest comments. STM `status: active` means nothing if the ticket system says Done.
2. **Git/GitHub**: if STM says "pushed" / "awaiting review", check the branch/PR actually exists and its state.
3. **Repos**: if STM references code, the working tree is authoritative.
4. Reconcile: if STM is out of sync, **fix the STM in the same turn**, then answer.

A nightly reconcile script can do this in bulk, but per-question verification is still required — the pipeline may not have run (see Staleness check).

### The blocked-verdict rule

A `blocked`/waiting verdict is a claim about the world *at write time* — it has a shelf life, and its expiry is usually caused by a *different* item shipping. Verifying the asked-about item alone misses this (real case: a ticket's E2E stayed "blocked" in an agent's answers for days after the sibling ticket that removed the blocker had deployed — the agent verified the ticket's own status in the ticket system but never re-tested the blocker).

1. **Write side**: any blocked/waiting/deferred status MUST carry `unblock_when:` in frontmatter — a one-line testable condition plus how to test it. Causally-tied items get `relations:` edges (`unblocked_by` / `blocks` / `sibling`) — hand-curated, 0–3 per key, distinct from the auto-generated `links:` noise.
2. **Read side**: before answering a status question about X ("is X done?", "what's left?"), also read every item in X's `relations` whose `updated:` is newer than X's — a moved dependency invalidates cached verdicts.
3. **Repeat side**: never repeat a blocked verdict to the user without re-running its `unblock_when` test in the same turn. If the test is cheap (one API call, one query), just run it; if expensive, say the verdict's date and that it is unverified.

### Staleness check (session start — mandatory)

Memory pipelines die silently (real case: 2026-06-08 → 2026-07-01, three weeks unnoticed). At session start, after reading the L2 indexes, check the `Last updated:` line in `storage/short_term_memory/_index.md`:

- **> 3 days old** → tell Leo the dream pipeline appears down, before doing anything else. Suggest running the instance's dream script and checking its scheduler (launchd/cron).
- Treat all index scores as unreliable until the pipeline runs again; fall back to Grep + frontmatter `updated:` dates.

## Session bootstrap

1. Read instance `CLAUDE.md` (auto-loaded) + this file
2. Read L2 indexes (STM / LTM / journal) — routing tables only
3. Run the Staleness check above
4. **Do not preload L3.** Drill per question per the mapping above.

### Index maintenance

- `_index.md` files are fully generated by `scripts/memory_scoring.py` (dream
  Phase 4, or manually). Never hand-edit them.
- Each file's `summary:` / `tags:` frontmatter is what session-time grep hits —
  keep them current (writing rules in `dream.md` § Rules).
- To pin a file's score against the formula (e.g. an archived reference the
  formula over-ranks): set `score_frozen: true` + the intended `score:` +
  a `note:` explaining why. The scoring script honors the pin.

## Write-back protocol

| Event | Layer to write | Notes |
|---|---|---|
| Ticket state change during work | STM file directly | Agent owns; no permission needed |
| New rule / pattern derived from work | **Journal (L3a) only** — dream distills to LTM | Do NOT write LTM directly mid-session; the double-write path (agent + dream both writing LTM) caused duplication |
| L4 check found STM out-of-sync | Fix STM in same turn | Note discrepancy in journal entry |
| Leo gives behavioral correction | Personal `AGENTS.md`（本檔所在 factory repo）if universal; instance CLAUDE.md if job-specific; + journal | Never require the same correction twice |
| Universal engineering lesson (not job-specific) | `framework/ENGINEERING-LESSONS.md`（本檔所在 factory repo）— Leo-confirmed edits commit direct; instance proposals go through a lesson PR (see `CONTRIBUTING.md`) | Keeps portable lessons out of the job instance |
| End of non-trivial session | Journal entry (below) | |

## Session journaling (L3a)

A session is **non-trivial** if any of: ≥10 tool calls of real work; any STM/LTM/CLAUDE.md/dream mutation; a decision about future agent behavior; a user correction.

Write to `journal/YYYY-MM-DD-{slug}.md`:

```markdown
---
date: YYYY-MM-DD
slug: short-kebab-summary
related_tickets: [XX-xxxxx]
distilled: false        # dream sets true after distillation
---

# {Topic in one line}

## Context
## What we explored     ← include things ruled out + why (highest-value section)
## Decisions            ← what was chosen + why, with Leo's quote if it drove the call
## Files touched
## Open questions / followups
## User feedback this session
```

Cap ~200 lines. Journal is the compressed reasoning trace, not a play-by-play — the ticket report already exists in STM.

## Native harness auto memory（決策 2026-08-15）

Claude Code v2.1.33+ 內建 per-cwd auto memory（`~/.claude/projects/{slug}/memory/`，
每 session 自動載入 MEMORY.md 前 200 行，模型自行寫入）。**在有 instance memory
系統的 repo 與其 workspace，一律 `autoMemoryEnabled: false` 關閉**（instance
`.claude/settings.json` + workspace `.claude/settings.local.json`；template 已內建）。

理由（自家 dream ≥ 原生，實測比較 2026-08-15）：
1. 原生無 consolidation 保證（所謂 Auto Dream 官方文件不存在）、無 L4 ground-truth
   reconcile、無 scoring/decay、無跨 instance lesson 傳播 — 這些自家 pipeline 都有。
2. Store 在 `~/.claude` 之下：不進 instance git、不可攜、受 harness 行為與政策變動影響。
3. 兩套並存的實害已發生：同一教訓三份 copy（原生 + instance CLAUDE.md + factory
   lessons）；另有 Leo 行為規則只被原生抓到、instance memory 全然不知（divergence）。
4. 每 session 200 行 context tax，疊在 instance CLAUDE.md + indexes 之上。

原生的唯一優勢是 mid-session 隨手捕捉（不靠 session-end 紀律）— 弱點由 journal
紀律 + AGENTS.md 的 auto-learner 協議補償。沒有 instance memory 系統的一般專案
不在此限，原生 auto memory 可留用。headless dream run 一律
`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`（run-dream.sh 已內建），避免 shadow store。

## Anti-patterns this protocol replaces

1. Trusting STM `status:` without checking the ticket system → L3→L4 rule
2. Writing LTM directly mid-session → journal, let dream distill
3. Case-specific "always check X" rules in CLAUDE.md → instances belong here
4. Assuming the dream pipeline ran → Staleness check
5. Repeating a cached "blocked" verdict after its dependency shipped → Blocked-verdict rule (`unblock_when` re-test + `relations` sweep)
