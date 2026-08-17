#!/usr/bin/env bash
# Wire the shared git hooks. Idempotent.
#
#   bash framework/githooks/install.sh            # global (recommended)
#   bash framework/githooks/install.sh <repo-dir> # this repo only
#
# Global is the point: the reason these hooks moved out of `.git/hooks/` is that
# `.git/hooks/` does not survive a fresh clone. A per-repo `core.hooksPath` has
# exactly the same problem — it lives in `.git/config`. Only the global setting
# is clone-proof, which is why the hooks are written to be repo-aware and to
# chain to whatever the repo installs for itself.
set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$HOOKS_DIR"/pre-commit "$HOOKS_DIR"/pre-push

if [ $# -eq 0 ]; then
  prev="$(git config --global core.hooksPath || true)"
  if [ -n "$prev" ] && [ "$prev" != "$HOOKS_DIR" ]; then
    echo "NOTE: global core.hooksPath was already set to: $prev"
    echo "      Overwriting it would disable whatever that provided. Aborting."
    echo "      Point that directory's hooks at $HOOKS_DIR, or install per-repo instead."
    exit 1
  fi
  git config --global core.hooksPath "$HOOKS_DIR"
  echo "global core.hooksPath -> $HOOKS_DIR"
else
  repo="$1"
  git -C "$repo" config core.hooksPath "$HOOKS_DIR"
  echo "$repo: core.hooksPath -> $HOOKS_DIR"
fi

cat <<EOF

Wired. Two things worth knowing:

- A repo's LOCAL core.hooksPath overrides the global one. Check for stale local
  settings: git -C <repo> config --local core.hooksPath
  (One was found pointing at a directory deleted by a rename months earlier,
  which had silently disabled that repo's hooks the whole time.)
- Repos that install their own hooks the normal way (git-lfs, and anything else
  writing to .git/hooks/) still work: these hooks run the repo's own first and
  honour its exit code.
EOF
