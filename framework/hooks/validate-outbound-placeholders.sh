#!/bin/bash
# PreToolUse guard on tools that put text in front of other people (Outlook
# drafts/sends, Teams, Slack, Jira comments): the text must not carry an
# unfilled placeholder such as [TBD], TODO or [...].
#
# Why a hook and not prose: the rule "no placeholders inside the outgoing body;
# open decisions belong in the report to Leo" was written down on 2026-10-13
# (INCIDENT-20261013 R7) and re-violated two days later, when an Outlook-ready
# draft to a vendor carried "Requisitions: [TBD]". A draft that looks finished
# gets sent; the placeholder then reaches the partner verbatim.
#
# Pauses once per distinct text (exit 2). Re-running the same call passes, so a
# deliberate literal (e.g. a Jira comment quoting code that contains TODO) is
# one justified retry away, not impossible.

INPUT=$(cat)
SESSION=$(echo "$INPUT" | jq -r '.session_id // "nosession"')
# Every string in tool_input except identifiers: body, comment, message, text,
# subject, whatever the tool calls it.
TEXT=$(echo "$INPUT" | jq -r '
  .tool_input // {} | paths(scalars) as $p
  | select(($p[-1] | tostring | test("(?i)(^|_)(id|ids|cloudId|messageId|channel|channel_id|thread_ts|issueIdOrKey|uri|url)$")) | not)
  | getpath($p) | strings')
[ -z "$TEXT" ] && exit 0

HIT=$(printf '%s\n' "$TEXT" | perl -ne '
  if (/\bTBD\b|\bTODO\b|\bFIXME\b|\bXXX+\b|\[\s*(\.\.\.|…)\s*\]|\[(insert|fill|placeholder|your |name of|date of|tbc)[^\]]*\]|<<[^>]*>>|\{\{[^}]*\}\}/i) {
    s/^\s+//; print substr($_, 0, 160); exit 0 }
  END { exit 1 }')
[ -z "$HIT" ] && exit 0

KEY=$(printf '%s' "$TEXT" | shasum | cut -c1-12)
MARKER="${TMPDIR:-/tmp}/outbound-placeholder-ok-${SESSION}-${KEY}"
[ -f "$MARKER" ] && exit 0
touch "$MARKER"

cat >&2 <<MSG
PAUSED: this text is about to reach other people and contains what looks like
an unfilled placeholder:
  ${HIT}
Open decisions go in the report to Leo, not inside the outgoing text
(INCIDENT-20261013 R7). Either resolve it (fill it in, or drop the sentence),
or, if the literal is intentional, say so and re-run the same call; it will pass.
MSG
exit 2
