# Host integration seam

A trusted library *host* — a desktop app, a multi-tenant control plane — may
provision authority into a running engine out of band: which model each role
runs, which external tools are approved, and dispatch observation. The engine
core stays host-neutral: it carries no host's schema or naming and consults a
provider through this seam instead.

## Contract

`decepticon_core.contracts.host_integration` defines:

- `HostIntegration` — the provider protocol the engine consults.
- `HostModelSelection(model, effort)` — the only structured value core reads
  back; the policy itself is an opaque handle the provider interprets.
- `HostPolicyError(code)` — raised by a provider when provisioned authority is
  violated or no longer current; the engine propagates it rather than
  swallowing it.
- `NullHostIntegration` — the default. `read_policy()` returns `None`,
  `policy_tools()`/`server_routes()` are empty. Every consumer guards on
  `read_policy() is not None`, so pure-OSS / red-only / CLI behavior is
  unchanged when no host is present.
- `get_host_integration()` / `set_host_integration()` — resolve the active
  provider (explicit override, else the single registered plugin provider,
  else the null default) and wire one directly.

## Registration

A host ships its implementation in its own plugin package and registers it:

```toml
[project.entry-points."decepticon.host_integration"]
my-host = "my_pkg.host:PROVIDER"
```

Resolution is allowlist-gated like plugin bundles: a provider may expose a
`bundle` attribute (e.g. `bundle = "vendor"`) to require that name in
`DECEPTICON_PLUGINS`. At most one provider may register; a second is ignored.
A load failure never degrades pure-OSS startup.

## What a host provider may do

- Select a per-role model/effort (`model_selection`) and force a single model
  via the host-neutral `DECEPTICON_FORCED_MODEL` env (legacy alias
  `DECEPTICON_DESKTOP_MODEL`).
- Supply host-approved external tools (`policy_tools`) and MCP-style
  connection/search/schema/call surfaces, under host-owned read-only custody.
- Contribute server routes (`server_routes`) such as a settings endpoint.
- Enforce freshness: a host atomic replacement revokes handles immediately
  (`require_current`), and the engine rechecks before returning results.

Scope enforcement, tool restriction and human approval remain
capability/runtime contracts; this seam neither confers authorization nor
expands a planning role's capability core. See
[extension-contract.md](extension-contract.md).
