---
name: ticket-requirements-clarify
description: Multi-round requirements clarification before coding an ambiguous ticket. Use BEFORE executing any code change when a Jira/ticket's scope, acceptance criteria, affected service, or expected behavior is unclear, under-specified, or could be interpreted multiple ways — or when the user says "幫我看這個 ticket", "需求不清", "要改什麼", or hands over a ticket without confirmed scope. Produces a structured analysis plus a ready-to-send English Jira comment drafting the open questions for the PM. Do not start writing code from this skill — its job is to remove ambiguity first.
---

# Ticket Requirements Clarification

Ambiguous tickets are the most expensive failure mode: you build the wrong thing, or quietly widen scope by guessing. The fix is cheap — surface the ambiguity and confirm with a human before touching code. This skill front-loads that.

This maps onto the Work Loop Steps 2–4 (Analyze → Debate → Discuss). Run it whenever the *what* is unclear, not the *how*.

## When NOT to use
If scope is already crisp and confirmed (clear acceptance criteria, one obvious service, no behavioral ambiguity), skip this — go straight to implementation planning. Clarifying an already-clear ticket just wastes the PM's time.

## Procedure

### 1. Read the ticket and the code, separately
First state what the ticket *literally says* (don't interpret yet). Then look at the relevant code/config to see what the system *actually does today*. Most ambiguity hides in the gap between the two.

### 2. Enumerate the ambiguities
For each unclear point, write:
- **The question** — concrete, answerable
- **Why it matters** — what changes in the implementation depending on the answer (if nothing changes, it's not a real question — drop it)
- **Your best guess + confidence** — so the PM can just confirm/correct instead of writing an essay

Cover at minimum: exact scope boundary (what's in / explicitly out), affected service(s), expected behavior on edge cases (empty/null/legacy data, multiple matches, failures), and any prod-data or migration implications.

### 3. Self-debate the scope
Argue both sides briefly: the *minimal* interpretation (smallest change that satisfies the literal ticket) vs the *broad* interpretation (everything a reasonable reader might expect). When in doubt, recommend the minimal one — it is far cheaper to widen scope after confirmation than to unwind an over-built change. This is the "Confirm Before Over-engineering" principle.

### 4. Draft the PM comment (English)
Jira ticket comments go out in **English** (replies to the user stay zh-TW). Draft — never auto-post — a comment for the user to review and send. Keep it short and skimmable:

```
Before I start, a few things to confirm so I build the right scope:

1. <question> — my read is <guess>. Correct?
2. <question> — <guess>. Or should it also cover <X>?
3. <edge case>: what's the expected behavior when <condition>?

If the above is right, the change is limited to <service/area>. Let me know.
```

## Output to the user (zh-TW)
Present in this order, concisely:
1. **目的** — ticket 想解決什麼
2. **改前現狀** — 系統現在的行為（對著 code 講，不要憑印象）
3. **歧義點** — 編號清單，每點含「為何影響實作」+ 你的最佳猜測
4. **建議範圍** — minimal vs broad，給出推薦
5. **待 PM 確認的 Jira comment 草稿**（英文，等使用者過目再貼）

Then stop and wait. Do not begin implementation until the human confirms scope.
