# 0013. Expose live Blue Cell observations through MCP

- **Status:** Proposed
- **Date:** 2026-10-05
- **Deciders:** OSS maintainers
- **Related:** [MCP capability audit](../integrations/mcp-capability-audit-2026-10-05.md)

## Context

The interactive CLI reads the Blue receiver and monitor over their local HTTP
APIs. A coding agent using MCP can observe a Red engagement but cannot inspect
the target events, incidents, and defensive messages behind the Blue Cell.
Repeating collector logic inside MCP would create a second evidence source.

## Decision

The MCP bridge reads the existing receiver and monitor APIs through bounded,
typed read-only tools. Event and notification reads preserve their sequence
cursors. Body access requires an exact opaque reference and limits the returned
preview. MCP propagates collector errors rather than returning an empty page.
The bridge marks these tools read-only in MCP metadata.

The sensor remains an independently managed local service. This decision does
not grant the engagement graph or the MCP bridge Docker control.

## Consequences

- **Easier:** coding agents can correlate Red activity with live target evidence
  and Blue incident messages through MCP.
- **Harder:** an MCP client must handle separate receiver and monitor outages.
- **Given up:** the MCP bridge cannot infer that a target was safe from an empty
  event page or a stopped collector.
- **Migration:** installed stacks need an updated LangGraph image; current
  `BLUE_SENSOR_URL` and `BLUE_MONITOR_URL` configuration remains in use.

## Alternatives considered

- **Read the receiver's SQLite files:** rejected because it would bypass its
  pagination and retention contract and require an additional volume mount.
- **Have the Blue agent summarize all events first:** rejected because the
  coding agent also needs raw evidence and independent cursor-based reads.
