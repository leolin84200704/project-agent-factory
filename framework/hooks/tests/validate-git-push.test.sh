#!/bin/bash
# Exercise validate-git-push.sh. Self-contained: builds throwaway git repos with
# fake origin URLs, so it runs on any machine regardless of clone layout (that
# portability is the same reason the hook keys on the origin URL, not the path).
#
# Run: bash framework/hooks/tests/validate-git-push.test.sh

HOOK="$(cd "$(dirname "$0")/.." && pwd)/validate-git-push.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkrepo() {  # mkrepo <dir-name> <origin-url>
  local d="$TMP/$1"
  mkdir -p "$d" && git -C "$d" init -q . && git -C "$d" remote add origin "$2"
  echo "$d"
}

PERSONAL=$(mkrepo personal    'git@github.com:leolin84200704/project-agent-factory.git')
PRODUCT=$(mkrepo product      'git@github.com:Some-Org/product-service.git')
# A URL that CONTAINS a personal repo's name but is a different repo. Must NOT
# inherit the personal policy — substring matching on the whole URL would.
FORKISH=$(mkrepo forkish      'git@github.com:evil/project-agent-factory-mirror.git')
NOREPO="$TMP/not-a-repo"; mkdir -p "$NOREPO"

fails=0
run() {  # run <expected_exit> <command> <cwd>
  local want="$1" cmd="$2" cwd="$3" out got mark='ok  '
  local payload
  payload=$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]},"cwd":sys.argv[2]}))' "$cmd" "$cwd")
  out=$(printf '%s' "$payload" | "$HOOK" 2>&1); got=$?
  if [ "$got" != "$want" ]; then mark='FAIL'; fails=$((fails+1)); fi
  printf '[%s] want=%s got=%s | %-52s | %s\n' "$mark" "$want" "$got" "$cmd" "$out"
}

echo "--- force push: always BLOCKED, every spelling ---"
run 2 'git push --force origin main'                        "$PERSONAL"
run 2 'git push --force-with-lease origin feature/x'        "$PERSONAL"
run 2 'git push -f origin feature/x'                        "$PRODUCT"
run 2 'git push origin feature/x -f'                        "$PERSONAL"
run 2 'git push origin +main'                               "$PERSONAL"   # +refspec, no flag anywhere
run 2 'git push origin +feature/x'                          "$PRODUCT"
run 2 'git push origin +refs/heads/main:refs/heads/main'     "$PERSONAL"
run 2 'git -C /somewhere push -f'                           "$PERSONAL"

echo "--- --no-verify on a push: BLOCKED (it skips the shared pre-push gate) ---"
run 2 'git push --no-verify origin feature/x'               "$PRODUCT"
run 2 'git push origin feature/x --no-verify'               "$PERSONAL"
run 2 'git push --no-verify'                                "$PERSONAL"
# Only the push segment is inspected: a --no-verify on the chained commit is
# the pre-COMMIT hooks' business, not this guard's.
run 0 'git commit --no-verify -m x && git push origin feature/x' "$PERSONAL"

echo "--- non-push commands carrying -f / +: ALLOWED ---"
run 0 'rm -f tmp.js'                                        "$PERSONAL"
run 0 'tail -f logs/app.log'                                "$PERSONAL"
run 0 'grep -f patterns file'                               "$PERSONAL"
run 0 'tar -f archive.tar -x'                               "$PERSONAL"
run 0 'rm -f a.js && git push origin feature/x'             "$PERSONAL"
run 0 'echo a+b && git push origin feature/x'               "$PERSONAL"

echo "--- personal repo: main OK, master/staging/develop BLOCKED ---"
run 0 'git push origin main'                                "$PERSONAL"
run 0 'git push -u origin main'                             "$PERSONAL"
run 2 'git push origin master'                              "$PERSONAL"
run 2 'git push origin staging'                             "$PERSONAL"
run 2 'git push origin develop'                             "$PERSONAL"

echo "--- product repo: every protected branch BLOCKED, feature/bugfix OK ---"
run 2 'git push origin main'                                "$PRODUCT"
run 2 'git push -u origin main'                             "$PRODUCT"
run 2 'git push origin staging'                             "$PRODUCT"
run 2 'git push origin stage_test'                          "$PRODUCT"
run 2 'git push origin develop'                             "$PRODUCT"
run 0 'git push -u origin bugfix/leo/x'                     "$PRODUCT"
run 0 'git push origin feature/leo/vp-1'                    "$PRODUCT"
run 0 'git push origin release/main'                        "$PRODUCT"   # branch token must be exact

echo "--- personal-lookalike origin URL: STRICT, not personal ---"
run 2 'git push origin main'                                "$FORKISH"

echo "--- unidentified repo: fail-safe STRICT ---"
run 2 'git push origin main'                                "$NOREPO"
run 2 'git push origin main'                                "/nonexistent/dir"

echo "--- target resolution: cd and -C win over the session cwd ---"
run 2 "cd $PRODUCT && git push origin main"                 "$PERSONAL"
run 0 "cd $PERSONAL && git push origin main"                "$PRODUCT"
run 2 "git -C $PRODUCT push origin main"                    "$PERSONAL"
run 0 "git -C $PERSONAL push origin main"                   "$PRODUCT"

echo "--- only the git-push segments are inspected, not the whole command ---"
# The PR-body case that blocked real work on 2026-08-18: a legal branch push
# chained to a `gh pr create` whose body text mentions a protected branch. The
# blocked call took the chained commit down with it, so this is not cosmetic.
run 0 'git push -u origin feature/leo/x && gh pr create --base main --title "x"'        "$PRODUCT"
run 0 'git push origin feature/leo/x && gh pr create --body "run: git checkout main"'  "$PRODUCT"
run 0 'git push origin feature/leo/x && echo "merged into main later"'                 "$PRODUCT"
run 0 'echo "see main branch" && git push origin bugfix/leo/y'                         "$PRODUCT"
# ...and the fail-open this replaces: taking only the FIRST `push` in the command
# meant an earlier innocuous one shielded the real push from every check.
run 2 'echo "about to push" && git push --force origin feature/x'                      "$PERSONAL"
run 2 'echo "about to push" && git push origin main'                                   "$PRODUCT"
run 2 'echo "ready to push" && git push origin +main'                                  "$PERSONAL"
# A multi-line command: heredoc text naming a protected branch is not a push.
run 0 "$(printf 'git push origin feature/leo/z\ncat > notes.md <<%s\ndeploy from main after review\nEOF\n' "'EOF'")" "$PRODUCT"

echo "--- heredoc bodies are data: quoting a push in a message is not a push ---"
# Blocked this very commit before the fix: the message described the force pushes
# the change closes, and the guard read its own documentation as a command.
run 0 "$(printf 'git commit -m "$(cat <<%s\nfixes: git push --force origin x sailed through\nEOF\n)"\n' "'EOF'")"  "$PERSONAL"
run 0 "$(printf 'gh pr create --body "$(cat <<%s\ndo not git push origin main by hand\nEOF\n)"\n' "'EOF'")"        "$PRODUCT"
# The body is ignored, the real command in the same call is not.
run 2 "$(printf 'git push --force origin feature/x\ncat > n.md <<%s\nharmless text\nEOF\n' "'EOF'")"               "$PERSONAL"

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
