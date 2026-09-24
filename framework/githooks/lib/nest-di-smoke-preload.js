// Preloaded into the smoked app with `node -r`. Wraps NestFactory.create (and
// siblings) from the APP's own @nestjs/core so the smoke gets a logger-
// independent signal: the returned promise resolving means Nest finished
// scanning modules and instantiating every provider — the whole dependency
// graph is built. Rejecting surfaces the error even when bootstrap() swallows
// it. Nothing else is touched. Apps that bundle (webpack) or use ESM will not
// pick this up; the smoke then falls back to its output/socket evidence.
'use strict';
const out = (line) => { try { process.stderr.write(`[di-smoke] ${line}\n`); } catch (_) { /* ignore */ } };
let core;
try {
  core = require(require.resolve('@nestjs/core', { paths: [process.cwd()] }));
} catch (e) {
  out(`preload: @nestjs/core not resolvable from ${process.cwd()} (${e && e.message})`);
}
if (core && core.NestFactory) {
  for (const m of ['create', 'createMicroservice', 'createApplicationContext']) {
    const orig = core.NestFactory[m];
    if (typeof orig !== 'function') continue;
    core.NestFactory[m] = function patched(...args) {
      const p = orig.apply(this, args);
      Promise.resolve(p).then(
        () => out(`NestFactory.${m} resolved: dependency graph fully initialised`),
        (e) => out(`NestFactory.${m} rejected: ${(e && e.name) || 'Error'}: ${(e && e.message) || e}`),
      );
      return p;
    };
  }
}

// ---------------------------------------------------------------------------
// Checkout environment containment.
//
// The smoke starts the child with `env -i` plus a handful of gate-owned keys
// and documents that as "behaves like a clean CI box". Measured 2026-09-24 on
// a reference NestJS service, that promise was false by two independent
// routes, and the child ended up with 126 variables instead of 3:
//
//   1. the app's own `dotenv.config()` reads <cwd>/.env;
//   2. a generated Prisma client loads the .env recorded next to its schema —
//      an ABSOLUTE path baked at generate time, so it fires even from a
//      checkout that has no .env of its own.
//
// Two consequences, both worse than the missing keys they hide:
//   - Non-determinism. The same commit passes in a checkout that happens to
//     have a .env and fails in one that does not, and the standing workaround
//     has been to copy the real .env into every worktree.
//   - Inverted safety. In the passing case the gate boots the service with
//     live database/broker/RPC targets and a real signing key, on every push.
//
// So when DI_SMOKE_CONTAIN=1, reads of any `.env*` file are refused (ENOENT,
// which every loader treats as "no env file") unless the path is listed in
// DI_SMOKE_ENV_ALLOW. The gate's own env files stay readable; the checkout's
// do not. Each distinct refusal is announced once so the report can show what
// the boot would otherwise have picked up.
if (process.env.DI_SMOKE_CONTAIN === '1') {
  const fs = require('fs');
  const path = require('path');
  const allow = (process.env.DI_SMOKE_ENV_ALLOW || '')
    .split(':').filter(Boolean).map((p) => { try { return fs.realpathSync(p); } catch (_) { return path.resolve(p); } });
  const seen = Object.create(null);
  const blocked = (p) => {
    if (typeof p !== 'string') { try { p = String(p); } catch (_) { return false; } }
    if (!/^\.env($|[.-])/.test(path.basename(p))) return false;
    const abs = path.resolve(p);
    let real = abs; try { real = fs.realpathSync(abs); } catch (_) { /* missing anyway */ }
    if (allow.indexOf(abs) !== -1 || allow.indexOf(real) !== -1) return false;
    if (!seen[abs]) { seen[abs] = 1; out(`contained: refused ${abs} (the gate owns this boot's environment)`); }
    return true;
  };
  const enoent = (p) => Object.assign(new Error(`ENOENT: no such file or directory, open '${p}'`), { code: 'ENOENT', errno: -2, syscall: 'open', path: p });

  const rfs = fs.readFileSync;
  fs.readFileSync = function (p, ...rest) { if (blocked(p)) throw enoent(p); return rfs.call(this, p, ...rest); };
  const es = fs.existsSync;
  fs.existsSync = function (p) { if (blocked(p)) return false; return es.call(this, p); };
  const rf = fs.readFile;
  fs.readFile = function (p, ...rest) {
    if (blocked(p)) { const cb = rest[rest.length - 1]; if (typeof cb === 'function') return process.nextTick(() => cb(enoent(p))); }
    return rf.call(this, p, ...rest);
  };
  if (fs.promises && fs.promises.readFile) {
    const prf = fs.promises.readFile;
    fs.promises.readFile = function (p, ...rest) { if (blocked(p)) return Promise.reject(enoent(p)); return prf.call(this, p, ...rest); };
  }
  // Whatever the gate did hand over, stated once, so "N vars" in the report is
  // a number someone can check rather than a claim.
  out(`contained: ON — ${Object.keys(process.env).length} variables provided by the gate`);
}
