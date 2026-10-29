#!/bin/bash
# Validate git push commands — block force push and push to protected branches.
# Called by Claude Code PreToolUse hook. Receives JSON on stdin.
#
# Protected-branch policy is REPO-AWARE and fail-safe:
#   - personal repos (the agent's own repos — see PERSONAL_REPOS below):
#       `main` is allowed; automation jobs and knowledge consolidation land
#       there. master/staging/develop stay blocked.
#   - every other repo (product / team-owned):
#       main/master/staging/develop ALL blocked. The agent may only push its own
#       feature/bugfix branches; promotion goes through a PR.
#   - if the target repo cannot be identified, default to the STRICT policy.
#
# The fail-safe default is the whole point. The previous version carried a single
# global policy with a comment saying "this is the personal repo, so main is
# allowed" — and BOOTSTRAP step 5 wires product repos straight at this file, so
# that comment silently removed `main` protection from every product repo on
# every machine. A guard whose policy depends on where it sits must RESOLVE
# where it is, never assume.
#
# Identity is resolved from the repo's origin URL, not its filesystem path: this
# one file is shared across machines whose clone layouts differ (~/src on one,
# ~/personal_project on another), so a path-keyed list would not be portable.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
[ -z "$COMMAND" ] && exit 0

# ── ADAPT PER INSTANCE ───────────────────────────────────────────
# Repos where pushing to `main` is allowed, matched against the origin URL's
# repo name. Listed centrally rather than edited per instance: this file is the
# single source wired by absolute path from several instances, so a local edit
# would be overwritten by the next factory pull. A new personal repo goes in
# this list; a one-off can be added for a session via AGENT_PERSONAL_REPOS.
PERSONAL_REPOS='project-agent-factory|agent-waves|vibrant-america-working-agent|industry-intel-code-agent|job-applier-code-agent|vibrant-america-auto'
[ -n "${AGENT_PERSONAL_REPOS:-}" ] && PERSONAL_REPOS="$PERSONAL_REPOS|$AGENT_PERSONAL_REPOS"
# Extra protected branch names beyond main/master/staging/develop — project
# staging branches with non-standard names. Leave empty if none.
EXTRA_PROTECTED='stage_test'
# ─────────────────────────────────────────────────────────────────

# Every check runs against the command segments that actually invoke `git push`,
# never against the whole command string. Splitting on shell separators is what
# keeps `rm -f x && git push` from reading as a force push, and what stops a bare
# `-f ` anywhere blocking ordinary `rm -f tmp.js`, `tail -f log`, `grep -f pats`.
#
# Two bugs the earlier scoping still had, both measured 2026-08-18:
#   - the protected-branch check matched `$COMMAND`, so ANY push was blocked as
#     soon as the word `main` appeared later in the command — a `gh pr create
#     --body` mentioning `git checkout main` was enough. And because a blocked
#     call is discarded whole, the `git commit` chained ahead of that push was
#     lost with it.
#   - `grep -o 'push[^;&|]*' | head -1` took the FIRST `push` in the command, so
#     `echo "about to push" && git push --force origin main` was checked against
#     the echo and sailed through. Collecting every git-push segment closes that.
#   - heredoc bodies were inspected as if they were commands, so a commit
#     message or PR body that quoted `git push --force` as an example blocked
#     the commit that carried it. Bodies are data; lib/command-text.sh drops them.
. "$(dirname "$0")/lib/command-text.sh"
PUSH_SEGS=$(command_shell_text "$COMMAND" | tr ';&|\n' '\n\n\n\n' \
  | grep -E '(^|[[:space:]])git([[:space:]]|$)' \
  | grep -E '(^|[[:space:]])push([[:space:]]|$)')

if [ -n "$PUSH_SEGS" ]; then
  # (a) explicit flags, (b) the `+refspec` form — `git push origin +main` and
  # `git push origin +refs/heads/main:refs/heads/main` are force pushes with no
  # flag anywhere in the command.
  if echo "$PUSH_SEGS" | grep -qE '(--force-with-lease|--force|[[:space:]]-f([[:space:]]|$))' \
     || echo "$PUSH_SEGS" | grep -qE '[[:space:]]\+[A-Za-z0-9_./*-]+(:[A-Za-z0-9_./*-]+)?([[:space:]]|$)'; then
    echo "BLOCKED: force push 不允許（含 --force / --force-with-lease / -f / +refspec）。" >&2
    exit 2
  fi
  # (c) --no-verify skips the shared git pre-push gate (framework/githooks/
  # pre-push: build + Nest DI boot smoke). That gate is the only mechanism
  # standing between "unit tests green" and "new pods crash-loop" (2026-09-10),
  # and the pre-push's own failure message names --no-verify as the skip — so
  # the agent must not be able to take it. A human at a terminal still can.
  if echo "$PUSH_SEGS" | grep -qE '(^|[[:space:]])--no-verify([[:space:]]|$)'; then
    echo "BLOCKED: git push --no-verify 會跳過 pre-push 守門（build + Nest DI smoke）。守門失敗就修好再推；真要跳過請 Leo 自己在終端執行。" >&2
    exit 2
  fi
fi

# Resolve the effective repo directory for this push.
# Priority: `git -C <path>` on the push itself > the LAST `cd <path>` that runs
# BEFORE the push > session cwd.
#
# Only a cd that precedes the push can move it. The earlier version took the
# first `cd` anywhere in the command, which went wrong on 2026-10-15: the
# harness drops a leading `cd <cwd> &&` that is a no-op before handing the
# command to this hook, so in `cd <repo> && git push origin main && cd $WT`
# the first cd the hook could see was the one AFTER the push. `$WT` does not
# resolve, the repo became "unidentified", and a legal main push to a personal
# repo was blocked as a product-repo push.
PUSH_C=$(echo "$PUSH_SEGS" | grep -oE 'git[[:space:]]+-C[[:space:]]+[^[:space:]]+' | head -1 | awk '{print $3}')
if [ -n "$PUSH_C" ]; then
  REPO_DIR="$PUSH_C"
else
  REPO_DIR=$(command_shell_text "$COMMAND" | tr ';&|\n' '\n\n\n\n' \
    | awk '/(^|[[:space:]])git([[:space:]]|$)/ && /(^|[[:space:]])push([[:space:]]|$)/ {exit} {print}' \
    | grep -oE '^[[:space:]]*cd[[:space:]]+[^[:space:]]+' | tail -1 | awk '{print $NF}')
fi
[ -z "$REPO_DIR" ] && REPO_DIR="$CWD"
REPO_DIR="${REPO_DIR/#\~/$HOME}"   # expand a leading ~

ORIGIN=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null)
# Compare the repo NAME, not the whole URL: an unrelated URL that merely
# contains a personal repo's name (a fork path, a mirror host, a vendored
# directory) must not be able to promote itself to the personal policy.
REPO_NAME=$(basename "${ORIGIN%.git}" 2>/dev/null)

# Protected-branch match: tolerant of flags/remote between `push` and the branch
# (so `git push -u origin main` is caught, not just `git push origin main`).
# The leading \s+ anchors the branch as its own token, so `release/main` is safe.
STRICT_NAMES="main|master|staging|develop${EXTRA_PROTECTED:+|$EXTRA_PROTECTED}"
PERSONAL_NAMES="master|staging|develop${EXTRA_PROTECTED:+|$EXTRA_PROTECTED}"
PERSONAL_BLOCK="push([[:space:]]+[^[:space:]]+)*[[:space:]]+($PERSONAL_NAMES)([[:space:]]|$|:)"
WORKREPO_BLOCK="push([[:space:]]+[^[:space:]]+)*[[:space:]]+($STRICT_NAMES)([[:space:]]|$|:)"

if [ -n "$REPO_NAME" ] && echo "$REPO_NAME" | grep -qE "^($PERSONAL_REPOS)$"; then
  # Personal repo: main allowed, master/staging/develop blocked.
  if echo "$PUSH_SEGS" | grep -qE "$PERSONAL_BLOCK"; then
    echo "BLOCKED: 不能直接 push 到 master/staging/develop。" >&2
    exit 2
  fi
else
  # Product repo, or a target we could not identify: block them all.
  if echo "$PUSH_SEGS" | grep -qE "$WORKREPO_BLOCK"; then
    echo "BLOCKED: 產品 repo 不允許直接 push main/master/staging（改開 PR，只能 push 自己的 feature/bugfix 分支）。" >&2
    exit 2
  fi
fi

exit 0
