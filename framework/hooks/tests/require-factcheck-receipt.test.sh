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
    '{session_id:$s, tool_name:$t, tool_input:$ti}' | PAF_FACTCHECK_MODE="$mode" PAF_FACTCHECK_REQUIRE_DUAL="${PAF_FACTCHECK_REQUIRE_DUAL:-0}" "$HOOK" >/dev/null 2>&1; got=$?
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
run 0 'cat heredoc into notes file' Bash '{"command":"cat >> stm.md <<EOF\n- the parser ran INSERT INTO t\nEOF"}'
run 0 'mode off'                   "$SLACK" '{"channel_id":"C1","message":"hi"}' off

echo "--- no receipt, block mode: BLOCKED ---"
run 2 'slack draft'                "$SLACK" '{"channel_id":"C1","message":"use 48875"}'
run 2 'jira comment'               mcp__claude_ai_Atlassian_MCP__addOrEditJiraIssueComment '{"issueIdOrKey":"VP-1","commentBody":"x"}'
run 2 'outlook draft'              mcp__claude_ai_Microsoft_365__outlook_create_draft '{"body":"x"}'
run 2 'write SQL via MCP'          mcp__vibrant__mysql_query '{"query":"UPDATE t SET a=1 WHERE id=2"}'
run 2 'gh pr create'               Bash '{"command":"gh pr create --title x --body y"}'
run 2 'mysql client with INSERT'   Bash '{"command":"mysql -h h -e \"INSERT INTO t VALUES (1)\""}'
run 2 'write in heredoc body'      Bash '{"command":"python3 - <<PY\ncur.execute(\"DELETE FROM t WHERE id=1\")\nPY"}'
run 2 'node heredoc with UPDATE'   Bash '{"command":"node <<JS\ndb.query(\"UPDATE t SET a=1 WHERE id=2\")\nJS"}'
run 2 'cat heredoc piped to mysql' Bash '{"command":"cat <<SQL | mysql -h h\nDELETE FROM t WHERE id=1\nSQL"}'

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

echo "--- require dual-model receipts ---"
KEY="$TMP/consensus.key"; printf 'test-secret-0123456789abcdef\n' > "$KEY"; chmod 600 "$KEY"
export PAF_CONSENSUS_KEY_FILE="$KEY"
signed_receipt() {  # signed_receipt <name> <target> <dual true|false> <verifier_family> [bad] [tier]
  python3 - "$PAF_FACTCHECK_DIR/$1.json" "$2" "$3" "$4" "${5:-}" "${6:-}" <<'PY'
import hashlib, hmac, json, sys
path, target, dual, fam, bad, tier = sys.argv[1:7]
r = {"version": 2, "status": "pass", "target": target, "dual_model": dual == "true",
     "verification": {"status": "pass" if dual == "true" else "fail", "family": fam, "author_family": "claude"}}
if tier:
    r["tier"] = tier
canon = json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
r["sig"] = "0" * 64 if bad else hmac.new(b"test-secret-0123456789abcdef", canon, hashlib.sha256).hexdigest()
open(path, "w").write(json.dumps(r))
PY
}
signed_receipt d-single C7 false gemini
PAF_FACTCHECK_REQUIRE_DUAL=1 run 2 'single-model receipt refused' "$SLACK" '{"channel_id":"C7","message":"x"}'
run 0 'single-model receipt ok when dual not required' "$SLACK" '{"channel_id":"C7","message":"x"}'
signed_receipt d-dual C8 true gemini
PAF_FACTCHECK_REQUIRE_DUAL=1 run 0 'dual receipt with valid sig' "$SLACK" '{"channel_id":"C8","message":"x"}'
signed_receipt d-same C9 true claude
PAF_FACTCHECK_REQUIRE_DUAL=1 run 2 'same-family verifier refused' "$SLACK" '{"channel_id":"C9","message":"x"}'
signed_receipt d-forged C10 true gemini bad
PAF_FACTCHECK_REQUIRE_DUAL=1 run 2 'bad signature refused' "$SLACK" '{"channel_id":"C10","message":"x"}'
signed_receipt d-std-db "db:orders" true gemini "" standard
PAF_FACTCHECK_REQUIRE_DUAL=1 run 2 'db-write refuses standard-tier receipt' mcp__vibrant__mysql_query '{"query":"UPDATE orders SET status=1 WHERE id=2"}'
if grep -q '"decision":"blocked".*"tier":"standard".*"required_tier":"high"' "$PAF_FACTCHECK_DIR/hook-log.jsonl"; then echo "[ok  ] log records rejected tier and required_tier"; else echo "[FAIL] log lacks rejected tier"; fails=$((fails+1)); fi
# Tampering with tier on an otherwise validly signed dual receipt invalidates HMAC.
python3 - "$PAF_FACTCHECK_DIR/d-std-db.json" "$PAF_FACTCHECK_DIR/d-tampered-tier.json" <<'PY'
import json, sys
r = json.loads(open(sys.argv[1]).read())
r["target"] = "db:tampered_tbl"
r["tier"] = "high"
open(sys.argv[2], "w").write(json.dumps(r))
PY
PAF_FACTCHECK_REQUIRE_DUAL=1 run 2 'tampered tier breaks HMAC' mcp__vibrant__mysql_query '{"query":"UPDATE tampered_tbl SET status=1 WHERE id=2"}'
signed_receipt d-high-db "db:orders" true gemini "" high
PAF_FACTCHECK_REQUIRE_DUAL=1 run 0 'db-write accepts high-tier receipt' mcp__vibrant__mysql_query '{"query":"UPDATE orders SET status=1 WHERE id=2"}'
# Setting min-tier explicitly requires dual-signed receipt of that tier even when REQUIRE_DUAL=0.
PAF_FACTCHECK_REQUIRE_DUAL=0 PAF_FACTCHECK_MIN_TIER_DB=high run 0 'explicit min-tier accepts high-tier receipt' mcp__vibrant__mysql_query '{"query":"UPDATE orders SET status=1 WHERE id=2"}'
signed_receipt d-spoof-tier "db:billing_accounts" false gemini "" high
PAF_FACTCHECK_REQUIRE_DUAL=0 PAF_FACTCHECK_MIN_TIER_DB=high run 2 'explicit min-tier refuses unsigned/single high-tier receipt' mcp__vibrant__mysql_query '{"query":"UPDATE billing_accounts SET status=1 WHERE id=2"}'
# Invalid min-tier typo fails closed instead of degrading to rank 0.
PAF_FACTCHECK_REQUIRE_DUAL=0 PAF_FACTCHECK_MIN_TIER_DB=hgh run 2 'invalid min-tier typo fails closed' mcp__vibrant__mysql_query '{"query":"UPDATE orders SET status=1 WHERE id=2"}'
if grep -q '"verification":"dual"' "$PAF_FACTCHECK_DIR/hook-log.jsonl"; then echo "[ok  ] log records dual"; else echo "[FAIL] log lacks dual"; fails=$((fails+1)); fi
if grep -q '"tier":"high"' "$PAF_FACTCHECK_DIR/hook-log.jsonl"; then echo "[ok  ] log records tier"; else echo "[FAIL] log lacks tier"; fails=$((fails+1)); fi

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
