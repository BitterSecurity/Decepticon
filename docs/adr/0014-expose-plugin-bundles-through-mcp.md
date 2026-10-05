# 0014. Expose plugin bundles through MCP

- **Status:** Proposed
- **Date:** 2026-10-05
- **Deciders:** OSS maintainers
- **Related:** [MCP capability audit](../integrations/mcp-capability-audit-2026-10-05.md)

## Context

The interactive CLI can list, enable, and disable agent plugin bundles through
the LangGraph server's `/_decepticon/bundles` API. Coding agents using MCP can
discover graphs but cannot activate an optional bundle before using one.

## Decision

Expose three explicit MCP tools for bundle listing, enabling, and disabling.
The bridge calls the existing API and propagates its errors. Bundle names are
validated before constructing a URL; the core `standard` bundle cannot be
disabled. MCP metadata marks listing read-only and disabling destructive.
Changes affect the running server session; persistence across restart remains
the existing `DECEPTICON_PLUGINS` setting.

## Consequences

- Coding agents can activate optional graph bundles without the interactive CLI.
- Clients can distinguish observation from a change to available agents.
- A stopped LangGraph server or an unknown bundle produces a tool error.

## Alternatives considered

- Duplicating `register_graph()` in MCP would diverge from the CLI and its
  server-owned runtime state.
- A generic server-command tool would hide the allowed actions and their
  effects from MCP clients.
