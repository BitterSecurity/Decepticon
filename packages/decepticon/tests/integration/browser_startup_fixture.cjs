// Run inside the owned, network-isolated sandbox image. Never contact a target.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const template = process.argv[2];
assert(template && path.isAbsolute(template));
const expectedBlankNewPages = Number(process.argv[3] || 0);
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-browser-startup-'));
let hits = 0;
const server = http.createServer((_req, res) => {
  hits++;
  res.setHeader('Content-Type', 'text/html');
  res.end(`<!doctype html><h1></h1><script>
    const visits = Number(localStorage.getItem('visits') || 0) + 1;
    localStorage.setItem('visits', String(visits));
    document.cookie = 'ownedVisits=' + visits + '; Path=/';
    document.querySelector('h1').textContent = 'OWNED_VISIT_' + visits;
  </script>`);
});

// Observe the actual selected automation implementation. Mutations below only
// construct real empty/nonblank startup-page controls in this owned fixture.
const preload = path.join(root, 'observe.cjs');
fs.writeFileSync(preload, `
const fs = require('node:fs');
const Module = require('node:module');
const load = Module._load;
const seen = new WeakSet();
Module._load = function(name, ...rest) {
  const value = load.call(this, name, ...rest);
  if (['patchright', 'playwright-extra', 'playwright'].includes(name) &&
      value.chromium && !seen.has(value.chromium)) {
    seen.add(value.chromium);
    const launch = value.chromium.launchPersistentContext.bind(value.chromium);
    value.chromium.launchPersistentContext = async (...args) => {
      const start = performance.now();
      const ctx = await launch(...args);
      const launchSeconds = (performance.now() - start) / 1000;
      const startupPages = ctx.pages();
      if (process.env.OWNED_PAGE_CONTROL === 'empty') {
        for (const page of startupPages) await page.close();
      } else if (process.env.OWNED_PAGE_CONTROL === 'navigated') {
        for (const page of startupPages) await page.goto('data:text/html,owned-control');
      }
      const initialUrls = ctx.pages().map(page => page.url());
      let newPages = 0;
      const newPage = ctx.newPage.bind(ctx);
      ctx.newPage = async (...args) => { newPages++; return newPage(...args); };
      const close = ctx.close.bind(ctx);
      ctx.close = async (...args) => {
        fs.writeFileSync(process.env.OWNED_PAGE_TRACE, JSON.stringify({
          automation: name, initialUrls, newPages, launchSeconds,
          finalUrls: ctx.pages().map(page => page.url())
        }));
        return close(...args);
      };
      return ctx;
    };
  }
  return value;
};
`);

async function run(label, control, profile, allowPrivate, expectedPages) {
  const traceFile = path.join(root, label + '.json');
  const started = performance.now();
  const child = spawn(process.execPath, ['--require', preload, template], {
    env: { ...process.env, OWNED_PAGE_TRACE: traceFile, OWNED_PAGE_CONTROL: control },
    stdio: ['pipe', 'pipe', 'pipe'],
    timeout: 90000,
  });
  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => { stdout += chunk; });
  child.stderr.on('data', chunk => { stderr += chunk; });
  child.stdin.end(JSON.stringify({
    url: `http://127.0.0.1:${server.address().port}/`, profileDir: profile,
    headless: true, allowPrivate, timeout: 5000, waitSelector: 'h1',
  }));
  const exit = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({ code, signal }));
  });
  const trace = JSON.parse(fs.readFileSync(traceFile, 'utf8'));
  assert.equal(trace.newPages, expectedPages, label);
  assert.equal(exit.signal, null);
  if (allowPrivate) {
    assert.equal(exit.code, 0, stderr);
    const result = JSON.parse(stdout);
    assert.equal(result.status, 200);
    assert.equal(result.finalUrl, `http://127.0.0.1:${server.address().port}/`);
    const count = label === 'blank-second-visit' ? 2 : 1;
    assert(result.html.includes('OWNED_VISIT_' + count));
    assert(result.cookies.some(cookie => cookie.name === 'ownedVisits' && cookie.value === String(count)));
  } else {
    assert.notEqual(exit.code, 0, 'Default private-address guard must refuse navigation');
    assert.equal(stdout, '');
  }
  console.log(JSON.stringify({ label, ...trace, ...exit, seconds: (performance.now() - started) / 1000 }));
}

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const profile = path.join(root, 'profile');
    await run('blank-first-visit', '', profile, true, expectedBlankNewPages);
    await run('blank-second-visit', '', profile, true, expectedBlankNewPages);
    if (expectedBlankNewPages === 0) {
      await run('empty-startup', 'empty', path.join(root, 'empty'), true, 1);
      await run('navigated-startup', 'navigated', path.join(root, 'navigated'), true, 1);
      const before = hits;
      await run('private-address-refused', '', path.join(root, 'blocked'), false, 0);
      assert.equal(hits, before, 'Blocked request reached the owned HTTP server');
    }
    console.log('BROWSER_STARTUP_FIXTURE_PASSED');
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
