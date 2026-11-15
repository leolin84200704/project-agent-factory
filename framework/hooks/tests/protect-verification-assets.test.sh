#!/usr/bin/env bash
# Behavioral tests for framework/hooks/protect-verification-assets.sh
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$HOOK_DIR/protect-verification-assets.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir -p "$TMP_ROOT/repo/.github/workflows" "$TMP_ROOT/repo/githooks" "$TMP_ROOT/repo/hooks" "$TMP_ROOT/repo/src"
echo '{}' > "$TMP_ROOT/repo/.agent-verify.json"
echo '#!/usr/bin/env bash' > "$TMP_ROOT/repo/githooks/pre-push"
echo 'export const x = 1;' > "$TMP_ROOT/repo/src/app.ts"

SESSION="test-session-$$"
FAIL=0

run_hook() {
  local cwd="$1"
  local fpath="$2"
  local auto_flag="${3:-0}"
  PAF_AUTONOMOUS="$auto_flag" bash "$HOOK" <<EOF
{"session_id":"$SESSION","cwd":"$cwd","tool_input":{"file_path":"$fpath"}}
EOF
}

# 1. Unprotected source file -> allowed (exit 0)
if run_hook "$TMP_ROOT/repo" "src/app.ts" 0 >/dev/null 2>&1; then
  echo "PASS unprotected source file allowed"
else
  echo "FAIL unprotected source file blocked"
  FAIL=1
fi

# 2. Brand-new verification asset (does not exist yet) -> allowed (exit 0)
if run_hook "$TMP_ROOT/repo" ".github/workflows/new-ci.yml" 0 >/dev/null 2>&1; then
  echo "PASS brand-new verification asset allowed"
else
  echo "FAIL brand-new verification asset blocked"
  FAIL=1
fi

# 3. Existing relative path `.agent-verify.json` -> first attempt paused (exit 2), retry allowed (exit 0)
set +e
run_hook "$TMP_ROOT/repo" ".agent-verify.json" 0 >/dev/null 2>&1
rc1=$?
run_hook "$TMP_ROOT/repo" "$TMP_ROOT/repo/.agent-verify.json" 0 >/dev/null 2>&1
rc2=$?
set -e
if [[ "$rc1" -eq 2 && "$rc2" -eq 0 ]]; then
  echo "PASS relative .agent-verify.json normalized, paused once, then allowed on retry"
else
  echo "FAIL relative .agent-verify.json: rc1=$rc1 rc2=$rc2 (expected 2 then 0)"
  FAIL=1
fi

# 4. Existing `../`-traversed githook `hooks/../githooks/pre-push` -> paused (exit 2)
set +e
run_hook "$TMP_ROOT/repo" "hooks/../githooks/pre-push" 0 >/dev/null 2>&1
rc_gh=$?
set -e
if [[ "$rc_gh" -eq 2 ]]; then
  echo "PASS ../ traversal into githooks/pre-push caught and paused"
else
  echo "FAIL ../ traversal into githooks/pre-push: rc=$rc_gh (expected 2)"
  FAIL=1
fi

# 5. Under PAF_AUTONOMOUS=1, even an already-acknowledged asset is hard-blocked (exit 2)
set +e
run_hook "$TMP_ROOT/repo" ".agent-verify.json" 1 >/dev/null 2>&1
rc_auto=$?
set -e
if [[ "$rc_auto" -eq 2 ]]; then
  echo "PASS PAF_AUTONOMOUS=1 hard-blocks existing verification asset even after session marker"
else
  echo "FAIL PAF_AUTONOMOUS=1 did not block: rc=$rc_auto"
  FAIL=1
fi

exit "$FAIL"
