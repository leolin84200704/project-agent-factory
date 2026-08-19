#!/usr/bin/env bash
# Exercise the shared pre-commit / pre-push hooks. Self-contained: builds
# throwaway repos, so it runs on any machine regardless of clone layout.
#
# Run: bash framework/githooks/tests/githooks.test.sh

HOOKS="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fails=0

mkrepo() {  # mkrepo <name> [origin-url]
  local d="$TMP/$1"
  mkdir -p "$d"
  git -C "$d" init -q .
  git -C "$d" config user.email t@t
  git -C "$d" config user.name t
  git -C "$d" config core.hooksPath "$HOOKS"
  [ -n "${2:-}" ] && git -C "$d" remote add origin "$2"
  echo "$d"
}

run() {  # run <expected_exit> <label> <repo> <hook> [args...]
  local want="$1" label="$2" repo="$3" hook="$4"; shift 4
  local out got mark='ok  '
  out=$(cd "$repo" && "$HOOKS/$hook" "$@" 2>&1); got=$?
  if [ "$got" != "$want" ]; then mark='FAIL'; fails=$((fails+1)); fi
  printf '[%s] want=%s got=%s | %s\n' "$mark" "$want" "$got" "$label"
  [ "$mark" = FAIL ] && echo "$out" | sed 's/^/        /'
  return 0
}

echo "--- guard 1: config coupled with code ---"
NOYAML=$(mkrepo noyaml)
printf 'const a = process.env.BRAND_NEW_VAR;\n' > "$NOYAML/x.ts"
git -C "$NOYAML" add -A
run 0 "repo without a ConfigMap pair: out of scope, allowed" "$NOYAML" pre-commit

WITHYAML=$(mkrepo withyaml)
printf 'data:\n  EXISTING: "1"\n' > "$WITHYAML/svc-config.yaml"
printf 'data:\n  EXISTING: "1"\n' > "$WITHYAML/svc-config-prod.yaml"
printf 'const a = process.env.BRAND_NEW_VAR;\n' > "$WITHYAML/x.ts"
git -C "$WITHYAML" add -A
run 1 "new env var missing from both ConfigMaps: blocked" "$WITHYAML" pre-commit

printf 'data:\n  EXISTING: "1"\n  BRAND_NEW_VAR: "2"\n' > "$WITHYAML/svc-config.yaml"
printf 'data:\n  EXISTING: "1"\n  BRAND_NEW_VAR: "2"\n' > "$WITHYAML/svc-config-prod.yaml"
git -C "$WITHYAML" add -A
run 0 "same var present in both ConfigMaps: allowed" "$WITHYAML" pre-commit

printf 'data:\n  EXISTING: "1"\n' > "$WITHYAML/svc-config-prod.yaml"
git -C "$WITHYAML" add -A
run 1 "present in dev but missing in prod: blocked" "$WITHYAML" pre-commit

echo "--- guard 2: no CJK in code ---"
CJK=$(mkrepo cjk)
printf 'const a = 1; // 中文註解\n' > "$CJK/code.ts"
git -C "$CJK" add -A
run 1 "CJK in .ts: blocked" "$CJK" pre-commit

git -C "$CJK" reset -q
rm "$CJK/code.ts"
printf '# 中文文件\n' > "$CJK/doc.md"
git -C "$CJK" add -A
run 0 "CJK in .md only: allowed" "$CJK" pre-commit

git -C "$CJK" reset -q
printf 'SELECT 1; -- 中文\n' > "$CJK/m.sql"
git -C "$CJK" add "$CJK/m.sql"
run 1 "CJK in .sql migration: blocked" "$CJK" pre-commit

echo "--- guard 3: memory files must not lose entries or links ---"
# Shaped like the real thing: frontmatter with a links list, then entry headings.
mk_ltm() {  # mk_ltm <path> <n_links> <ids...>
  local path="$1" nlinks="$2"; shift 2
  { echo '---'; echo 'id: failures'; echo 'score: 1.0'; echo 'links:'
    for i in $(seq 1 "$nlinks"); do echo "- VP-$((17000 + i))"; done
    echo 'tags:'; echo '- failures'; echo '---'; echo ''
    for id in "$@"; do echo "### **[[$id]]** — note"; echo ''; done
  } > "$path"
}

NOTAGENT=$(mkrepo notagent)
mkdir -p "$NOTAGENT/long-term-memory"
mk_ltm "$NOTAGENT/long-term-memory/failures.md" 5 A B C
git -C "$NOTAGENT" add -A && git -C "$NOTAGENT" commit -qm base --no-verify
mk_ltm "$NOTAGENT/long-term-memory/failures.md" 2 A
git -C "$NOTAGENT" add -A
run 0 "no STM store: not an agent repo, out of scope" "$NOTAGENT" pre-commit

MEM=$(mkrepo memrepo)
mkdir -p "$MEM/long-term-memory" "$MEM/storage/short_term_memory"
touch "$MEM/storage/short_term_memory/.keep"
mk_ltm "$MEM/long-term-memory/failures.md" 5 A B C
git -C "$MEM" add -A && git -C "$MEM" commit -qm base --no-verify

mk_ltm "$MEM/long-term-memory/failures.md" 5 A B C D
git -C "$MEM" add -A
run 0 "entries and links grow: allowed" "$MEM" pre-commit

mk_ltm "$MEM/long-term-memory/failures.md" 5 A B
git -C "$MEM" add -A
run 1 "an entry disappears: blocked" "$MEM" pre-commit

mk_ltm "$MEM/long-term-memory/failures.md" 2 A B C
git -C "$MEM" add -A
run 1 "links list shrinks while entries hold: blocked" "$MEM" pre-commit

# The 2026-08-18 shape: file still well-formed, more entries than before, but
# quietly missing one and rebuilt from a narrower link base.
mk_ltm "$MEM/long-term-memory/failures.md" 2 A B D E
git -C "$MEM" add -A
run 1 "more entries but one lost and links narrowed: blocked" "$MEM" pre-commit

# Counts can grow while knowledge disappears — the shape that would have slipped
# through a count-only check.
mk_ltm "$MEM/long-term-memory/failures.md" 6 A B D E
git -C "$MEM" add -A
run 1 "entry swapped out while both counts grow: blocked" "$MEM" pre-commit

mk_ltm "$MEM/long-term-memory/failures.md" 5 A B C
printf 'note\n' > "$MEM/long-term-memory/notes.md"
git -C "$MEM" add -A
run 0 "unchanged memory file plus a new one: allowed" "$MEM" pre-commit

echo "--- chaining to the repo's own hook (git-lfs et al.) ---"
CHAIN=$(mkrepo chain)
mkdir -p "$CHAIN/.git/hooks"
printf '#!/bin/sh\necho "local hook ran"\nexit 0\n' > "$CHAIN/.git/hooks/pre-commit"
chmod +x "$CHAIN/.git/hooks/pre-commit"
printf 'const a = 1;\n' > "$CHAIN/ok.ts"
git -C "$CHAIN" add -A
run 0 "repo-local pre-commit passes: continue" "$CHAIN" pre-commit

printf '#!/bin/sh\necho "local hook refused"\nexit 3\n' > "$CHAIN/.git/hooks/pre-commit"
chmod +x "$CHAIN/.git/hooks/pre-commit"
run 3 "repo-local pre-commit fails: its exit code is honoured" "$CHAIN" pre-commit

printf '#!/bin/sh\nexit 2\n' > "$CHAIN/.git/hooks/pre-push"
chmod +x "$CHAIN/.git/hooks/pre-push"
run 2 "repo-local pre-push fails (lfs shape): honoured" "$CHAIN" pre-push

echo "--- pre-push build gate is opt-in by repo ---"
OTHER=$(mkrepo other 'git@github.com:Some-Org/unrelated-service.git')
printf '{"scripts":{"build":"exit 1"}}\n' > "$OTHER/package.json"
run 0 "repo not in the gate list: no build, allowed" "$OTHER" pre-push

GATED=$(mkrepo gated 'git@github.com:Vibrant-America/lis-backend-emr-v2.git')
printf '{"scripts":{"build":"exit 1"}}\n' > "$GATED/package.json"
run 1 "gated repo with a failing build: blocked" "$GATED" pre-push

printf '{"scripts":{"build":"exit 0"}}\n' > "$GATED/package.json"
run 0 "gated repo with a passing build: allowed" "$GATED" pre-push

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
