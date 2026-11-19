#!/bin/bash
# Exercise require-factcheck-receipt.sh.
# Run: bash framework/hooks/tests/require-factcheck-receipt.test.sh

HOOK="$(cd "$(dirname "$0")/.." && pwd)/require-factcheck-receipt.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"
export PAF_FACTCHECK_DIR="$TMP/receipts"
mkdir -p "$PAF_FACTCHECK_DIR"

fails=0; n=0
run() {  # run <expected_exit> <label> <tool_name> <tool_input-json> [mode]
  local want="$1" label="$2" tool="$3" ti="$4" mode="${5:-block}" got mark='ok  '
  n=$((n+1))
  jq -cn --arg t "$tool" --argjson ti "$ti" --arg s "sess-$n" \
    '{session_id:$s, tool_name:$t, tool_input:$ti}' | PAF_FACTCHECK_MODE="$mode" "$HOOK" >/dev/null 2>&1; got=$?
  if [ "$got" != "$want" ]; then mark='FAIL'; fails=$((fails+1)); fi
  printf '[%s] want=%s got=%s | %s\n' "$mark" "$want" "$got" "$label"
}
receipt() {  # receipt <name> <status> <target>
  jq -n --arg s "$2" --arg t "$3" '{version:1,status:$s,target:$t}' > "$PAF_FACTCHECK_DIR/$1.json"
}

SLACK=mcp__claude_ai_Slack__slack_send_message_draft
echo "--- not gated: ALLOWED ---"
run 0 'read tool'                  mcp__claude_ai_Slack__slack_read_channel '{"channel_id":"C1"}'
run 0 'read-only SQL via MCP'      mcp__vibrant__lisportal_mysql_query '{"query":"SELECT * FROM clinic WHERE clinic_id=1"}'
run 0 'plain bash'                 Bash '{"command":"ls -la"}'
run 0 'grep for a write verb'      Bash '{"command":"grep -n \"DELETE FROM\" audit.sql"}'
run 0 'gh pr view'                 Bash '{"command":"gh pr view 12"}'
run 0 'mode off'                   "$SLACK" '{"channel_id":"C1","message":"hi"}' off

echo "--- no receipt, block mode: BLOCKED ---"
run 2 'slack draft'                "$SLACK" '{"channel_id":"C1","message":"use 48875"}'
run 2 'jira comment'               mcp__claude_ai_Atlassian_MCP__addOrEditJiraIssueComment '{"issueIdOrKey":"VP-1","commentBody":"x"}'
run 2 'outlook draft'              mcp__claude_ai_Microsoft_365__outlook_create_draft '{"body":"x"}'
run 2 'write SQL via MCP'          mcp__vibrant__mysql_query '{"query":"UPDATE t SET a=1 WHERE id=2"}'
run 2 'gh pr create'               Bash '{"command":"gh pr create --title x --body y"}'
run 2 'mysql client with INSERT'   Bash '{"command":"mysql -h h -e \"INSERT INTO t VALUES (1)\""}'
run 2 'write in heredoc body'      Bash '{"command":"python3 - <<PY\ncur.execute(\"DELETE FROM t WHERE id=1\")\nPY"}'

echo "--- receipts ---"
receipt r-fail fail "slack:C2"
run 2 'failing receipt ignored'    "$SLACK" '{"channel_id":"C2","message":"x"}'
receipt r-other pass "slack:C9"
run 2 'receipt for another target' "$SLACK" '{"channel_id":"C3","message":"x"}'
receipt r-ok pass "slack:C4"
run 0 'matching receipt (kind:id)' "$SLACK" '{"channel_id":"C4","message":"x"}'
receipt r-jira pass "VP-77"
run 0 'matching receipt (bare key)' mcp__claude_ai_Atlassian_MCP__addOrEditJiraIssueComment '{"issueIdOrKey":"VP-77","commentBody":"x"}'
receipt r-old pass "slack:C5"; touch -t 202001010000 "$PAF_FACTCHECK_DIR/r-old.json"
run 2 'stale receipt ignored'      "$SLACK" '{"channel_id":"C5","message":"x"}'

echo "--- warn mode: pauses once, re-run passes ---"
P=$(jq -cn --arg t "$SLACK" '{session_id:"w", tool_name:$t, tool_input:{channel_id:"C6",message:"x"}}')
printf '%s' "$P" | PAF_FACTCHECK_MODE=warn "$HOOK" >/dev/null 2>&1; a=$?
printf '%s' "$P" | PAF_FACTCHECK_MODE=warn "$HOOK" >/dev/null 2>&1; b=$?
if [ "$a" = 2 ] && [ "$b" = 0 ]; then echo "[ok  ] first=2 second=0"; else echo "[FAIL] first=$a second=$b"; fails=$((fails+1)); fi

echo "--- decisions are logged ---"
for d in pass paused override blocked; do
  if grep -q "\"decision\":\"$d\"" "$PAF_FACTCHECK_DIR/hook-log.jsonl"; then echo "[ok  ] logged $d"; else echo "[FAIL] no $d in log"; fails=$((fails+1)); fi
done

echo; [ "$fails" = 0 ] && echo "ALL PASS" || { echo "$fails FAILED"; exit 1; }
