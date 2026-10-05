# OSS MCP capability audit — 2026-10-05

**Verdict:** The MCP bridge is an engagement control and observation surface,
not a complete remote interface for Decepticon OSS. It registers 10 tools.
The installed CLI exposes service lifecycle, engagement selection and approval,
Blue Cell sensor control, plugins, and web startup outside MCP.

| Capability | Current MCP behavior | Gap |
|---|---|---|
| Graph and thread discovery | Lists assistants and recent threads | No plugin bundle control or CLI assistant selection |
| Start and stop | Creates a background LangGraph run; cancels an active run | MCP start does not select the CLI engagement workspace or validate a complete plan; `instruction` defaults to empty |
| Follow-up conversation | Queues a new turn behind the current run | Cannot immediately steer a running turn or answer a paused approval through a dedicated tool |
| Observation | Reads transcript, bounded live stream, and compact thread state | Transcript text clips at 2,000 characters; stream data at 600; large state values are omitted |
| Findings | Reads `graph.json` and exports SARIF from that graph | LangGraph container lacks the CLI-selected workspace mount; canonical `findings/FIND-*.md` and `report/` files are not exposed |
| Local source target | Passes a path string in `targets` | Does not copy or mount the coding agent's repository into the sandbox |
| Blue Cell | A `blue_cell` graph may be discoverable | No MCP sensor lifecycle, source/coverage check, event/incident read, or monitor control |
| Operations | None | `onboard`, `start`, `stop`, `update`, `/plugins`, and `/web` remain CLI operations |
| Tool metadata | All 10 tools register without MCP annotations | Clients cannot distinguish read-only tools from launch, cancel, and message actions by annotation |

## Evidence

- `decepticon.mcp_server.server.build_server` registers only
  `tools_lifecycle.py` and `tools_interactive.py`. Those modules define the 10
  tools above.
- `mcp_server.engagements.EngagementClient.start` puts the engagement name and
  scan mode in `configurable`, but no `workspace_path`, target mount, or CLI
  selected engagement. `tools_lifecycle.decepticon_start_engagement` accepts
  `instruction=""` and does not check the CLI planning marker.
- `clients/cli/src/hooks/useAgent.ts` supplies the CLI-selected engagement
  name and `/workspace` in every run config. `clients/cli/src/commands/planMode.ts`
  verifies the planning bundle before creating `.red-approved` for Red mode.
- Compose mounts the selected host engagement at `/workspace` in `sandbox`.
  Rendered Compose config has no `/workspace` mount in `langgraph`, where
  `decepticon mcp serve` executes the bridge.
- `mcp_server.findings` and the status tool only check `graph.json`. Current
  agent prompts write canonical `findings/FIND-*.md` and `report/` artifacts.
- `clients/cli/src/commands/blue.ts` implements `/blue up`, `verify`, sources,
  events, incidents, metrics, analyze, and stop in the interactive client.
  `clients/cli/src/commands/registry.ts` also registers `/plugins` and `/web`.

## Verification and limit

`go test ./...` and `go vet ./...` passed for the launcher. All 75 MCP unit
tests passed. A real MCP stdio client initialized the bridge, listed 10 tools,
and observed `instruction=""` in the start tool's input schema. A source-built
launcher installed the Skill into isolated Codex
and Claude homes and installed it again without changes. Rendered Compose
config confirmed the mount separation. The local Docker daemon was unavailable,
so a real installed-stack MCP engagement and Blue Cell operation were not
exercised during this audit. PR #855's LangGraph image build and ARM64 smoke
checks passed, but they did not exercise an end-to-end MCP engagement.

## Work required for full coding-agent parity

1. Bind MCP engagements to the selected workspace and enforce the same scope,
   planning, and approval contract as the interactive CLI. Define how a coding
   agent submits local source files to the sandbox.
2. Expose canonical finding and report artifacts, with validated paths and
   bounded reads. Make `findings_available` reflect those artifacts.
3. Expose Blue Cell sensor lifecycle, source coverage, events, incidents, and
   monitor state through a stable control API before adding MCP tools.
4. Add operator approval, plugin, web, and service controls only through
   explicit typed APIs. Keep destructive actions distinct from read-only tools.

The installed Agent Skill describes today's split honestly: MCP for the
supported engagement operations, interactive CLI for the remaining controls.
