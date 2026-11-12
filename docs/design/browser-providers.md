# Design: replaceable engagement browser providers

Status: implemented (first provider: Obscura) · Baseline: `main` @ `bdb1016`

## Problem

Before this change the agent had no working browser. `browser_action`
(`tools/browser/tools.py`) was not bound to any agent, ran Playwright inside
the langgraph process where Playwright is not installed, shared one
process-wide session across engagements, and had no network-level scope
enforcement. `web_fetch` launches a one-shot headless Chromium only as its
last WAF escalation rung. Browser backends could not be exchanged without
rewriting the tool.

## Decision

Split the browser into a provider-neutral agent surface and replaceable
provider adapters that run **inside the engagement sandbox**.

```
langgraph (provider-neutral)                    engagement sandbox (OpenShell policy)
 browser_* typed tools ──sandbox.execute──▶  python3 -I /usr/local/lib/decepticon-sandbox/decepticon/sandbox_browser/__main__.py call
 untrusted-output quarantine                   └ provider adapter (obscura | <entry point>)
                                                    └ obscura mcp --http 127.0.0.1:<port>
```

* **Contract** — `decepticon/sandbox_browser/contract.py` defines the action
  set (navigate, snapshot with element refs, click/fill/type by ref or
  selector, forms, wait, evaluate, network/console lists, cookies, storage
  export/import, tabs, screenshot/PDF, close, capabilities) and the result
  shape. It is standard-library only and shared by both sides.
* **Provider interface** — `sandbox_browser/provider.py`:
  `capabilities() -> frozenset[str]`, `invoke(request) -> BrowserResult`,
  `shutdown()`. Actions a provider does not declare fail with
  `unsupported_action`; nothing is emulated.
* **Selection** — `DECEPTICON_BROWSER_PROVIDER` on the langgraph service
  (default `obscura`). `none` removes the tools. Built-ins live in
  `provider._BUILTIN`; external providers register a factory
  `(workspace: Path) -> BrowserProvider` under the
  `decepticon.browser_providers` entry-point group and must be importable in
  the sandbox image.
* **Agent surface** — `tools/browser/agent_tools.py` exposes 33 typed
  `browser_*` tools (names in `tools/browser/names.py`), bound to `recon`,
  `osint_operator`, `exploit` and `verifier`. The orchestrator keeps
  `tools=[]`. Screenshots/PDFs are written to
  `<workspace>/evidence/browser/` and returned as path + sha256, not base64.
  Session storage exports go to `<workspace>/.browser/storage/<name>.json`
  (0600) and the tool returns only the path and counts.
* **Host/plugin ownership** — if a host policy or a plugin supplies any tool
  named like an OSS browser tool, `build_tools` removes the whole OSS browser
  group for that role (`agents/build.py::_yield_browser_surface`), so two
  browser backends are never mixed and tool names never collide.

## Network scope

The browser runs in the sandbox, so the OpenShell network policy compiled
from `plan/roe.json` (`middleware/policy_compiler.py`) applies to every
connection it opens — top-level navigation, redirects, subresources, `fetch`
and form posts — exactly like `curl` or `nmap` in the same sandbox. The
default rule binaries glob `**` covers the provider binary; operators who
narrow `DECEPTICON_OPENSHELL_ROE_BINARIES` must include it
(`/usr/local/bin/obscura`). There is no separate application-layer scope
check for browser URLs; all browser output is quarantined
(`UNTRUSTED_TOOL_NAMES`). Providers must not route traffic to a
browser hosted outside the sandbox: that traffic would leave the policy
boundary.

## Obscura provider

[Obscura](https://github.com/h4ckf0r0day/obscura) (Apache-2.0) is a Rust
headless browser with V8. The sandbox image builds it from an exact commit
(`OBSCURA_COMMIT`, `cargo build --locked --features render`) because upstream
release archives carry no checksums or signatures. The adapter starts one
`obscura mcp --http` server per workspace on an ephemeral loopback port with a
random bearer token, keeps its state in `<workspace>/.browser/obscura/`
(owner-only), and reuses it across calls; an unhealthy server is replaced.
Obscura's own private-network guard is disabled
(`OBSCURA_ALLOW_PRIVATE_NETWORK=1`) so in-scope internal targets are
reachable; the OpenShell policy remains the network authority. OpenShell
0.1.6 enforces Obscura's connections through policy DNS and transparent TCP,
so no proxy configuration is needed (measured, see Verification); if a
runtime injects proxy variables, the adapter passes them to `--proxy`. The
loopback control channel never uses a proxy.

The runner is executed as an isolated script from the immutable image
bundle (`python3 -I /usr/local/lib/decepticon-sandbox/.../__main__.py`):
managed OpenShell sessions do not inherit the image `PYTHONPATH` or
`DECEPTICON_SKIP_BOOT`, and `-I` keeps workspace files from shadowing
imports. In script mode the runner sets `DECEPTICON_SKIP_BOOT=1` itself
because the sandbox ships only part of the `decepticon` package.

Known engine limits relevant to findings (verify with another method before
reporting): `alert`/`confirm`/`prompt` are no-ops and raise no dialog events;
CSP is not enforced; `javascript:` URLs are not executed; WebSocket,
EventSource and Workers are stubs; rendering differs from Chromium; there is
no file upload, hover or request interception.

## Adding a provider

1. Implement `BrowserProvider` in a module importable inside the sandbox.
2. Run the browser inside the sandbox; declare only the actions you
   implement.
3. Register it (built-in table or the entry-point group) and install its
   binary in `containers/sandbox.Dockerfile` with a pinned, verified source.
4. Set `DECEPTICON_BROWSER_PROVIDER=<name>`.

Candidates evaluated for later slices: agent-browser (Rust CLI + Chrome),
Ego Lite, Aside. A dedicated browser sandbox carrying the same compiled RoE
policy is the planned second slice; the runner's workspace/endpoint model is
kept transport-neutral for that move.

## Not changed

`web_fetch`'s Playwright escalation and the legacy `browser_action` /
owned-browser-v1 path are unchanged in this slice.

## Verification

* `tests/unit/sandbox_browser/`, `tests/unit/tools/test_agent_browser_tools.py`
* `tests/integration/test_sandbox_browser_obscura.py` — real Obscura round
  trip against a loopback fixture (skips without the binary).
* [Native OpenShell measurement](../verification/agent-browser-obscura-openshell-20261112.md)
  — in-scope navigation allowed, page-script and direct out-of-scope egress
  denied by the supervisor for `/usr/local/bin/obscura`.
