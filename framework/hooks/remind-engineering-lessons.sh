#!/bin/bash
# PreToolUse reminder: risky DB/prod commands require reading the relevant
# ENGINEERING-LESSONS section first. Deterministic point-of-use enforcement —
# the lazy-load pointer in AGENTS.md ("動 DB 前先讀") was measurably ignored
# (canary test 2026-08-15), and instruction adherence decays with session
# length; hooks don't.
#
# Blocks the FIRST risky command per session (exit 2 with instructions),
# then stands down for the rest of the session (marker file keyed by
# session_id), so it reminds without nagging.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
SESSION=$(echo "$INPUT" | jq -r '.session_id // "nosession"')
[ -z "$COMMAND" ] && exit 0

RISKY_RE='(sqlite3|psql|mysql[^d]|UPDATE\s+.+\s+SET|DELETE\s+FROM|INSERT\s+INTO|DROP\s+TABLE|ALTER\s+TABLE|gcloud\s+(run|sql|scheduler)|kubectl\s+(apply|delete|rollout)|prisma\s+migrate)'
echo "$COMMAND" | grep -qiE "$RISKY_RE" || exit 0

# Read-only sqlite invocations are fine (heuristic: no write verbs anywhere)
if echo "$COMMAND" | grep -qiE '^(.*sqlite3[^;|&]*)$' \
   && ! echo "$COMMAND" | grep -qiE '(UPDATE|DELETE|INSERT|DROP|ALTER|REPLACE|VACUUM)'; then
  exit 0
fi

MARKER="${TMPDIR:-/tmp}/lessons-reminded-${SESSION}"
[ -f "$MARKER" ] && exit 0
touch "$MARKER"

cat >&2 <<'EOF'
PAUSED (once per session): this command touches a DB / prod surface.
Before re-running it, read the matching sections of
~/project-agent-factory/framework/ENGINEERING-LESSONS.md (驗證紀律 / 變更範圍紀律 /
設計判斷 — e.g. batch verify 100% never sample; DELETE WHERE bound to this
operation's scope; schema migration before deploy) and state which lessons
apply to what you are about to do. Then re-run the command — it will pass.
EOF
exit 2
