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
#   PASS  = evidence the injector finished — the preload's "NestFactory.create
#           resolved" marker (see nest-di-smoke-preload.js), or at least one
#           such loader line, or "Nest application successfully started", or
#           the process is LISTENING on a TCP port (app.listen() only runs after
#           the whole graph resolved) — AND no DI error pattern in the output.
#   FAIL  = a DI error pattern in the output.
# The socket check is what makes this work for apps that boot with
# `logger: false` or a custom logger that swallows InstanceLoader lines: such an
# app prints nothing Nest-shaped, yet a DI failure still surfaces, because
# NestFactory.create() rejects and the unhandled rejection is printed by Node.
# (Measured 2026-09-11 on a repo with `logger: false`: zero loader lines, app
# listening on its port within 2 s — INCONCLUSIVE without this check.)
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
# DI_SMOKE_ENV_FILES (default: a per-machine file plus ".env.di-smoke", a
# repo-local git-ignored file of dummies). Nothing from the caller's shell
# leaks in.
#
# That alone was not enough, and saying it was is how this gate spent two weeks
# lying about what it tested. Measured 2026-09-24 on a reference service: the
# child reached 126 variables, not 3, because the app called dotenv.config()
# on <cwd>/.env and a generated Prisma client loaded the .env recorded next to
# its schema by ABSOLUTE path. So the verdict depended on whether the checkout
# happened to carry a .env — the same commit passed in the main clone and
# failed in a worktree — and the passing run booted the service against live
# database, broker and RPC targets with a real signing key.
#
# DI_SMOKE_CONTAIN=1 (default) therefore has the preload refuse every `.env*`
# read except the gate's own files, so each checkout of a commit gets the same
# verdict and no real credential enters the smoke. A repo whose boot needs
# config then needs dummies — generate them once per repo per machine with
#   node framework/githooks/lib/nest-di-smoke-init.js <repo>
# which derives them from the repo's .env KEYS and value SHAPES (scheme, port,
# short switches) and never copies an opaque value or a real host.
#
# Until that file exists the gate cannot be strict without breaking pushes in
# repos nobody has provisioned, so an inconclusive contained run is retried
# uncontained: if the retry passes, the report says the boot depends on a
# checkout-local .env and names the command, and the push proceeds. Once the
# per-machine file exists the repo is treated as provisioned and the contained
# verdict is final — no retry, no silent pass.
# `.env.example` is deliberately NOT read by default: measured on the reference
# repo it added zero modules (40 with, 40 without), and such files routinely
# carry real internal hosts that a booting app would happily contact.
#
# Apps that validate config at module-load time (ConfigModule.forRoot with a
# Joi schema throwing '"JWT_SECRET" is required') die before the injector runs
# and come out INCONCLUSIVE. Give them dummies in the per-machine file
# ~/.config/nest-di-smoke/<origin-repo-name>.env — one file per repo per
# machine, shared by all its worktrees, outside every product repo.
#
# Knobs (all optional): DI_SMOKE_ENTRY (auto: dist/main.js, dist/src/main.js,
# nest-cli.json entryFile), DI_SMOKE_TIMEOUT (seconds, default 30),
# DI_SMOKE_ENV_FILES (default "<machine file> .env.di-smoke"), DI_SMOKE_ENV_DIR,
# DI_SMOKE_LOG (default ${TMPDIR:-/tmp}/di-smoke-<repo>.log),
# DI_SMOKE_ALLOW_SILENT=1, DI_SMOKE_CONTAIN=0 to disable containment. ALLOW_SILENT / TIMEOUT / ENTRY are also read from the
# env files themselves (and not passed to the child), so a repo whose DI phase
# awaits real infrastructure can be marked as such in its per-machine file.

set -u

repo_root="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$repo_root" || { echo "x [di-smoke] cannot cd to $repo_root"; exit 4; }
# Absolute from here on: the report prints this path as a command to re-run, and
# a "." in that command only works from the directory the caller happened to be in.
repo_root="$(pwd)"
# Repo identity from the origin URL, like pre-push: a worktree's directory name
# is whatever the ticket was called, and the per-machine env file below must be
# found from every worktree of the same repo.
origin_url="$(git remote get-url origin 2>/dev/null)"
repo_name="$(basename "${origin_url%.git}" 2>/dev/null)"
[ -n "$repo_name" ] && [ "$repo_name" != "/" ] || repo_name="$(basename "$(pwd)")"

timeout_s="${DI_SMOKE_TIMEOUT:-30}"
allow_silent="${DI_SMOKE_ALLOW_SILENT:-0}"
log="${DI_SMOKE_LOG:-${TMPDIR:-/tmp}/di-smoke-${repo_name}.log}"
# Env sources, in order (later wins): a per-MACHINE file keyed by repo name —
# shared by every clone and worktree of that repo on this box, never inside a
# product repo — then a repo-local git-ignored .env.di-smoke.
machine_env_dir="${DI_SMOKE_ENV_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/nest-di-smoke}"
machine_env_file="$machine_env_dir/$repo_name.env"
env_files="${DI_SMOKE_ENV_FILES-$machine_env_file .env.di-smoke}"

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
    case "$key" in
      # Knobs for the smoke itself may live in the env files too, so a repo's
      # per-machine file can say "this app never gives boot evidence without
      # infra" without anyone exporting anything in their shell.
      DI_SMOKE_ALLOW_SILENT) allow_silent="$val"; continue;;
      DI_SMOKE_TIMEOUT)      timeout_s="$val"; continue;;
      DI_SMOKE_ENTRY)        [ -n "$val" ] && entry="$val"; continue;;
      DI_SMOKE_*)            continue;;
    esac
    child_env+=("$key=$val")
  done < "$f"
done
# Fixed overrides come last so a repo's example file cannot point the smoke at
# a real port or a non-test mode.
child_env+=("NODE_ENV=test" "PORT=$(( 30000 + RANDOM % 10000 ))")

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

# ---------- boot ----------
preload="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nest-di-smoke-preload.js"
contain="${DI_SMOKE_CONTAIN:-1}"
# A repo counts as provisioned once its per-machine dummy file exists; from then
# on the contained verdict is final (see the header: no retry, no silent pass).
provisioned=0; [ -f "$machine_env_file" ] && provisioned=1
# The gate's own env files stay readable inside the contained child; every other
# `.env*` does not.
allow=""
for f in $env_files; do [ -f "$f" ] && allow="${allow:+$allow:}$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"; done

DI_RE='UnknownDependenciesException|UndefinedDependencyException|can.t resolve dependencies|CircularDependencyException|UnknownExportException|UndefinedModuleException|InvalidModuleException|InvalidClassModuleException|UndefinedForwardRefException'
STARTED_RE='Nest application successfully started|Application is running on|[Ll]istening on|\[di-smoke\] NestFactory\.[A-Za-z]+ resolved'
LOADER_RE='dependencies initialized|Starting Nest application'

# Is the child (or a descendant) listening on a TCP socket? Logger-independent
# evidence that app.listen() ran, i.e. the DI graph resolved. lsof is present on
# macOS and most Linux images; where it is missing this simply never fires.
is_listening() {
  command -v lsof >/dev/null 2>&1 || return 1
  lsof -Pan -p "$1" -iTCP -sTCP:LISTEN 2>/dev/null | grep -q LISTEN
}

# boot_once <contain 0|1> <logfile> -> sets reason, listening
boot_once() {
  _contain="$1"; _log="$2"
  : > "$_log"
  if [ "$_contain" = 1 ]; then
    env -i PATH="$PATH" HOME="${HOME:-/tmp}" DI_SMOKE_CONTAIN=1 DI_SMOKE_ENV_ALLOW="$allow" \
      "${child_env[@]}" node -r "$preload" "$entry" >> "$_log" 2>&1 &
  else
    env -i PATH="$PATH" HOME="${HOME:-/tmp}" \
      "${child_env[@]}" node -r "$preload" "$entry" >> "$_log" 2>&1 &
  fi
  pid=$!
  listening=0; elapsed=0; reason=''
  while :; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"; code=$?; reason="process exited (code $code) after ${elapsed}s"; break
    fi
    if grep -qE "$STARTED_RE" "$_log" 2>/dev/null; then reason="application started after ${elapsed}s"; break; fi
    if is_listening "$pid"; then listening=1; reason="process listening on a TCP port after ${elapsed}s"; break; fi
    if grep -qE "$DI_RE" "$_log" 2>/dev/null; then sleep 1; reason="DI error printed after ${elapsed}s"; break; fi
    if [ "$elapsed" -ge "$timeout_s" ]; then reason="still running at ${timeout_s}s (waiting on a connection is normal)"; break; fi
    sleep 1; elapsed=$((elapsed + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    # stderr parked on fd 3 so bash's own "Terminated: 15" job notice stays out
    # of the report; the child's output went to $_log anyway.
    exec 3>&2 2>/dev/null
    kill "$pid" 2>/dev/null; sleep 1
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    exec 2>&3 3>&-
  fi
}

echo "[di-smoke] node $entry (timeout ${timeout_s}s, env: ${#child_env[@]} vars from: $env_files)"
if [ "$contain" = 1 ]; then
  echo "[di-smoke] containment ON (checkout .env files are refused; provisioned: $([ "$provisioned" = 1 ] && echo yes || echo no))"
fi
boot_once "$contain" "$log"

# ---------- verdict ----------
# What the boot would have inherited from the checkout had containment been off.
# Printed on every verdict: a refusal the report never mentions is a difference
# between this run and a clean CI box that nobody would have known about.
report_containment() {
  [ "$contain" = 1 ] || { echo "  containment: OFF (DI_SMOKE_CONTAIN=0) — this verdict may depend on files in the checkout"; return; }
  _n=$(grep -c 'contained: refused' "$verdict_log" 2>/dev/null || true); _n=${_n:-0}
  if [ "$_n" -gt 0 ]; then
    echo "  containment: refused $_n checkout env file(s) the boot tried to read —"
    grep -o 'contained: refused [^ ]*' "$verdict_log" | sed 's/contained: refused /    /' | sort -u | head -n 5
  fi
}
verdict_log="$log"
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
  report_containment
  echo "  log: $log"
  exit 1
fi

if [ "$modules" -gt 0 ] || [ "$listening" = 1 ] || grep -qE "$STARTED_RE" "$log"; then
  echo "[di-smoke] PASS — DI graph resolved: $modules modules initialised; $reason"
  [ -n "$last_line" ] && echo "  last non-loader line: $last_line"
  report_containment
  echo "  log: $log"
  exit 0
fi

echo ""
echo "? [di-smoke] INCONCLUSIVE — no sign the injector ran ($reason)."
echo "  Nothing matched 'dependencies initialized' / 'successfully started' / 'running on',"
echo "  the preload never saw NestFactory.create resolve, and the process never listened"
echo "  on a TCP port."
echo "  Either the app died before NestFactory.create (check the log), the logger is"
echo "  silenced below 'log' level, or the entry file is wrong (DI_SMOKE_ENTRY=...)."
tail -n 8 "$log" | cut -c1-300 | sed 's/^/    /'
if grep -qE 'Config validation error|is required|Missing required|must be provided' "$log"; then
  echo "  This looks like config validation at module load, i.e. before the injector."
  echo "  Give it dummies in the per-machine env file (shared by every worktree of"
  echo "  this repo, outside the repo):  $machine_env_file"
  grep -oE '"[A-Z][A-Z0-9_]+" is required' "$log" | sort -u | sed 's/"\([^"]*\)" is required/    \1=dummy/' | head -n 20
fi
report_containment
echo "  log: $log"

# Contained and inconclusive, on a repo nobody has provisioned yet: find out
# whether the checkout's own .env is what used to carry this boot. Making that
# a hard failure would break pushes in every gated repo at once, so it stays a
# pass — but a named, counted one, not a silent one.
if [ "$contain" = 1 ] && [ "$provisioned" = 0 ]; then
  echo ""
  echo "[di-smoke] retrying WITHOUT containment to see whether the checkout's .env carried this boot ..."
  boot_once 0 "$log.uncontained"
  if grep -qE "$STARTED_RE" "$log.uncontained" || [ "$listening" = 1 ] \
     || [ "$(grep -cE 'dependencies initialized' "$log.uncontained" 2>/dev/null || echo 0)" -gt 0 ]; then
    echo ""
    echo "! [di-smoke] this repo boots only with a .env from the checkout — so the gate is"
    echo "  testing whatever that file happens to contain, and a worktree without it fails"
    echo "  on the same commit. Passing anyway (unprovisioned), with the divergence named."
    echo "  Fix once per repo per machine, values synthesised from key names and value"
    echo "  shapes, no secret or real host copied:"
    echo "      node $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nest-di-smoke-init.js \"$repo_root\""
    echo "  After that file exists the contained verdict is final."
    echo "  uncontained log: $log.uncontained"
    exit 0
  fi
  echo "  uncontained retry also gave no evidence — the failure is not about the environment."
  echo "  uncontained log: $log.uncontained"
fi
if [ "$allow_silent" = "1" ]; then
  echo "  DI_SMOKE_ALLOW_SILENT=1 (shell or env file) — accepting; a printed DI error would still have failed."
  exit 0
fi
exit 3
