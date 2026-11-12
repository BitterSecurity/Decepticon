#!/usr/bin/env node
/**
 * Generic Playwright fetcher — real Chrome channel (not bundled Chromium).
 *
 * Usage (driven by engine/executor.py):
 *   echo '{"url":"...", "profileDir":"/tmp/.p", "waitSelector":"article"}' | node playwright_real_chrome.js
 *
 * Outputs page HTML to stdout on success; errors to stderr with non-zero exit.
 *
 * NO-SITE-NAME RULE: this file must never branch on specific hostnames.
 * All site specifics come from the JSON input (url, waitSelector).
 *
 * Dependencies (install once on target machine):
 *   npm i -g playwright playwright-extra puppeteer-extra-plugin-stealth
 *   npx playwright install chrome    # system Chrome binary
 */

const fs = require('fs');
const dns = require('dns').promises;
const ipaddr = require('ipaddr.js');
const { chromiumOptions } = require('./chromium_options');

// Drain stdout fully before exiting. `process.exit()` can truncate a large
// HTML payload because it does not wait for pending stdout I/O (Node docs).
function writeStdoutAsync(payload) {
  return new Promise((resolve, reject) => {
    process.stdout.write(payload, (err) => (err ? reject(err) : resolve()));
  });
}

// Structured envelope so the Python side can (a) validate on real status /
// final URL and (b) bridge the browser-cleared cookies + UA into curl_cffi.
async function buildEnvelope(ctx, page, html, resp, automation) {
  let cookies = [];
  try { cookies = (await ctx.cookies()).map((c) => ({ name: c.name, value: c.value, domain: c.domain })); } catch (_e) {}
  let userAgent = '';
  try { userAgent = await page.evaluate(() => navigator.userAgent); } catch (_e) {}
  let finalUrl = '';
  try { finalUrl = page.url(); } catch (_e) {}
  let status = 0;
  try { status = resp ? resp.status() : 0; } catch (_e) {}
  return JSON.stringify({ html, finalUrl, status, cookies, userAgent, automation });
}

async function readStdinJson() {
  return await new Promise((resolve, reject) => {
    let data = '';
    process.stdin.on('data', (c) => (data += c));
    process.stdin.on('end', () => {
      try { resolve(JSON.parse(data || '{}')); }
      catch (e) { reject(e); }
    });
    process.stdin.on('error', reject);
  });
}

function isBlockedIp(host) {
  try {
    let ip = ipaddr.process(host); // unwrap IPv4-mapped IPv6 addresses
    if (ip.kind() === 'ipv6' && ip.match(ipaddr.parse('64:ff9b::'), 96)) {
      const bytes = ip.toByteArray();
      ip = ipaddr.fromByteArray(bytes.slice(12)); // NAT64 WKP embeds IPv4 in low 32 bits
    }
    const range = ip.range();
    return !['unicast'].includes(range);
  } catch (_e) {
    return false;
  }
}

async function classifyUrl(candidate, allowPrivate) {
  let parsed;
  try {
    parsed = new URL(candidate);
  } catch (e) {
    return { ok: false, reason: `parse_error:${e.message || e}` };
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    return { ok: false, reason: `scheme:${parsed.protocol.replace(':', '') || 'none'}` };
  }
  if (allowPrivate) {
    return { ok: true, reason: 'allow_private' };
  }
  const host = parsed.hostname;
  if (isBlockedIp(host)) {
    return { ok: false, reason: `ip_blocked:${host}` };
  }
  try {
    const records = await dns.lookup(host, { all: true });
    for (const record of records) {
      if (isBlockedIp(record.address)) {
        return { ok: false, reason: `resolves_internal:${host}->${record.address}` };
      }
    }
  } catch (_e) {
    return { ok: false, reason: 'resolve_failed_blocked' };
  }
  return { ok: true, reason: 'public' };
}

async function installSafetyGuard(page, allowPrivate) {
  await page.route('**/*', async (route) => {
    const check = await classifyUrl(route.request().url(), allowPrivate);
    if (!check.ok) {
      await route.abort('blockedbyclient');
      return;
    }
    await route.continue();
  });
}

async function main() {
  const args = await readStdinJson();
  const url = args.url;
  if (!url) { process.stderr.write('missing url\n'); process.exit(2); }

  const profileDir = args.profileDir || '/tmp/.insane_pw_profile';
  const waitSelector = args.waitSelector || null;
  const timeoutMs = args.timeout || 60000;
  const headless = args.headless ?? false;     // Akamai/etc detect headless
  const viewport = args.viewport || { width: 1366, height: 900 };
  const allowPrivate = args.allowPrivate === true;

  let chromium;
  let automation = 'playwright';
  try {
    // Patchright is a DROP-IN Playwright fork (same API) that closes the CDP
    // Runtime.enable leak Cloudflare/DataDome now detect. Preferred when
    // installed; it does its own patching, so NO stealth plugin is added.
    // Additive only: if patchright is absent we fall back to exactly the
    // previous playwright-extra(+stealth) → playwright behaviour.
    ({ chromium } = require('patchright'));
    automation = 'patchright';
  } catch (_e0) {
    try {
      ({ chromium } = require('playwright-extra'));
      const stealth = require('puppeteer-extra-plugin-stealth')();
      chromium.use(stealth);
      automation = 'playwright-extra+stealth';
    } catch (_e) {
      // Fallback to plain playwright (no stealth). Still uses channel:chrome.
      ({ chromium } = require('playwright'));
      automation = 'playwright';
    }
  }

  let ctx;
  try {
    // Patchright official best practice: channel:'chrome', headless:false,
    // no_viewport (JS: viewport:null), persistent context, and NO custom
    // headers/UA/flags. We only override viewport for patchright; plain
    // playwright keeps the fixed viewport it has always used.
    // Decepticon: launch the sandbox's system Chromium by executablePath when
    // provided (INSANE_CHROMIUM_PATH); else fall back to the real Chrome channel.
    const ctxOpts = { ...chromiumOptions(), headless };
    if (automation === 'patchright') {
      ctxOpts.viewport = null;     // == no_viewport=True (use real window size)
    } else {
      ctxOpts.viewport = viewport;
    }
    ctx = await chromium.launchPersistentContext(profileDir, ctxOpts);
    // Persistent Chromium normally opens a blank page during launch. Reuse
    // only that blank page in this newly created context; a restored/navigated
    // page must never inherit this request's navigation guard or authority.
    const page = ctx.pages().find((candidate) =>
      !candidate.isClosed() && candidate.url() === 'about:blank'
    ) || await ctx.newPage();
    await installSafetyGuard(page, allowPrivate);
    // Single shared deadline across warmup + main + reload navigations so the
    // first nav can't eat the whole budget and starve the rest.
    const deadline = Date.now() + timeoutMs;
    const rem = (cap) => Math.max(1000, Math.min(cap || timeoutMs, deadline - Date.now()));

    // Warmup hop: visit the site root first so Akamai-style bot managers
    // can run their JS sensor and set a resolved session cookie. Direct
    // landing on a search/deep URL is the classic first-hit rejection pattern.
    // Use domcontentloaded (not networkidle) — many SPAs keep analytics/xhr
    // open indefinitely and would hit the 90s timeout.
    try {
      const urlObj = new URL(url);
      const rootUrl = `${urlObj.protocol}//${urlObj.host}/`;
      if (rootUrl !== url) {
        await page.goto(rootUrl, { waitUntil: 'domcontentloaded', timeout: rem(90000) });
        await page.waitForTimeout(3500);   // let sensor JS finish
      }
    } catch (_e) {
      // warmup is best-effort; continue even if it hiccups
    }

    // Main page — DOM loaded then give the sensor a moment.
    let mainResp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: rem(90000) });
    await page.waitForTimeout(2500);

    if (waitSelector) {
      try {
        await page.waitForSelector(waitSelector, { timeout: rem(20000) });
      } catch (_e) {
        // Selector still missing — try one hard reload in case the first hit
        // landed on a challenge page and the sensor has just cleared.
        try {
          mainResp = await page.reload({ waitUntil: 'domcontentloaded', timeout: rem(90000) });
          await page.waitForTimeout(2000);
          try {
            await page.waitForSelector(waitSelector, { timeout: rem(10000) });
          } catch (_e2) {
            // Still no luck — caller validates HTML anyway.
          }
        } catch (_e3) {
          // reload failed — proceed with whatever we have
        }
      }
    } else {
      // Without a positive-proof selector, give the sensor a couple more seconds.
      await page.waitForTimeout(2000);
    }

    const html = await page.content();
    const payload = await buildEnvelope(ctx, page, html, mainResp, automation);
    await writeStdoutAsync(payload);  // flush fully before any exit
    process.exitCode = 0;
    return;                           // let finally close ctx, then exit naturally
  } catch (e) {
    process.stderr.write(`${e.name || 'Error'}: ${e.message || e}\n`);
    process.exitCode = 1;
    return;
  } finally {
    try { if (ctx) await ctx.close(); } catch (_e) {}
  }
}

main();
