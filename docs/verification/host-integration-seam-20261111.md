# Verification — host integration seam (Desktop glue relocation)

Local verification (GitHub Actions billing is down; this file is the record).
Branch `refactor/host-integration-seam-20261111` off OSS `main` `d26ddaa1`.

## What changed

Replaced the Desktop-host-specific glue in OSS core with a host-neutral
integration seam (`decepticon_core.contracts.host_integration`) and a null
default. Moved nothing new in; the Desktop implementation is relocated to the
SaaS vendor package in a separate, sequenced change. Design:
`docs/superpowers/specs/2026-11-11-host-integration-seam-design.md`,
contract: `docs/design/host-integration.md`.

- New: `decepticon_core/contracts/host_integration.py` — `HostIntegration`
  protocol, `HostModelSelection`, `HostPolicyError`, `NullHostIntegration`,
  `get_host_integration()` / `set_host_integration()`, discovery over the
  `decepticon.host_integration` entry-point group (allowlist-gated, single
  provider, null fallback).
- Rewrote 7 consumer sites to call through the seam instead of importing
  `decepticon.desktop_*`: `agents/build.py` (model selection + tool gate),
  `agents/middleware_slots.py`, `middleware/model_override.py`,
  `middleware/run_controls.py`, `tools/mcp/registry.py`,
  `server/plugins_api.py`, and `llm/factory.py`.
- Renamed the host-set single-model env `DECEPTICON_DESKTOP_MODEL` →
  host-neutral `DECEPTICON_FORCED_MODEL`; the old name is still read as a
  deprecated alias (new name wins).
- Removed from OSS: `desktop_policy.py`, `desktop_tools.py`,
  `desktop_dispatch.py`, `server/desktop_settings.py`, their tests, the
  `desktop-settings-policy.md` design and its verification logs.

## Results (local, `uv run`)

| Check | Result |
|---|---|
| `py_compile` (8 changed modules) | pass |
| `ruff check` (all changed files) | pass (1 import-order auto-fixed) |
| `basedpyright` (seam + 6 consumers) | **0 errors**, 4 warnings (all pre-existing, unrelated code) |
| null-seam import + default behavior | `get_host_integration()` → `NullHostIntegration`; `read_policy()` None; consumers import clean |
| core dependency guard (`test_no_runtime_deps`) | pass — core stays langchain-free |
| core public API stability (`test_public_api_stability`) | pass |
| new seam tests (`test_host_integration.py`) | pass |
| forced-model alias tests (new name + precedence + legacy) | pass |
| unit suites: middleware, agents, llm, tools | 2638 passed, 1 skipped (transformers absent) |

Backward compatibility: the pre-existing `DECEPTICON_DESKTOP_MODEL` tests in
`test_role_model_override.py` still pass unchanged, proving the legacy alias.

## Scope and limits

- OSS source change only. No release, version bump, image/pin change, or SaaS
  deployment. The SaaS vendor half (register the Desktop provider in
  `decepticon_saas`) is sequenced after SaaS consumes a seam-bearing OSS.
- Not touched: the Go launcher's `clients/launcher/cmd/desktop_*.go` and
  `docs/design/desktop-native-terminal.md`. Those are the client-adapter layer
  and a separate, larger relocation (the launcher has no plugin seam yet).
- CI not run (Actions billing down); evidence above is local.
