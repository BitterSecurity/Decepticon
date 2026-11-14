# Decepticon version history

All notable changes to the Decepticon project. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [Semantic Versioning](https://semver.org/) from `1.0.0`
onward (the `0.x` cycle is pre-stable per the core/framework/sdk split
design spec, §13.4).

Before opening or updating a PR, record its changes and PR link under
**Unreleased**. An open linked PR means proposed work; a merged linked PR means
integrated but unreleased work. Only dated release sections describe shipped work.
Maintain this single root `CHAGELOG.md`, preserving the full version history.
At release, move merged entries into a dated version section and add a comparison link.
Keep proposed and unmerged changes out of released sections.

## [Unreleased]


- Proposed [OpenTelemetry trace-context dependency fix #1195](https://github.com/BitterSecurity/Decepticon/pull/1195), not released: require `opentelemetry-api>=1.45.2` in the base install and update the coherent eleven-package OpenTelemetry lock cohort to 1.45.2/0.66b2. Fixes [CVE-2026-126360 / GHSA-mf5v-w3w3-f6cg](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-mf5v-w3w3-f6cg), where malformed untrusted trace context can cause excessive parsing time. Existing telemetry checks: 10 passed, zero skipped; actual patched propagator preserves valid trace context and rejects three oversized malformed inputs (up to 1,000,057 bytes) in under 1 ms each. Full 191-distribution lock audit reports zero known findings, without adding exclusions. The paired SDK patch also introduces metric-cardinality bounds; no model, provider, sandbox policy or tracing destination is changed. This Python dependency fix is separate from the user-deferred Agent Server Go finding.
