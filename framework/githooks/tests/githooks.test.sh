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

echo "--- guard 1 (ConfigMap coupling) moved to the instance repo: shared hook stays silent ---"
# The guard itself lives in the instance repo via guard-paths (see the "MOVED
# OUT" block in pre-commit). The two cases that expected THIS hook to block were
# stale and failed on every machine; removed 2026-09-18 (Leo). What remains pins
# the inverse: the shared hook must not fire on that shape.
NOYAML=$(mkrepo noyaml)
printf 'const a = process.env.BRAND_NEW_VAR;\n' > "$NOYAML/x.ts"
git -C "$NOYAML" add -A
run 0 "new env var, no ConfigMap pair: allowed" "$NOYAML" pre-commit

WITHYAML=$(mkrepo withyaml)
printf 'data:\n  EXISTING: "1"\n' > "$WITHYAML/svc-config.yaml"
printf 'data:\n  EXISTING: "1"\n' > "$WITHYAML/svc-config-prod.yaml"
printf 'const a = process.env.BRAND_NEW_VAR;\n' > "$WITHYAML/x.ts"
git -C "$WITHYAML" add -A
run 0 "new env var missing from a ConfigMap pair: not this hook's guard any more, allowed" "$WITHYAML" pre-commit

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

echo "--- pre-push prisma step prefers the repo's own generate script ---"
# Multi-schema repos generate every client from a package.json script; a bare
# `npx prisma generate` would refresh only the default schema. The fake script
# drops a marker the fake build then requires.
PRISMA=$(mkrepo prisma 'git@github.com:Vibrant-America/lis-backend-emr-v2.git')
mkdir -p "$PRISMA/prisma" && touch "$PRISMA/prisma/schema.prisma"
printf '{"scripts":{"prisma:generate":"touch .all-clients","build":"test -f .all-clients"}}\n' > "$PRISMA/package.json"
run 0 "repo with prisma:generate script: npm run script, then build sees its output" "$PRISMA" pre-push
printf '{"scripts":{"generate:prisma":"touch .all-clients","build":"test -f .all-clients"}}\n' > "$PRISMA/package.json"
rm -f "$PRISMA/.all-clients"
run 0 "generate:prisma spelling is honoured too" "$PRISMA" pre-push

echo "--- pre-push Nest DI smoke: gated Nest repos must boot their build ---"
# The 2026-09-10 shape: build green, injector throws on the first module import.
# dist/main.js stands in for the compiled app; the "build" is a no-op that
# leaves it in place. Exit codes come from the smoke's verdict, not the script.
NEST=$(mkrepo nest 'git@github.com:Vibrant-America/lis-backend-emr-v2.git')
mk_nest() {  # mk_nest <repo> <main.js body>
  mkdir -p "$1/dist"
  printf '{"scripts":{"build":"exit 0"},"dependencies":{"@nestjs/core":"^11"}}\n' > "$1/package.json"
  printf '%s\n' "$2" > "$1/dist/main.js"
}
LOADER='console.log(JSON.stringify({level:"log",context:"InstanceLoader",message:"AppModule dependencies initialized"}));'
DI_ERR='console.error("UnknownDependenciesException [Error]: Nest can'"'"'t resolve dependencies of the FooStore (ConfigService, ?)");'
export DI_SMOKE_TIMEOUT=5

mk_nest "$NEST" "$LOADER $DI_ERR process.exit(1);"
run 1 "DI error after some modules initialised: blocked" "$NEST" pre-push

mk_nest "$NEST" "$LOADER console.error('Error: JWT_SECRET environment variable is required'); process.exit(1);"
run 0 "graph resolved, then a config hard-check kills it: allowed" "$NEST" pre-push

mk_nest "$NEST" "$LOADER console.log('Nest application successfully started'); setInterval(function(){},1000);"
run 0 "app starts and stays up: allowed (killed, not waited out)" "$NEST" pre-push

mk_nest "$NEST" "$LOADER setInterval(function(){},1000);"
run 0 "graph resolved, then hangs on a connection until the timeout: allowed" "$NEST" pre-push

# `logger: false` shape (measured 2026-09-11): nothing Nest-shaped is ever
# printed, but the app reaches listen(). The open socket is the evidence.
mk_nest "$NEST" "require('http').createServer().listen(0, '127.0.0.1');"
run 0 "silent logger but the process listens on a port: allowed" "$NEST" pre-push

# Fully silent app (logger: ["error"]), slow to listen: the only evidence is the
# preload seeing the app's own NestFactory.create() resolve. A fake @nestjs/core
# in the fake repo's node_modules stands in for the real one.
mkdir -p "$NEST/node_modules/@nestjs/core"
printf '%s\n' "exports.NestFactory = { create: function () { return new Promise(function (r) { setTimeout(function () { r({ listen: function () { return new Promise(function () {}); } }); }, 300); }); } };" > "$NEST/node_modules/@nestjs/core/index.js"
mk_nest "$NEST" "require('@nestjs/core').NestFactory.create().then(function (app) { return app.listen(); });"
run 0 "silent app, no socket yet, but NestFactory.create resolved (preload marker): allowed" "$NEST" pre-push
printf '%s\n' "exports.NestFactory = { create: function () { var e = new Error(\"Nest can't resolve dependencies of the FooStore (ConfigService, ?)\"); e.name = 'UnknownDependenciesException'; return Promise.reject(e); } };" > "$NEST/node_modules/@nestjs/core/index.js"
mk_nest "$NEST" "require('@nestjs/core').NestFactory.create().catch(function () { setInterval(function(){},1000); });"
run 1 "bootstrap swallows the DI rejection; the preload still reports it: blocked" "$NEST" pre-push
rm -rf "$NEST/node_modules"

mk_nest "$NEST" "process.exit(0);"
run 1 "no sign the injector ran: inconclusive, blocked (fail-safe)" "$NEST" pre-push
DI_SMOKE_ALLOW_SILENT=1 run 0 "...unless the repo explicitly accepts a silent boot" "$NEST" pre-push

# Env handling: the child sees the repo-local dummies (quotes and trailing
# comments stripped, `export` tolerated), NODE_ENV forced to test, and nothing
# from the caller's shell. A wrong value is reported as a DI error so the
# assertion rides on the exit code like every other case here.
printf 'FOO="bar" # comment\nBAZ=1  # comment\nexport QUX=q\nNODE_ENV=production\n' > "$NEST/.env.di-smoke"
mk_nest "$NEST" "$LOADER var e=process.env; if(e.FOO!=='bar'||e.BAZ!=='1'||e.QUX!=='q'||e.NODE_ENV!=='test'||e.LEAKED){ $DI_ERR process.exit(1);} process.exit(0);"
LEAKED=1 run 0 "env file parsed, NODE_ENV forced, caller's shell vars do not leak" "$NEST" pre-push
rm "$NEST/.env.di-smoke"

# Per-machine env file, found by ORIGIN repo name (not the worktree's directory
# name), so one file serves every clone/worktree of a repo on the box.
MACHINE_ENV="$TMP/xdg/nest-di-smoke"; mkdir -p "$MACHINE_ENV"
printf 'JWT_SECRET=dummy\n' > "$MACHINE_ENV/lis-backend-emr-v2.env"
mk_nest "$NEST" "if(!process.env.JWT_SECRET){console.error('Config validation error: \"JWT_SECRET\" is required'); process.exit(1);} $LOADER process.exit(0);"
XDG_CONFIG_HOME="$TMP/xdg" run 0 "config-at-load app fed from the per-machine env file (keyed by origin name): allowed" "$NEST" pre-push
XDG_CONFIG_HOME="$TMP/nowhere" run 1 "...and without that file it dies before the injector: blocked" "$NEST" pre-push
# The knob can live in that file too — for apps whose DI phase awaits real infra.
printf 'DI_SMOKE_ALLOW_SILENT=1\nDI_SMOKE_TIMEOUT=2\n' > "$MACHINE_ENV/lis-backend-emr-v2.env"
mk_nest "$NEST" "if(process.env.DI_SMOKE_ALLOW_SILENT){ $DI_ERR process.exit(1);} setInterval(function(){},1000);"
XDG_CONFIG_HOME="$TMP/xdg" run 0 "DI_SMOKE_ALLOW_SILENT=1 in the per-machine file: silent boot accepted, knob not leaked to the child" "$NEST" pre-push
mk_nest "$NEST" "$DI_ERR process.exit(1);"
XDG_CONFIG_HOME="$TMP/xdg" run 1 "...but a printed DI error still fails even with ALLOW_SILENT" "$NEST" pre-push
rm -rf "$TMP/xdg"

# Scope: a gated repo WITHOUT @nestjs/core gets the build gate only.
mkdir -p "$GATED/dist"
printf '%s\n' "$DI_ERR process.exit(1);" > "$GATED/dist/main.js"
run 0 "gated repo without @nestjs/core: build only, no smoke" "$GATED" pre-push
unset DI_SMOKE_TIMEOUT

echo "--- pre-push related-tests gate: opt-in, diff-scoped, specs select themselves ---"
# A fake jest in node_modules/.bin records its argv and exits per a marker file,
# so the tests assert WHICH files the gate hands to jest, not jest's verdict.
mk_testrepo() {  # mk_testrepo <name> <origin-url>
  local d; d=$(mkrepo "$1" "$2")
  printf '{"scripts":{"build":"exit 0"},"jest":{}}\n' > "$d/package.json"
  mkdir -p "$d/node_modules/.bin" "$d/src"
  cat > "$d/node_modules/.bin/jest" <<'JEST'
#!/bin/sh
printf '%s\n' "$@" > "$(git rev-parse --show-toplevel)/.jest-argv"
[ -f "$(git rev-parse --show-toplevel)/.jest-fail" ] && { echo "FAIL src/x.spec.ts"; echo "Tests: 1 failed, 1 total"; exit 1; }
echo "Tests: 1 passed, 1 total"; exit 0
JEST
  chmod +x "$d/node_modules/.bin/jest"
  printf 'export const a = 1;\n' > "$d/src/base.ts"
  git -C "$d" add -A && git -C "$d" commit -qm base
  git -C "$d" update-ref refs/remotes/origin/main HEAD
  git -C "$d" checkout -qb feature/x
  echo "$d"
}
jest_saw() { [ -f "$1/.jest-argv" ] && grep -qx "$2" "$1/.jest-argv"; }

TG=$(mk_testrepo testgate 'git@github.com:Vibrant-America/lis-backend-emr-v2.git')
run 0 "gated repo, nothing changed since origin/main: skipped, allowed" "$TG" pre-push
[ ! -f "$TG/.jest-argv" ] || { echo "        FAIL: jest was invoked with no changed files"; fails=$((fails+1)); }

printf 'export const b = 2;\n' > "$TG/src/b.service.ts"
printf 'it("x",()=>{});\n' > "$TG/src/b.service.spec.ts"
printf 'it("x",()=>{});\n' > "$TG/src/b.integration.spec.ts"
printf 'export {};\n' > "$TG/src/types.d.ts"
mkdir -p "$TG/scripts" && printf 'export const s = 1;\n' > "$TG/scripts/tool.ts"
git -C "$TG" add -A && git -C "$TG" commit -qm change
run 0 "changed source + spec, fake jest passes: allowed" "$TG" pre-push
jest_saw "$TG" "src/b.service.ts"       || { echo "        FAIL: changed source not handed to jest"; fails=$((fails+1)); }
jest_saw "$TG" "src/b.service.spec.ts"  || { echo "        FAIL: changed spec not handed to jest"; fails=$((fails+1)); }
jest_saw "$TG" "src/b.integration.spec.ts" && { echo "        FAIL: integration spec must stay out"; fails=$((fails+1)); }
jest_saw "$TG" "src/types.d.ts"         && { echo "        FAIL: .d.ts must stay out"; fails=$((fails+1)); }
jest_saw "$TG" "scripts/tool.ts"        && { echo "        FAIL: files outside src/apps/libs must stay out"; fails=$((fails+1)); }
grep -qx -- "--findRelatedTests" "$TG/.jest-argv" || { echo "        FAIL: --findRelatedTests not passed"; fails=$((fails+1)); }

touch "$TG/.jest-fail"
run 1 "changed source, fake jest fails: blocked" "$TG" pre-push
rm -f "$TG/.jest-fail" "$TG/.jest-argv"

git -C "$TG" rm -q src/b.service.ts && git -C "$TG" commit -qm delete
run 0 "a file deleted on the branch is not handed to jest" "$TG" pre-push
jest_saw "$TG" "src/b.service.ts" && { echo "        FAIL: deleted file handed to jest"; fails=$((fails+1)); }

# pretest runs first when the repo declares one (emr-v2 generates its test prisma client there).
printf '{"scripts":{"build":"exit 0","pretest":"touch .pretest-ran"},"jest":{}}\n' > "$TG/package.json"
git -C "$TG" add -A && git -C "$TG" commit -qm pretest
run 0 "repo with a pretest script: it runs before jest" "$TG" pre-push
[ -f "$TG/.pretest-ran" ] || { echo "        FAIL: pretest did not run"; fails=$((fails+1)); }
printf '{"scripts":{"build":"exit 0","pretest":"exit 1"},"jest":{}}\n' > "$TG/package.json"
git -C "$TG" add -A && git -C "$TG" commit -qm pretest-fail
run 1 "pretest fails: blocked" "$TG" pre-push

NOTG=$(mk_testrepo notestgate 'git@github.com:Vibrant-America/LIS-backend-dashboard.git')
printf 'export const b = 2;\n' > "$NOTG/src/b.ts"
git -C "$NOTG" add -A && git -C "$NOTG" commit -qm change
touch "$NOTG/.jest-fail"
run 0 "build-gated repo NOT in the test-gate list: jest never runs" "$NOTG" pre-push
[ ! -f "$NOTG/.jest-argv" ] || { echo "        FAIL: jest invoked outside the test-gate list"; fails=$((fails+1)); }

NOBASE=$(mkrepo nobase 'git@github.com:Vibrant-America/LIS-transformer-v2.git')
printf '{"scripts":{"build":"exit 0"},"jest":{}}\n' > "$NOBASE/package.json"
mkdir -p "$NOBASE/src" && printf 'export const a = 1;\n' > "$NOBASE/src/a.ts"
git -C "$NOBASE" add -A && git -C "$NOBASE" commit -qm only
run 0 "no origin/main and no upstream: cannot scope the diff, skipped with a note" "$NOBASE" pre-push

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; else echo "$fails FAILED"; exit 1; fi
