# OSS MCP capability audit — 2026-10-05

**Verdict:** The MCP bridge is an engagement control and observation surface,
not a complete remote interface for Decepticon OSS. It registers 21 tools
after the Blue observation and plugin bundle additions. The installed CLI
still owns service lifecycle, engagement selection and approval, Blue Cell
sensor control, and web startup.

| Capability | Current MCP behavior | Gap |
|---|---|---|
| Graph and thread discovery | Lists assistants and recent threads; lists and toggles plugin bundles | No CLI assistant selection |
| Start and stop | Creates a background LangGraph run; cancels an active run | MCP start does not select the CLI engagement workspace or validate a complete plan; `instruction` defaults to empty |
| Follow-up conversation | Queues a new turn behind the current run | Cannot immediately steer a running turn or answer a paused approval through a dedicated tool |
| Observation | Reads transcript, bounded live stream, and compact thread state | Transcript text clips at 2,000 characters; stream data at 600; large state values are omitted |
| Findings | Reads `graph.json` and exports SARIF from that graph | LangGraph container lacks the CLI-selected workspace mount; canonical `findings/FIND-*.md` and `report/` files are not exposed |
| Local source target | Passes a path string in `targets` | Does not copy or mount the coding agent's repository into the sandbox |
| Blue Cell | Reads receiver and monitor status, sources, events, bodies, incidents, notifications, search, and timeline | No MCP sensor lifecycle, coverage verification command, or monitor control |
| Operations | Toggles optional plugin bundles | `onboard`, `start`, `stop`, `update`, and `/web` remain CLI operations |
| Tool metadata | Blue and plugin tools have read-only/change annotations | Original engagement tools still lack annotations |

## Evidence

- `decepticon.mcp_server.server.build_server` registers the original 10
  engagement tools plus `tools_blue.py` and `tools_plugins.py`.
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
  MCP now reads the receiver and monitor APIs but does not run these lifecycle
  commands. `/plugins` and MCP call the same runtime bundle API.

## Verification and limit

The original audit ran `go test ./...` and `go vet ./...` for the launcher,
75 MCP unit tests, and a real MCP stdio client that listed the original 10
tools and observed `instruction=""` in the start tool's input schema. A source-built
launcher installed the Skill into isolated Codex
and Claude homes and installed it again without changes. Rendered Compose
config confirmed the mount separation. The local Docker daemon was unavailable,
so a real installed-stack MCP engagement and Blue Cell operation were not
exercised during this audit. The later Blue and plugin tool tests use local
HTTP stand-ins; they do not prove an installed-stack engagement. PR #855's
LangGraph image build and ARM64 smoke checks did not exercise one either.

## Work required for full coding-agent parity

1. Bind MCP engagements to the selected workspace and enforce the same scope,
   planning, and approval contract as the interactive CLI. Define how a coding
   agent submits local source files to the sandbox.
2. Expose canonical finding and report artifacts, with validated paths and
   bounded reads. Make `findings_available` reflect those artifacts.
3. Expose Blue Cell sensor lifecycle and coverage verification through a stable
   control API. Existing observation tools already read events and incidents.
4. Add operator approval, web, and service controls through explicit typed
   APIs. Keep destructive actions distinct from read-only tools.

The installed Agent Skill describes today's split honestly: MCP for the
supported engagement operations, interactive CLI for the remaining controls.
