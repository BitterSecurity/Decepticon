# Vendored: insane-search engine

`decepticon_sandbox/web/` is adapted from **insane-search** by fivetaku
(https://github.com/fivetaku/insane-search), MIT-licensed — see <!-- NOTE-BIAS-OK -->
`INSANE_SEARCH_LICENSE`.

Decepticon adaptations on top of the upstream engine:
- per-hop RoE `scope_check` gating (threaded through probe / grid / phase0 /
  browser fallback) so out-of-scope hosts are refused, not just nftables-blocked
- `web_search` provider layer (`providers.py`) for allowlisted OSINT search
- in-sandbox local-Playwright only (no Playwright-MCP path; MCP is a
  Claude-session concept that does not exist inside the sandbox)
- SSRF safety hardened to fail-closed for the sandbox boundary
- workspace-scoped content offload + CLI envelope (`__main__.py`)
