# Shared public model catalog

`catalog.json` is the single public data authority for Decepticon OSS and SaaS:
model identity, LiteLLM upstream routing, public capabilities/cost metadata and
optional picker labels/reasoning choices. Add a public model here once. Never
copy a SaaS model inventory into a product runtime by hand.

The reviewed 2026-10-18 SaaS main refresh contains 202 public records, including
context-tier rates and their existing source/check-date metadata. It preserves
explicit product overrides; it does not independently re-audit provider prices
or publish a package. New metadata fields require local generator review before
consumers accept the data.

Run `uv run model-catalog/generate.py`, then
`uv run model-catalog/generate.py --check` and
`uv run model-catalog/test_catalog.py`. Commit the source and generated outputs.
The CI drift check rejects edits made only to generated files. No provider network
calls occur during generation or tests. Public metadata retains the existing
baseline, and is not a fresh claim about provider availability or current prices.

`overlay.json` holds local credential references, legacy/private local routes,
settings, provider admission and compatibility patches. It generates:

- `config/litellm.yaml`, retaining baseline routes/settings and now containing 92 routes;
- `config/litellm_prod_catalog.json`, now containing 136 optional routes,
  still enabled by `current_model_routes` only when required credentials exist;
- the CLI's `currentProdModels.ts` catalog, retaining legacy groups/aliases.

A new model for an admitted provider appends automatically. Providers absent from
an output's `providers` map remain disabled. The `exclude` list records baseline
models absent from that particular output; future models need no inventory edit.
Local JSON merge patches retain baseline differences (null removes a shared field).
Review existing overrides when changing a model because explicit local policy wins.
Local OAuth handlers and generic dynamic-provider support remain unchanged.

Do not put tenant routes, subscription auth, platform billing, deployment secrets,
private endpoints or `runyour/` managed inventories into public data. The preexisting
OSS optional `runyour/` routes are retained only in the OSS overlay with environment
references; no additional SaaS-private data is exported. Public routing may use
public provider URLs or symbolic environment references, never resolved secrets.

SaaS consumes a pinned snapshot of this file, verified by immutable OSS commit and
SHA-256. Its local generator is reviewed code, not downloaded/executed by sync.
A scheduled/manual workflow uses public git access and its existing `GITHUB_TOKEN`
to propose updates, with no cross-repository secret and no runtime network fetch.
SaaS overlays retain tenant/BYOK/billing/subscription policy, default new picker
models to no platform-credit eligibility, and require admission of new providers.
Subscription availability is explicitly separate from public API availability.

The two LiteLLM instances remain separate: local OSS proxy and SaaS Cloud Run proxy.
Merging a catalog update does not publish a package or deploy a proxy. An update
requires normal PR review and each product's established release process.

Tests execute the actual generator CLI with synthetic records in temporary trees:
one admitted-provider addition flows to routes (and SaaS picker), a new provider
remains disabled, manual generated drift fails, and private/duplicate records are
rejected. These are offline fixture scenarios, not provider inference evidence.
