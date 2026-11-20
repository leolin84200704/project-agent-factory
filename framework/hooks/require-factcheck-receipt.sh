#!/bin/bash
# PreToolUse gate: outbound text and DB writes need a passing fact-check receipt
# from `paf factcheck` (framework/FACT-CHECK.md, AGENTS.md principle 6).
#
# Why a hook: by 2026-11-18 the repo held more than a hundred prose rules about
# verifying facts, and wrong facts still went out. The OptiMantra practice-ID
# list (VP-19079) was built from a label column and an inference, and it was sent
# that day. Prose rules are the weakest tier of the enforcement ladder; this hook
# checks, at the point of sending, that the facts were run through a tool that
# actually returned data.
#
# What counts as "outbound" or "DB write":
#   - MCP tools that put text in front of people: Slack send/draft/schedule,
#     Outlook draft/send/forward, Teams, Jira comment/create/edit, Confluence
#     create/update.
#   - Bash: `gh pr|issue create|comment|edit|review`; a DB client invoked with
#     a write statement, or a write statement inside a heredoc body that feeds an
#     interpreter (python, node, ruby, perl, ...). A heredoc written to a file with
#     cat is prose, not a write.
#   - MCP SQL tools (*mysql_query*) whose SQL is a write statement.
#
# A receipt matches when it passed, is younger than PAF_FACTCHECK_MAX_AGE_MIN
# (default 120) minutes, and its "target" string (or the part after "kind:")
# appears in the tool call. Receipts live in PAF_FACTCHECK_DIR
# (default ~/.cache/paf/factcheck).
#
# PAF_FACTCHECK_REQUIRE_DUAL=1 accepts only receipts that passed dual-model
# verification (`paf factcheck --verify`): verifier family differs from the
# author's, and the receipt's HMAC (consensus key) is valid. Every log line
# records whether the matching receipt was dual or single.
#
# Modes (PAF_FACTCHECK_MODE):
#   warn  (default) pause once per session and target with instructions; the same
#         call re-run passes and is logged as an override. Use this for the
#         measurement period before switching to block.
#   block every call without a matching receipt is refused.
#   off   no-op.
# Every decision is appended to hook-log.jsonl in the receipt directory, so the
# warn period yields counts of hits, overrides and matches.

MODE="${PAF_FACTCHECK_MODE:-warn}"
[ "$MODE" = "off" ] && exit 0

INPUT=$(cat)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // ""')
SESSION=$(printf '%s' "$INPUT" | jq -r '.session_id // "nosession"')
TI=$(printf '%s' "$INPUT" | jq -c '.tool_input // {}')

WRITE_RE='(UPDATE[[:space:]]+.+[[:space:]]+SET|DELETE[[:space:]]+FROM|INSERT[[:space:]]+INTO|REPLACE[[:space:]]+INTO|DROP[[:space:]]+TABLE|ALTER[[:space:]]+TABLE|TRUNCATE[[:space:]]+TABLE|CREATE[[:space:]]+TABLE)'
KIND=""

case "$TOOL" in
  *slack_send_message|*slack_send_message_draft|*slack_schedule_message|\
  *outlook_create_draft|*outlook_create_reply_draft|*outlook_create_reply_all_draft|*outlook_update_draft|*outlook_send_mail|*outlook_send_draft|*outlook_forward_mail|\
  *teams_send_chat_message|*teams_send_channel_message|*teams_reply_channel_message|\
  *addOrEditJiraIssueComment|*createJiraIssue|*editJiraIssue|*add_jira_comment|*update_jira_comment|*update_jira_issue|*create_jira_issue|\
  *createConfluenceContent|*updateConfluenceContent)
    KIND="outbound" ;;
  *mysql_query*)
    SQL=$(printf '%s' "$TI" | jq -r '[.. | strings] | join("\n")')
    printf '%s\n' "$SQL" | grep -qiE "$WRITE_RE" && KIND="db-write" ;;
  Bash)
    COMMAND=$(printf '%s' "$TI" | jq -r '.command // ""')
    . "$(dirname "$0")/lib/command-text.sh"
    SHELL_TEXT=$(command_shell_text "$COMMAND")
    BODY_TEXT=$(command_heredoc_bodies "$COMMAND")
    CMD_POS='(^|[|;&`(]|\$\()[[:space:]]*(sudo[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
    if printf '%s\n' "$SHELL_TEXT" | grep -qE "${CMD_POS}gh[[:space:]]+(pr|issue)[[:space:]]+(create|comment|edit|review)([[:space:]]|$)"; then
      KIND="outbound"
    elif printf '%s\n' "$SHELL_TEXT" | grep -qiE "${CMD_POS}(psql|mysql)([[:space:]]|$)" \
         && printf '%s\n%s\n' "$SHELL_TEXT" "$BODY_TEXT" | grep -qiE "$WRITE_RE"; then
      KIND="db-write"
    elif printf '%s\n' "$BODY_TEXT" | grep -qiE "$WRITE_RE" \
         && printf '%s\n' "$SHELL_TEXT" | grep -qiE "${CMD_POS}(python[0-9.]*|node|ts-node|npx|bun|deno|ruby|perl|php|sqlite3)([[:space:]]|$)"; then
      # A heredoc body only executes when it feeds an interpreter. `cat > notes.md <<EOF`
      # that merely names a write verb is prose (first live false positive, 2026-11-18).
      KIND="db-write"
    fi ;;
esac
[ -z "$KIND" ] && exit 0

DIR="${PAF_FACTCHECK_DIR:-$HOME/.cache/paf/factcheck}"
MAX_AGE="${PAF_FACTCHECK_MAX_AGE_MIN:-120}"
mkdir -p "$DIR" 2>/dev/null

REQUIRE_DUAL="${PAF_FACTCHECK_REQUIRE_DUAL:-0}"
DEFAULT_MIN_OUTBOUND="low"
DEFAULT_MIN_DB="low"
if [ "$REQUIRE_DUAL" = "1" ]; then
  DEFAULT_MIN_OUTBOUND="standard"
  DEFAULT_MIN_DB="high"
fi
MIN_TIER_OUTBOUND="${PAF_FACTCHECK_MIN_TIER_OUTBOUND:-$DEFAULT_MIN_OUTBOUND}"
MIN_TIER_DB="${PAF_FACTCHECK_MIN_TIER_DB:-$DEFAULT_MIN_DB}"
REQ_TIER="$MIN_TIER_OUTBOUND"
[ "$KIND" = "db-write" ] && REQ_TIER="$MIN_TIER_DB"

MATCH=$(DIR="$DIR" MAX_AGE="$MAX_AGE" TI="$TI" REQUIRE_DUAL="$REQUIRE_DUAL" REQ_TIER="$REQ_TIER" python3 - <<'PY'
import hashlib, hmac, json, os, time
from pathlib import Path
d = Path(os.environ["DIR"]); max_age = float(os.environ["MAX_AGE"]) * 60
ti = os.environ["TI"]; require_dual = os.environ["REQUIRE_DUAL"] == "1"
tier_rank = {"low": 0, "standard": 1, "high": 2}
req_tier = os.environ.get("REQ_TIER", "low").strip().lower()
req_rank = tier_rank.get(req_tier, 0)
key_file = Path(os.environ.get("PAF_CONSENSUS_KEY_FILE") or Path.home() / ".config/paf/consensus.key")
try:
    secret = key_file.read_text().strip()
except OSError:
    secret = ""

def dual_ok(r):
    ver = r.get("verification") or {}
    if not (r.get("dual_model") and ver.get("status") == "pass"):
        return False
    if not ver.get("family") or ver.get("family") == ver.get("author_family"):
        return False
    if not (secret and r.get("sig")):
        return False
    body = {k: v for k, v in r.items() if k not in ("sig", "receipt_path")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hmac.compare_digest(hmac.new(secret.encode(), canon, hashlib.sha256).hexdigest(), r["sig"])

def effective_tier(r, dual):
    raw = str(r.get("tier") or "").strip().lower()
    if raw in tier_rank:
        # Unverified or unsigned receipts never satisfy standard/high tiers.
        return raw if (raw == "low" or dual) else "low"
    if not dual:
        return "low"
    calls = (r.get("verification") or {}).get("calls") or []
    return "high" if any(c.get("step") == "blind" for c in calls if isinstance(c, dict)) else "standard"

now = time.time()
for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
    if now - p.stat().st_mtime > max_age:
        break
    try:
        r = json.loads(p.read_text())
    except Exception:
        continue
    if r.get("status") != "pass":
        continue
    t = str(r.get("target") or "").strip()
    keys = {t, t.split(":", 1)[-1]} - {""}
    if not any(k in ti for k in keys):
        continue
    dual = dual_ok(r)
    tier = effective_tier(r, dual)
    if (require_dual or req_rank > 0) and not dual:
        continue
    if tier_rank.get(tier, 0) < req_rank:
        continue
    print(f"{p.name}|{'dual' if dual else 'single'}|{tier}"); break
PY
)
IFS='|' read -r MATCH DUAL TIER <<< "$MATCH"

log() {  # log <decision>
  jq -cn --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg s "$SESSION" --arg t "$TOOL" \
     --arg k "$KIND" --arg m "$MODE" --arg d "$1" --arg r "$MATCH" --arg v "$DUAL" --arg tr "$TIER" \
     '{ts:$ts, session:$s, tool:$t, kind:$k, mode:$m, decision:$d, receipt:$r, verification:$v, tier:$tr}' \
     >> "$DIR/hook-log.jsonl" 2>/dev/null
}

if [ -n "$MATCH" ]; then
  log "pass"
  exit 0
fi

if [ "$MODE" = "warn" ]; then
  KEY=$(printf '%s|%s' "$TOOL" "$TI" | shasum | cut -c1-12)
  MARKER="${TMPDIR:-/tmp}/factcheck-warned-${SESSION}-${KEY}"
  if [ -f "$MARKER" ]; then
    log "override"
    exit 0
  fi
  touch "$MARKER"
  log "paused"
else
  log "blocked"
fi

FACTORY_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RECOMMENDED_TIER="standard"
[ "$KIND" = "db-write" ] && RECOMMENDED_TIER="high"
cat >&2 <<MSG
PAUSED: this ${KIND} has no passing fact-check receipt (AGENTS.md principle 6, required tier: ${REQ_TIER}).
Before sending text or writing data, list every fact it depends on in a claims
file and run:
  ${FACTORY_ROOT}/bin/paf factcheck <claims.json> --tier ${RECOMMENDED_TIER}
Each verified claim needs a predicate-filtered command (SELECT, grep, kubectl,
gh, API call) that returns data only when the claim is true. "None / missing /
broken" claims need a control on a known positive. Identity and still-in-use
claims need an independent cross-check.
Set "target" to the channel id, issue key, PR or table so this hook can match it.
Use --tier standard for outbound text (audit + counterexamples) and --tier high
for DB writes or batch lists (blind + audit + counterexamples).
See ${FACTORY_ROOT}/framework/FACT-CHECK.md.
MSG
if [ "$MODE" = "warn" ]; then
  echo "Warn mode: if every fact here is already receipted or the text states no facts, say so and re-run the same call; it will pass and be logged as an override." >&2
fi
exit 2
