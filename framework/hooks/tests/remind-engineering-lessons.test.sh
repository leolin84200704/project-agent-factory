#!/bin/bash
# Exercise remind-engineering-lessons.sh. Each case runs against a fresh session
# id, because the hook is once-per-session by design and a reused marker would
# make every later case look like a pass.
#
# Run: bash framework/hooks/tests/remind-engineering-lessons.test.sh

HOOK="$(cd "$(dirname "$0")/.." && pwd)/remind-engineering-lessons.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export TMPDIR="$TMP"

fails=0
n=0
run() {  # run <expected_exit> <command> [label]
  local want="$1" cmd="$2" label="${3:-}" got mark='ok  ' payload
  n=$((n+1))
  payload=$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]},"session_id":sys.argv[2]}))' "$cmd" "s$n")
  printf '%s' "$payload" | "$HOOK" >/dev/null 2>&1; got=$?
  if [ "$got" != "$want" ]; then mark='FAIL'; fails=$((fails+1)); fi
  printf '[%s] want=%s got=%s | %s\n' "$mark" "$want" "$got" "${label:-$(printf '%.72s' "$cmd")}"
}

echo "--- real DB / prod surfaces: REMINDED ---"
run 2 'mysql -h prod -u root -e "UPDATE orders SET status = 1"'
run 2 'psql -c "DELETE FROM samples WHERE id = 1"'
run 2 'PGPASSWORD=x psql -h db -c "SELECT 1"'                        'env-prefixed psql'
run 2 'sudo mysql -e "SELECT 1"'                                     'sudo-prefixed mysql'
run 2 'cd /srv && mysqldump lis_core > dump.sql'
run 2 'kubectl apply -f deploy.yaml'
run 2 'kubectl rollout restart deploy/emr-v2'
run 2 'gcloud sql instances patch lis-prod --tier=db-n1'
run 2 'npx prisma migrate deploy'
run 2 'ls -la | grep foo; mysql -e "SELECT 1"'                       'DB client after a separator'

echo "--- heredoc bodies still count when they carry a write statement ---"
run 2 "$(printf 'mysql lis <<%s\nUPDATE orders SET status = 2 WHERE id = 7;\nSQL\n' "'SQL'")"
run 2 "$(printf 'python3 - <<%s\ncur.execute("DELETE FROM audit_log WHERE id = 3")\nPY\n' "'PY'")"

echo "--- the measured false positive (2026-08-18) and its neighbours: ALLOWED ---"
# A read-only analysis script whose regex literal merely names DB clients. This
# exact shape cost a full round-trip before the fix.
run 0 "$(printf 'cat > /tmp/an.py <<%s\ncats = [("db", r"mysql|psql|SELECT |select \\*")]\nEOF\npython3 /tmp/an.py\n' "'EOF'")" 'heredoc containing "mysql|psql" regex literal'
run 0 'grep -rn "mysql" ~/notes/patterns.md'                         'grep for the word mysql'
run 0 'grep -n "DELETE FROM" migrations/audit.sql'                   'searching a .sql file'
run 0 'echo "run mysql later" >> TODO.md'
run 0 'git log --grep="INSERT INTO" --oneline'
run 0 'rg -l "kubectl apply" docs/'                                  'searching for a kubectl command'
run 0 'cat notes/mysql-tuning.md'                                    'filename containing mysql'
run 0 'kubectl get pods -A'                                          'read-only kubectl'
run 0 'gcloud auth list'                                             'non-mutating gcloud'
run 0 'npx prisma generate'                                          'prisma without migrate'

echo "--- sqlite carve-out: read-only local file DB stays silent ---"
run 0 'sqlite3 cache.db "SELECT count(*) FROM rows"'
run 2 'sqlite3 cache.db "DELETE FROM rows WHERE id = 1"'             'sqlite write still reminded'
run 2 'sqlite3 cache.db "SELECT 1" && mysql -e "SELECT 1"'           'sqlite carve-out does not cover a second client'

echo "--- once per session, then stands down ---"
same=$(python3 -c 'import json;print(json.dumps({"tool_input":{"command":"kubectl apply -f a.yaml"},"session_id":"repeat"}))')
first=$(printf '%s' "$same" | "$HOOK" >/dev/null 2>&1; echo $?)
second=$(printf '%s' "$same" | "$HOOK" >/dev/null 2>&1; echo $?)
if [ "$first" = 2 ] && [ "$second" = 0 ]; then
  printf '[ok  ] first=2 second=0 | reminder fires once, then stands down\n'
else
  printf '[FAIL] first=%s second=%s | expected 2 then 0\n' "$first" "$second"; fails=$((fails+1))
fi

echo
if [ "$fails" = 0 ]; then echo "all passed"; else echo "$fails FAILED"; exit 1; fi
