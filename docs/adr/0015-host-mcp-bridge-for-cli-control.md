# 0015. Host MCP bridge for CLI control

- **Status:** Proposed
- **Date:** 2026-10-05
- **Deciders:** OSS maintainers
- **Related:** [MCP capability audit](../integrations/mcp-capability-audit-2026-10-05.md)

## Context

The current `decepticon mcp serve` command passes stdio through to a Python
server inside the LangGraph container. That server has engagement and Blue Cell
observation tools, but it cannot invoke host launcher operations or inspect
the CLI-selected host workspace. Coding agents need both surfaces through one
MCP registration.

## Decision

The installed Go launcher owns the client-facing MCP stdio connection. It
connects to the existing Python MCP server as a child process, copies its tool
schemas and annotations, and forwards calls and results. Host-owned tools are
registered on the same server: service status, knowledge graph health, stop,
and update. Stop and update require an explicit confirmation argument.
The launcher captures command output for tool results and reserves stdout for
MCP JSON-RPC. A runtime tool name may not replace a host-owned name.

The host MCP connection remains available when the runtime is stopped. A
`decepticon_cli_connect_runtime` tool connects and publishes the runtime tools
after the services become available.

Local Blue sensor and web dashboard control run in a one-off CLI image through
Docker Compose so MCP and the interactive slash commands share their behavior.
Only `blue up|status|verify|stop` and `web up|down|url` are accepted; stop
operations require operator confirmation. The launcher exposes a bounded
service log tail separately from the interactive endless follow stream.
Opscontrol management and version-matched Agent Skill installation are also
host launcher operations. MCP validates their action and client enumerations;
installing or uninstalling opscontrol, and force-replacing a modified Skill,
require explicit operator confirmation.
Headless onboarding accepts only known environment setting names, validates
authentication and a deliberate telemetry choice, and generates unique
service credentials for new installs. Reset retains existing service
credentials and refuses missing or development defaults. This avoids replaying
an interactive wizard over MCP stdio or rotating database passwords without
recreating their volumes.
Complete removal is a separate destructive host tool. Its default preserves
the engagement workspace in the same backup location as the interactive CLI;
deleting workspace data requires a stronger, distinct confirmation phrase.
The backup runs after services stop and before images or configuration are
removed, so a backup failure leaves those assets available for recovery.

The bridge uses the official Go MCP SDK. This adds a top-level dependency to
the launcher, avoiding a hand-written MCP protocol implementation. The child
server remains the owner of engagement and Blue observation behavior.

## Consequences

- Claude Code and Codex retain one `decepticon mcp serve` registration.
- The launcher can add typed host operations without putting a Docker socket
  into the LangGraph container.
- Host controls work before LangGraph starts; engagement and Blue observation
  tools appear after a runtime connection.
- A named existing engagement can start headlessly through the launcher.
  Interactive onboarding still needs a separate setup contract.

## Alternatives considered

- Mounting the Docker socket into LangGraph grants the agent container broad
  host control and bypasses the existing launcher boundary.
- Duplicating all Python engagement tools in Go would create two behavior
  contracts for the same agents.
- Parsing and rewriting MCP JSON-RPC manually would add protocol maintenance
  and interoperability risk.
