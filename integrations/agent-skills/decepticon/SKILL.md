---
name: decepticon
description: "Operate Decepticon OSS through its installed CLI and engagement MCP tools when the user asks to run or inspect an authorized security engagement or a local Blue Cell. Do not use for unrelated security questions or raw scanner commands."
license: Apache-2.0
metadata:
  version: 2.4.0
  homepage: "https://github.com/BitterSecurity/Decepticon"
---

# Decepticon operator

Use Decepticon as the user's security platform. The host `decepticon` command
manages the installation and opens an interactive CLI. The registered MCP
server exposes engagement control and observation to coding agents. These
interfaces cover different operations; choose the one that actually exists.

## Connect

1. Check MCP `decepticon_cli_status` when available, or run `decepticon status`
   on the host. If configuration is missing, use `decepticon_cli_onboard`
   with operator-approved settings or run the terminal wizard
   `decepticon onboard`. For an existing workspace, MCP
   `decepticon_cli_start` starts services without opening the interactive
   terminal; then call `decepticon_cli_connect_runtime`.
2. For MCP, call `decepticon_cli_connect_runtime` if engagement tools are
   absent after services start, then confirm `decepticon_list_graphs` works.
   A connection error means the stack needs attention. See the
   [integration guide](https://github.com/BitterSecurity/Decepticon/blob/main/docs/integrations/external-agents.md) for
   Claude Code and Codex registration.
3. The user can install this skill for both clients with
   MCP `decepticon_cli_skill_install` or `decepticon skill install` after a
   release containing that command. Use `source` for a local development bundle.

## Choose the operation

| Request | Supported surface |
|---|---|
| Discover agents, list engagements, read status or a compact state summary | MCP `decepticon_list_graphs`, `decepticon_list_engagements`, `decepticon_engagement_status`, `decepticon_engagement_state` |
| Watch an engagement | MCP `decepticon_transcript` with `next_index`; `decepticon_watch` for a bounded live sample |
| Send a follow-up or stop a run | MCP `decepticon_send_message` or `decepticon_cancel_engagement` |
| Inspect services and knowledge graph health | MCP `decepticon_cli_status`, `decepticon_cli_kg_health`, or host CLI |
| Stop services after the operator asks | MCP `decepticon_cli_stop` or host `decepticon stop` |
| Update after the operator asks | MCP `decepticon_cli_update` or host `decepticon update` |
| List or create local engagement workspaces | MCP `decepticon_cli_workspaces`, `decepticon_cli_create_workspace` |
| Start services for an existing engagement | MCP `decepticon_cli_start` with its workspace slug, then `decepticon_cli_connect_runtime` |
| Review a plan, findings, or report | MCP `decepticon_cli_list_artifacts` and bounded `decepticon_cli_read_artifact` |
| Approve the validated plan for Red | MCP `decepticon_cli_approve_red` after operator review, with `confirmed=true` |
| Configure a new installation or reset settings | MCP `decepticon_cli_onboard` after operator confirmation; terminal `decepticon onboard` is available for guided credential entry |
| Open the interactive CLI | Host `decepticon` command |
| Start, verify, or stop a local Blue Cell sensor | MCP `decepticon_cli_blue` with `action=up|status|verify|stop`; use a local upstream and optional absolute host log directory for `up` |
| Inspect a running Blue Cell | MCP `decepticon_blue_status`, `decepticon_blue_sources`, `decepticon_blue_events`, `decepticon_blue_incidents`, `decepticon_blue_notifications`, `decepticon_blue_body`, `decepticon_blue_search`, `decepticon_blue_timeline`; `decepticon_cli_blue` with `action=verify` checks coverage |
| List or toggle agent plugin bundles | MCP `decepticon_plugin_bundles`, `decepticon_plugin_enable`, `decepticon_plugin_disable`; interactive CLI `/plugins` |
| Control web dashboard or read service logs | MCP `decepticon_cli_web` with `action=up|down|url`; `decepticon_cli_logs` for a bounded recent sample |
| Inspect or manage opscontrol | MCP `decepticon_cli_opscontrol` with `action=status|install|uninstall`; require operator confirmation for changes |
| Install the version-matched coding-agent Skill | MCP `decepticon_cli_skill_install` with `client=codex|claude|both`; replacing a modified Skill requires confirmation |
| Completely remove the local installation | MCP `decepticon_cli_remove`; default backs up the workspace and requires `REMOVE DECEPTICON`, while deleting workspace data requires `DELETE ALL DECEPTICON DATA` |
| Select the active agent | Set the MCP engagement `assistant` parameter; interactive CLI `/agent` |

The interactive `/blue analyze` flow maps to `decepticon_send_message` with
`assistant="blue_cell"` after a thread exists. Keep the automatic monitor
and its notifications separate from this on-demand investigation.

For MCP onboarding, provide `DECEPTICON_AUTH_PRIORITY`, a working auth method
or credential, and an explicit `DECEPTICON_TELEMETRY=off|research` choice in
`settings`. Ask the operator to review the values before setting
`confirmed=true`. Tool output never includes credentials. Reset preserves
existing service passwords and refuses missing or known default passwords;
follow the returned reinstall guidance instead of editing database passwords.

- Poll `decepticon_blue_events` with its `next_after` cursor and
  `decepticon_blue_notifications` with its separate `next_after` cursor.
- Check `decepticon_blue_status` for collector and monitor health before
  interpreting missing events. Cite exact incident IDs and event sequences.

## Engagement workflow

- Obtain the user's explicit target scope and rules of engagement before any
  active testing. State both included and excluded assets. Do not infer
  authorization from a URL or repository path.
- For an existing CLI engagement, call `decepticon_list_engagements`, identify
  its `thread_id`, then read `decepticon_engagement_state` and
  `decepticon_transcript`. Keep the returned `next_index` for later reads.
- Start a new MCP thread only for the workspace selected by
  `decepticon_cli_start`. Use `assistant="soundwave"` while planning. Red and
  other active assistants require the current eight-document plan to be
  reviewed and approved. List and read all eight `plan/*.json` documents,
  present them to the operator, then call `decepticon_cli_approve_red` only
  after the operator approves. The MCP run checks the selected workspace and
  approval digest before dispatch.
- A local repository path in MCP `targets` is passed as text. It is not copied
  into the sandbox; confirm the target code is actually mounted before asking
  Decepticon to analyze it.
- `decepticon_send_message` queues a new turn behind an active run. Do not tell
  the user it changed the current execution until a subsequent transcript or
  state confirms the effect.
- `decepticon_engagement_findings` reads a persisted `graph.json` only. An
  `available=false` result does not prove the absence of findings. Use the
  artifact tools to inspect the selected workspace's `findings/FIND-*.md`
  and `report/` files. Cite the actual artifact and verification status.
- If a run is interrupted or fails, read the latest transcript and status
  before retrying. Stop polling when the user has the requested update or
  the run reaches a terminal state.

Read [reference.md](reference.md) for exact MCP arguments and
[examples.md](examples.md) for CLI and MCP handoffs.
