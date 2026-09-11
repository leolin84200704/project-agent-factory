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
