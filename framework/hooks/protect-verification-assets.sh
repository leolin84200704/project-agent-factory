#!/bin/bash
# PreToolUse guard for verification assets (eval tasks, hooks, githooks, graders).
#
# Rationale (ENFORCEMENT-LADDER.md): these files are the agent's reward
# signal — the things that judge the agent's own work. An agent editing an
# existing expect-regex, hook, or grader can turn a fail into a pass with no
# one noticing (same-author code+test has weak evidential value).
#
# Policy: ADDING a new asset is free (additions are cheap to review).
# MODIFYING an existing asset pauses once per session+file (exit 2) and
# requires stating explicit human approval, then the retry passes.
# Under PAF_AUTONOMOUS=1, modifying existing verification assets is hard-blocked.

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
SESSION=$(echo "$INPUT" | jq -r '.session_id // "nosession"')
[ -z "$FILE" ] && exit 0

BASE_DIR="${CWD:-$PWD}"
ABS_FILE=$(python3 -c 'import os, sys; base, p = sys.argv[1], sys.argv[2]; print(os.path.abspath(p if os.path.isabs(p) else os.path.join(base, p)))' "$BASE_DIR" "$FILE")

PROTECTED_RE='(/eval-tasks/[^/]+\.md$|/hooks/[^/]+\.sh$|/githooks/(pre-commit|pre-push|install\.sh|lib/[^/]+\.sh)$|/run-regression\.py$|/\.agent-verify\.json$|/\.github/workflows/[^/]+\.ya?ml$|/bin/paf$|/dune_lint\.py$|/paf_watcher\.py$|/\.config/paf/consensus\.key$)'
echo "$ABS_FILE" | grep -qE "$PROTECTED_RE" || exit 0

# New file -> allowed. What needs protection is mutation of existing assertions.
[ ! -e "$ABS_FILE" ] && exit 0

# In unattended autonomous loops (PAF_AUTONOMOUS=1), never allow self-modifying
# verification contracts, workflows, or hooks to turn red gates green.
if [ "${PAF_AUTONOMOUS:-0}" = "1" ]; then
  echo "BLOCKED (PAF_AUTONOMOUS=1): autonomous loops may not modify existing verification asset $ABS_FILE." >&2
  exit 2
fi

KEY=$(printf '%s' "$ABS_FILE" | shasum | cut -c1-12)
MARKER="${TMPDIR:-/tmp}/verif-asset-ok-${SESSION}-${KEY}"
[ -f "$MARKER" ] && exit 0
touch "$MARKER"

cat >&2 <<EOF
PAUSED (once per file per session): $ABS_FILE is a verification asset — part of
the reward signal that judges the agent's own work (eval task / hook / grader).
Modifying existing assertions weakens the evidence unless a human approved it.
If Leo explicitly approved this exact change, state so and re-run — it will
pass. Otherwise stop and propose the change instead of applying it.
After any eval-task/grader edit, re-validate the grader:
  python3 scripts/run-regression.py --check {task-id} --answer "{known-good}"
EOF
exit 2
