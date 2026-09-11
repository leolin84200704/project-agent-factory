#!/usr/bin/env bash
# Boot a built NestJS app just long enough to learn whether its dependency
# injection graph resolves. No database, no network, no real config needed.
#
#   bash framework/githooks/lib/nest-di-smoke.sh [repo-root]
#
# Exit codes
#   0  PASS          the injector ran and reported no unresolved dependency
#   1  FAIL          a DI error was printed (UnknownDependenciesException et al.)
#   3  INCONCLUSIVE  the process produced no evidence that the injector ran
#                    (crashed before NestFactory.create, custom logger silenced
#                    InstanceLoader, wrong entry file). Treated as FAIL by the
#                    pre-push gate; set DI_SMOKE_ALLOW_SILENT=1 to accept it.
#   4  NO ENTRY      no built entry file — run the build first
#
# Why this exists. Unit tests construct services by hand or stub the new
# provider inside the one TestingModule spec, so they never ask the real
# injector to resolve the real module. `nest build` / `tsc` cannot see DI
# metadata problems either. The only thing that catches "constructor param
# typed as an interface, Nest wants a provider registered under Object" is the
# injector itself — and it does so within about a second of `node dist/main.js`,
# before the app ever reaches its first database or config hard-check.
#
# What "PASS" means, precisely. Nest logs "<Module> dependencies initialized"
# per module as it resolves providers; a DI failure throws instead. So:
#   PASS  = at least one such line (or "Nest application successfully started")
#           AND no DI error pattern in the output.
#   FAIL  = a DI error pattern in the output.
# Whether the app finishes booting is NOT the criterion: without a database it
# will normally die on a config check or sit waiting for a connection, and both
# of those are fine — the DI graph was already fully resolved by then.
#
# Caveat, stated so it is not mistaken for a guarantee: providers are
# instantiated module by module, so a constructor that throws early (a
# "JWT_SECRET is required" style check) can stop instantiation before a later
# module's DI error is reached. Feeding `.env.example` placeholders pushes the
# boot as far as possible; the report prints how many modules initialised and
# what the last line was, so a suspiciously early stop is visible.
#
# Environment. The child gets an EMPTY environment (env -i) plus PATH, HOME,
# NODE_ENV=test, a random high PORT, and the keys of the env files listed in
# DI_SMOKE_ENV_FILES (default: only ".env.di-smoke", a repo-local, git-ignored
# file of dummies — opt-in). Nothing from the caller's shell leaks in, so a
# smoke on a laptop with real credentials exported behaves like one on a clean
# CI box. `.env.example` is deliberately NOT read by default: measured on the
# reference repo it added zero modules (40 with, 40 without), and such files
# routinely carry real internal hosts (gRPC targets, Kafka brokers, a staging
# consumer-group id) that a booting app would happily contact. Add it via
# DI_SMOKE_ENV_FILES=".env.example" only when a repo's constructors throw on
# missing config before the graph is fully resolved.
#
# Knobs (all optional): DI_SMOKE_ENTRY (auto: dist/main.js, dist/src/main.js,
# nest-cli.json entryFile), DI_SMOKE_TIMEOUT (seconds, default 30),
# DI_SMOKE_ENV_FILES, DI_SMOKE_LOG (default ${TMPDIR:-/tmp}/di-smoke-<repo>.log),
# DI_SMOKE_ALLOW_SILENT=1.

set -u

repo_root="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$repo_root" || { echo "x [di-smoke] cannot cd to $repo_root"; exit 4; }
repo_name="$(basename "$(pwd)")"

timeout_s="${DI_SMOKE_TIMEOUT:-30}"
log="${DI_SMOKE_LOG:-${TMPDIR:-/tmp}/di-smoke-${repo_name}.log}"
env_files="${DI_SMOKE_ENV_FILES-.env.di-smoke}"

# ---------- locate the entry file ----------
entry="${DI_SMOKE_ENTRY:-}"
if [ -z "$entry" ]; then
  for cand in dist/main.js dist/src/main.js; do
    [ -f "$cand" ] && { entry="$cand"; break; }
  done
fi
if [ -z "$entry" ] && [ -f nest-cli.json ]; then
  ef=$(sed -n 's/.*"entryFile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' nest-cli.json | head -1)
  [ -n "$ef" ] && [ -f "dist/$ef.js" ] && entry="dist/$ef.js"
fi
if [ -z "$entry" ] || [ ! -f "$entry" ]; then
  echo "x [di-smoke] no built entry file (looked for dist/main.js, dist/src/main.js, nest-cli.json entryFile)."
  echo "  Run the build first, or set DI_SMOKE_ENTRY=<path to compiled main>."
  exit 4
fi

# ---------- build the child environment ----------
# Keys are taken from the env files verbatim; values lose surrounding quotes
# and an unquoted trailing "# comment". Duplicates are simply appended: `env`
# applies assignments left to right, so later files and the fixed overrides
# below win. (Plain arrays only — macOS ships bash 3.2, no `declare -A`.)
child_env=()
for f in $env_files; do
  [ -f "$f" ] || continue
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"          # ltrim
    case "$line" in ''|'#'*) continue;; esac
    line="${line#export }"
    key="${line%%=*}"; val="${line#*=}"
    printf '%s' "$key" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*$' || continue
    val="${val#"${val%%[![:space:]]*}"}"
    case "$val" in
      \"*) val="${val#\"}"; val="${val%%\"*}";;         # "quoted"  (first closing quote ends it)
      \'*) val="${val#\'}"; val="${val%%\'*}";;          # 'quoted'
      *)   val="${val%%[[:space:]]#*}"; val="${val%"${val##*[![:space:]]}"}";;  # strip " # c" then rtrim
    esac
    child_env+=("$key=$val")
  done < "$f"
done
# Fixed overrides come last so a repo's example file cannot point the smoke at
# a real port or a non-test mode.
child_env+=("NODE_ENV=test" "PORT=$(( 30000 + RANDOM % 10000 ))")

# ---------- boot ----------
: > "$log"
echo "[di-smoke] node $entry (timeout ${timeout_s}s, env: ${#child_env[@]} vars from: $env_files)"
env -i PATH="$PATH" HOME="${HOME:-/tmp}" "${child_env[@]}" node "$entry" >> "$log" 2>&1 &
pid=$!

DI_RE='UnknownDependenciesException|UndefinedDependencyException|can.t resolve dependencies|CircularDependencyException|UnknownExportException|UndefinedModuleException|InvalidModuleException|InvalidClassModuleException|UndefinedForwardRefException'
STARTED_RE='Nest application successfully started'
LOADER_RE='dependencies initialized|Starting Nest application'

elapsed=0; reason=''
while :; do
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"; code=$?; reason="process exited (code $code) after ${elapsed}s"; break
  fi
  if grep -qE "$STARTED_RE" "$log" 2>/dev/null; then reason="application started after ${elapsed}s"; break; fi
  if grep -qE "$DI_RE" "$log" 2>/dev/null; then sleep 1; reason="DI error printed after ${elapsed}s"; break; fi
  if [ "$elapsed" -ge "$timeout_s" ]; then reason="still running at ${timeout_s}s (waiting on a connection is normal)"; break; fi
  sleep 1; elapsed=$((elapsed + 1))
done
if kill -0 "$pid" 2>/dev/null; then
  kill "$pid" 2>/dev/null; sleep 1
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
fi

# ---------- verdict ----------
modules=$(grep -cE 'dependencies initialized' "$log" 2>/dev/null || true)
modules=${modules:-0}
# The most useful single line to echo: the first error-shaped one (why the boot
# stopped), else the last non-empty line.
last_line=$(grep -vE "$LOADER_RE" "$log" | grep -vE '^[[:space:]]*(at |$)' \
  | grep -E '"level":"error"|Error|error|Missing|required' | head -n 1 | cut -c1-220)
[ -z "$last_line" ] && last_line=$(grep -vE "$LOADER_RE" "$log" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-220)

if grep -qE "$DI_RE" "$log"; then
  echo ""
  echo "x [di-smoke] FAIL — the DI graph does not resolve ($modules modules initialised before the error; $reason)"
  grep -nE "$DI_RE" "$log" | head -n 5 | cut -c1-400 | sed 's/^/    /'
  echo "  Typical cause: an @Injectable constructor param typed as an interface or"
  echo "  marked optional with '?' only — Nest records it as a required 'Object'"
  echo "  provider. Fix: @Optional() @Inject(TOKEN), and add a spec that compiles the"
  echo "  REAL module via Test.createTestingModule so this is caught before the build."
  echo "  log: $log"
  exit 1
fi

if [ "$modules" -gt 0 ] || grep -qE "$STARTED_RE" "$log"; then
  echo "[di-smoke] PASS — DI graph resolved: $modules modules initialised; $reason"
  [ -n "$last_line" ] && echo "  last non-loader line: $last_line"
  echo "  log: $log"
  exit 0
fi

echo ""
echo "? [di-smoke] INCONCLUSIVE — no sign the injector ran ($reason)."
echo "  Nothing matched 'dependencies initialized' / 'Nest application successfully started'."
echo "  Either the app died before NestFactory.create (check the log), the logger is"
echo "  silenced below 'log' level, or the entry file is wrong (DI_SMOKE_ENTRY=...)."
tail -n 8 "$log" | cut -c1-300 | sed 's/^/    /'
echo "  log: $log"
if [ "${DI_SMOKE_ALLOW_SILENT:-0}" = "1" ]; then
  echo "  DI_SMOKE_ALLOW_SILENT=1 set — accepting."
  exit 0
fi
exit 3
