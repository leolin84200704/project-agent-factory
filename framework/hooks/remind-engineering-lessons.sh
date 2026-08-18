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
#
# Matching rule (tightened 2026-08-18 after a measured false positive: a
# read-only analysis script was blocked because a regex literal *inside its
# heredoc* contained the string "mysql|psql" — one wasted round-trip, and the
# blocked call's heredoc never landed, so the whole command had to be retyped).
# Heredoc bodies are DATA, not commands, so the two halves get different rules:
#   shell text   -> a DB/prod CLI actually invoked in command position
#   heredoc body -> only a real write statement (a heredoc IS how SQL and inline
#                   scripts get fed to a client, so bodies still matter)
# Deliberate gap: a write statement that appears only in shell text without any
# DB client (`grep "DELETE FROM" audit.sql`) no longer fires. Nothing risky is
# reachable that way, and it was the other half of the false-positive surface.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
SESSION=$(echo "$INPUT" | jq -r '.session_id // "nosession"')
[ -z "$COMMAND" ] && exit 0

# Split the command into shell text (heredoc bodies removed) and the bodies.
# Quotes are written as \047 / \042 so the awk program needs no escaping.
split_command() {  # split_command shell|body
  printf '%s\n' "$COMMAND" | awk -v mode="$1" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
    {
      if (delim != "") {
        if (trim($0) == delim) { delim = ""; next }
        if (mode == "body") print
        next
      }
      if (match($0, /<<-?[ \t]*[\047\042]?[A-Za-z_][A-Za-z0-9_]*[\047\042]?/)) {
        d = substr($0, RSTART, RLENGTH)
        sub(/^<<-?[ \t]*/, "", d)
        gsub(/[\047\042]/, "", d)
        delim = d
      }
      if (mode == "shell") print
    }'
}

SHELL_TEXT=$(split_command shell)
BODY_TEXT=$(split_command body)

# Command position: start of input, or after a pipe/separator/subshell, allowing
# sudo and leading VAR=value assignments. Word end is spelled with a trailing
# class rather than \b, which is not portable across BSD and GNU grep.
CMD_POS='(^|[|;&`(]|\$\()[[:space:]]*(sudo[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
DB_CLI="${CMD_POS}(sqlite3|psql|mysql|mysqldump|mongosh|mongo|redis-cli)([[:space:]]|$)"
INFRA="${CMD_POS}(gcloud[[:space:]]+(run|sql|scheduler)|kubectl[[:space:]]+(apply|delete|rollout)|(npx[[:space:]]+)?prisma[[:space:]]+migrate)([[:space:]]|$)"
WRITE_RE='(UPDATE[[:space:]]+.+[[:space:]]+SET|DELETE[[:space:]]+FROM|INSERT[[:space:]]+INTO|DROP[[:space:]]+TABLE|ALTER[[:space:]]+TABLE|TRUNCATE[[:space:]]+TABLE)'

RISKY=0
printf '%s\n' "$SHELL_TEXT" | grep -qiE "$DB_CLI" && RISKY=1
printf '%s\n' "$SHELL_TEXT" | grep -qiE "$INFRA"  && RISKY=1
printf '%s\n' "$BODY_TEXT"  | grep -qiE "$WRITE_RE" && RISKY=1
[ "$RISKY" = 0 ] && exit 0

# Read-only sqlite invocations are fine (heuristic: no write verbs anywhere).
# Deliberately not extended to psql/mysql: those reach shared and prod databases,
# where even a read is worth one reminder per session.
OTHER_CLI="${CMD_POS}(psql|mysql|mysqldump|mongosh|mongo|redis-cli)([[:space:]]|$)"
if printf '%s\n' "$SHELL_TEXT" | grep -qiE "${CMD_POS}sqlite3([[:space:]]|$)" \
   && ! printf '%s\n' "$SHELL_TEXT" | grep -qiE "$OTHER_CLI" \
   && ! printf '%s\n' "$SHELL_TEXT" | grep -qiE "$INFRA" \
   && ! printf '%s\n%s\n' "$SHELL_TEXT" "$BODY_TEXT" | grep -qiE '(UPDATE|DELETE|INSERT|DROP|ALTER|REPLACE|VACUUM)'; then
  exit 0
fi

MARKER="${TMPDIR:-/tmp}/lessons-reminded-${SESSION}"
[ -f "$MARKER" ] && exit 0
touch "$MARKER"

# Self-locate: this hook lives at <factory>/framework/hooks/, so the factory
# root is two levels up — machine-agnostic (clone location differs per machine).
FACTORY_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

cat >&2 <<EOF
PAUSED (once per session): this command touches a DB / prod surface.
Before re-running it, read the matching sections of
${FACTORY_ROOT}/framework/ENGINEERING-LESSONS.md (驗證紀律 / 變更範圍紀律 /
設計判斷 — e.g. batch verify 100% never sample; DELETE WHERE bound to this
operation's scope; schema migration before deploy) and state which lessons
apply to what you are about to do. Then re-run the command — it will pass.
EOF
exit 2
