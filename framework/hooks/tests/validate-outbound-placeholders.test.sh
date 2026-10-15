#!/bin/bash
# Exercise validate-outbound-placeholders.sh.
# Run: bash framework/hooks/tests/validate-outbound-placeholders.test.sh

HOOK="$(cd "$(dirname "$0")/.." && pwd)/validate-outbound-placeholders.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"

fails=0; n=0
run() {  # run <expected_exit> <label> <tool_input-json>
  local want="$1" label="$2" ti="$3" out got mark='ok  '
  n=$((n+1))
  out=$(jq -cn --argjson ti "$ti" --arg s "sess-$n" '{session_id:$s, tool_input:$ti}' | "$HOOK" 2>&1); got=$?
  if [ "$got" != "$want" ]; then mark='FAIL'; fails=$((fails+1)); fi
  printf '[%s] want=%s got=%s | %s\n' "$mark" "$want" "$got" "$label"
}

echo "--- placeholders: PAUSED ---"
run 2 'Outlook body [TBD]'           '{"messageId":"AAk","bodyType":"html","body":"<p>Requisitions: [TBD]</p>"}'
run 2 'bare TBD'                     '{"body":"Go-live date is TBD."}'
run 2 'TODO in Jira comment'         '{"issueIdOrKey":"VP-1","commentBody":"TODO: confirm NPI"}'
run 2 'ellipsis bracket'             '{"body":"Hi [...], thanks"}'
run 2 'unicode ellipsis bracket'     '{"body":"Hi […], thanks"}'
run 2 '[insert date]'                '{"body":"Shipped on [insert date]."}'
run 2 '{{template var}}'             '{"text":"Hello {{name}}"}'
run 2 '<<fill>>'                     '{"message":"Folder: <<fill>>"}'
run 2 'subject placeholder'          '{"subject":"Re: XXX","body":"ok"}'

echo "--- clean text: ALLOWED ---"
run 0 'normal reply'                 '{"messageId":"AAk","body":"<p>The flow looks good to go.</p>"}'
run 0 'brackets that are not slots'  '{"body":"Send [MSH-5] as IN OFFICE; see item [2]."}'
run 0 'word containing tbd'          '{"body":"Outbound tbdx is not a slot"}'
run 0 'id fields ignored'            '{"messageId":"TODO-XXX-TBD","body":"Thanks"}'
run 0 'empty input'                  '{}'

echo "--- same text retried: passes the second time ---"
P='{"session_id":"retry","tool_input":{"body":"Literal TODO quoted from code"}}'
printf '%s' "$P" | "$HOOK" >/dev/null 2>&1; a=$?
printf '%s' "$P" | "$HOOK" >/dev/null 2>&1; b=$?
if [ "$a" = 2 ] && [ "$b" = 0 ]; then echo "[ok  ] first=2 second=0"; else echo "[FAIL] first=$a second=$b"; fails=$((fails+1)); fi

echo; [ "$fails" = 0 ] && echo "ALL PASS" || { echo "$fails FAILED"; exit 1; }
