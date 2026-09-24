#!/usr/bin/env node
// Provision the per-machine dummy environment the Nest DI smoke boots with.
//
//   node nest-di-smoke-init.js <repo-root> [--force] [--print]
//
// Writes  ${XDG_CONFIG_HOME:-~/.config}/nest-di-smoke/<origin-repo-name>.env
// — one file per repo per machine, shared by every clone and worktree of that
// repo, and deliberately outside every product repo.
//
// Why this exists. The smoke runs the compiled app for a few seconds to learn
// whether its dependency graph resolves. Apps whose modules read config at load
// time die before the injector runs unless something supplies keys, and the
// something used to be "whatever .env the checkout happened to carry" — which
// made the verdict depend on the directory rather than the commit, and booted
// the service against live infrastructure. The gate now refuses those files, so
// the keys have to come from here.
//
// What is copied and what is not. KEY NAMES are copied verbatim: the app asks
// for them by name, and a name is not a secret. VALUES are never copied. Each
// value is replaced by one synthesised from its SHAPE, because shape is what a
// loader validates:
//   scheme://user:pass@host:port/db  ->  scheme://di:smoke@127.0.0.1:port/di_smoke
//   host:port                        ->  127.0.0.1:port
//   12345 / true / false             ->  kept (ports, timeouts, feature flags)
//   short switch-like token          ->  kept, unless the key names a secret
//   anything else                    ->  di-smoke
// So a URL still parses, a port is still a port, a branch on
// platform_type == 'cloud' still takes the branch it takes in production — and
// every host points at loopback, so a booting app cannot reach anything real.
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const args = process.argv.slice(2);
const force = args.includes('--force');
const printOnly = args.includes('--print');
const repoRoot = path.resolve(args.find((a) => !a.startsWith('--')) || process.cwd());

const die = (m) => { console.error(`x [di-smoke-init] ${m}`); process.exit(1); };
if (!fs.existsSync(repoRoot)) die(`no such directory: ${repoRoot}`);

let repoName;
try {
  const origin = execFileSync('git', ['-C', repoRoot, 'remote', 'get-url', 'origin'], { encoding: 'utf8' }).trim();
  repoName = path.basename(origin.replace(/\.git$/, ''));
} catch (_) { repoName = path.basename(repoRoot); }

const outDir = process.env.DI_SMOKE_ENV_DIR
  || path.join(process.env.XDG_CONFIG_HOME || path.join(process.env.HOME || '/tmp', '.config'), 'nest-di-smoke');
const outFile = path.join(outDir, `${repoName}.env`);

const sources = ['.env', '.env.example', '.env.sample', '.env.local']
  .map((f) => path.join(repoRoot, f)).filter((f) => fs.existsSync(f));
if (!sources.length) {
  die(`no .env / .env.example in ${repoRoot} to take key names from.\n`
    + `  Run the smoke once and use the keys it reports, then write ${outFile} by hand.\n`
    + `  (An empty file would mark the repo provisioned and make the strict verdict final.)`);
}

const secretish = (k) => /SECRET|TOKEN|KEY|PASS|PWD|CREDENTIAL|SIGN|CONNECTION_STRING|AUTH|SALT|CERT|PRIVATE|USER|LOGIN|ACCOUNT/i.test(k);
const counts = { kept: 0, shaped: 0, replaced: 0, empty: 0 };

function synth(key, raw) {
  const val = String(raw).trim();
  if (val === '') { counts.empty++; return ''; }
  if (val.includes(',')) return val.split(',').map((v) => synth(key, v)).join(',');
  if (/^(true|false)$/i.test(val) || /^-?\d+(\.\d+)?$/.test(val)) { counts.kept++; return val; }
  const url = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/(.*)$/.exec(val);
  if (url) {
    const port = (/:(\d+)(?:[/?#]|$)/.exec(url[2]) || [])[1];
    counts.shaped++;
    return `${url[1]}://di:smoke@127.0.0.1:${port || 1}/di_smoke`;
  }
  const hostPort = /^([A-Za-z0-9_.-]+):(\d+)$/.exec(val);
  if (hostPort) { counts.shaped++; return `127.0.0.1:${hostPort[2]}`; }
  // A bare host — an IP or a dotted name — is an internal address even without a
  // port, and the "short token" rule below would otherwise copy it verbatim.
  // (Caught 2026-09-24 by the leak check on the first generated file: three
  // internal IPs had been copied straight through.) Dotted digit runs that are
  // not an IPv4 address are version strings and stay.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(val)) { counts.shaped++; return '127.0.0.1'; }
  if (/^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/.test(val) && !/^\d+(\.\d+)+$/.test(val)) { counts.shaped++; return '127.0.0.1'; }
  // Switch-like values drive real branches (platform type, environment name), so
  // they are kept — but only in a shape that cannot be a host or a credential:
  // letters first, then letters/digits/underscore, nothing dotted or dashed.
  if (!secretish(key) && /^[A-Za-z][A-Za-z0-9_]{0,15}$/.test(val)) { counts.kept++; return val; }
  counts.replaced++;
  return 'di-smoke';
}

const seen = Object.create(null);
const lines = [];
for (const src of sources) {
  for (const raw of fs.readFileSync(src, 'utf8').split(/\r?\n/)) {
    const line = raw.replace(/^\s+/, '').replace(/^export\s+/, '');
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || seen[key]) continue;
    if (/^DI_SMOKE_/.test(key)) continue;
    let val = line.slice(eq + 1).trim();
    const q = /^(['"])([\s\S]*?)\1/.exec(val);
    val = q ? q[2] : val.replace(/\s+#.*$/, '').trim();
    seen[key] = 1;
    lines.push(`${key}=${synth(key, val)}`);
  }
}
if (!lines.length) die(`found no usable keys in: ${sources.join(', ')}`);

const header = [
  '# Nest DI smoke — synthetic environment for this repo on this machine.',
  `# Generated ${new Date().toISOString().slice(0, 10)} from key names in: ${sources.map((s) => path.basename(s)).join(', ')}`,
  '#',
  '# Values are SYNTHETIC. No value was copied from the source file: every host is',
  '# loopback, every credential is a placeholder. Safe to keep, useless to steal.',
  '# Never copy this file into a repo, and never replace a value here with a real',
  '# one — the whole point is that the gate boots against nothing.',
  '#',
  '# Regenerate after the repo gains config keys:',
  '#   node <factory>/framework/githooks/lib/nest-di-smoke-init.js <repo> --force',
  '',
].join('\n');
const body = `${header}${lines.join('\n')}\n`;

if (printOnly) { process.stdout.write(body); process.exit(0); }
if (fs.existsSync(outFile) && !force) die(`${outFile} already exists — pass --force to regenerate.`);
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(outFile, body, { mode: 0o600 });
console.log(`[di-smoke-init] wrote ${outFile}`);
console.log(`  repo: ${repoName}   keys: ${lines.length}   sources: ${sources.map((s) => path.basename(s)).join(', ')}`);
console.log(`  values: ${counts.kept} kept (numbers/flags/switches), ${counts.shaped} shape-preserved (scheme+port, host -> 127.0.0.1), ${counts.replaced} replaced with a placeholder, ${counts.empty} empty`);
console.log('  this repo is now provisioned: the contained smoke verdict is final for it.');
