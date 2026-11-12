---
name: open-web
description: "Resilient public-page reading and OSINT keyword search — web_search (allowlisted-provider OSINT) and web_fetch (curl_cffi TLS-impersonation grid + headless-browser fallback that gets past WAF/403/anti-bot). Use when a fetch is blocked, a page is JS-rendered, or you need open-web OSINT about a target/org."
allowed-tools: Read
metadata:
  subdomain: reconnaissance
  when_to_use: "blocked page, 403/402, WAF, cloudflare, akamai, datadome, captcha, JS-rendered page, open-web OSINT, read public page, reddit, x/twitter, youtube, search the web, third-party intel"
  tags: open-web, osint, web-fetch, web-search, waf-bypass
  upstream_ref: "adapted from fivetaku/insane-search (MIT) — SKILL.md R1–R7; decepticon_sandbox/web engine"
---

# Open-Web Acquisition — web_search / web_fetch

Two tools front the sandbox open-web engine (`decepticon_sandbox.web`):

- **`web_search(query, provider="duckduckgo")`** — keyword OSINT over an
  allowlisted search provider. Target-EXEMPT (it hits the provider, not the
  target), so no engagement scope is required. Use it to DISCOVER: the org's
  public footprint, exposed tech / version-specific advisories, leaked
  references, doc/changelog/status pages, third-party subdomains and assets
  named in the wild.
- **`web_fetch(url, selector="", device="auto")`** — read ONE page's content,
  escalating past WAF / anti-bot blocks. **RoE target-gated** (the `url` must be
  in `plan/roe.json` scope) and SSRF-safe. Prefer it over `curl`-in-bash
  whenever a public page is blocked, returns a challenge, or is JS-rendered.

Typical flow: **`web_search` to find a URL → `web_fetch` to read it.**

## How web_fetch escalates (you do NOT drive this — the engine does)

`web_fetch` runs an escalation ladder inside the sandbox automatically:

1. **Phase 0 — official public APIs.** Reddit / X(Twitter) / YouTube URLs are
   auto-routed to their no-auth endpoints (Reddit `.rss`, X tweet-result/oEmbed/
   syndication, YouTube `yt-dlp`) BEFORE any WAF grid. Just pass the normal page
   URL; a Phase-0 win shows `verdict` with `profile=phase0:<platform>`.
2. **curl_cffi TLS-impersonation grid.** Real Safari/Chrome/Firefox JA3/JA4
   fingerprints × URL transforms (mobile subdomain, …) × referer strategies,
   tried exhaustively. This clears most WAFs (Cloudflare TLS, F5, AWS WAF, …).
3. **Headless browser tier.** For JS challenges the curl grid can't clear
   (Cloudflare Turnstile, Akamai Bot Manager, DataDome), a local headless
   Chromium with stealth runs. There is **no Playwright-MCP in the sandbox** —
   this local browser IS the final rung. If it still fails, that is a real wall.

You never pick a TLS profile, transform, or browser — the engine detects the WAF
and chooses. Your only knobs are `selector` and `device`.

## R2 — HTTP 200 is NOT success

The engine validates every response (4-layer: challenge markers / body size /
cookie sensor / your `selector`). A 200 that is actually a challenge or empty
SPA shell is reported as a FAILURE, not content. Trust the tool's `verdict` /
`[web_fetch OK|FAILED]` line, never a raw status. **Always pass a `selector`**
(e.g. `article`, `#content`, `[class*='product']`) when you know the content
marker — it upgrades a "looks clean" guess into proven success.

## R6 — a give-up is NOT exhaustion (the discipline that matters)

When `web_fetch` returns `[web_fetch FAILED]` it includes `stop_reason=` and
`grid_exhausted=`. **Do not declare a page unreadable on the first FAILED.**

- **TERMINAL walls** — `stop_reason` is `auth_required` (login/paywall) or
  `not_found` (404). These are real: record it and move on; retrying cannot
  help.
- **Everything else** (`challenge`, `blocked`, `rate_limited`, or a
  non-exhausted grid) still has escalation left:
  - **rate_limited (429) is NOT terminal** — back off, then retry.
  - Retry once with **`device="mobile"`** (mobile TLS + `m.` subdomain often
    walk past a desktop block) and/or a precise **`selector`**.
  - Only after a terminal `stop_reason` may you honestly conclude the page is
    unreachable.

This is the whole point of the engine: the cheap path failing is the *start* of
escalation, not permission to stop.

## RoE & OSINT boundary

- `web_search` is OSINT → not scope-gated (allowlisted provider egress only).
- `web_fetch` is target-gated → the `url` must be in `plan/roe.json` scope; an
  out-of-scope fetch returns `ROE_REFUSED`. SSRF-blocked (internal/metadata/
  private) hosts are refused regardless.
- **Reading public pages is OSINT, not exploitation.** Use `bash`/`http_request`
  for ACTIVE probing of in-scope target infrastructure; use `web_fetch` to READ
  pages (the target's own blocked/JS pages, or in-scope public assets).

## Platform fast-paths (handled by Phase 0 — just pass the URL)

| Need | Pass to web_fetch |
|---|---|
| Reddit thread/sub | the `reddit.com/...` URL (auto `.rss`) |
| A specific tweet / X profile | the `x.com/.../status/...` or profile URL |
| YouTube metadata / captions | the `youtube.com/watch?v=...` URL (yt-dlp) |
| GitHub repo (metadata, stars, language) | the `github.com/<owner>/<repo>` URL → repos API JSON |
| npm package (deps, versions, maintainers) | the `npmjs.com/package/<pkg>` URL → registry JSON |
| PyPI package (versions, deps, urls) | the `pypi.org/project/<pkg>` URL → JSON API |
| Any WAF-protected article/page | the page URL — the grid + browser tier handle it |

GitHub/npm/PyPI return structured JSON (great for supply-chain / source recon)
instead of HTML — just pass the normal page URL and the engine routes it.

For keyword discovery on a platform (e.g. "what's said about X on Reddit"),
`web_search("site:reddit.com <topic>")` first, then `web_fetch` the result URLs.
