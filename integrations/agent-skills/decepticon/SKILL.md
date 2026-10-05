---
name: decepticon
description: "Operate Decepticon OSS through its installed CLI and engagement MCP tools when the user asks to run or inspect an authorized security engagement or a local Blue Cell. Do not use for unrelated security questions or raw scanner commands."
license: Apache-2.0
metadata:
  version: 2.1.0
  homepage: "https://github.com/BitterSecurity/Decepticon"
---

# Decepticon operator

Use Decepticon as the user's security platform. The host `decepticon` command
manages the installation and opens an interactive CLI. The registered MCP
server exposes engagement control and observation to coding agents. These
interfaces cover different operations; choose the one that actually exists.

## Connect

1. Check `decepticon status`. If the installation is missing, the user can run
   `decepticon onboard`. `decepticon start` starts services and opens the
   interactive terminal; it is not a headless scan command.
2. For MCP, confirm `decepticon_list_graphs` works. A connection error means
   the stack or MCP registration needs attention. See the
   [integration guide](https://github.com/BitterSecurity/Decepticon/blob/main/docs/integrations/external-agents.md) for
   Claude Code and Codex registration.
3. The user can install this skill for both clients with
   `decepticon skill install` after a release containing that command.

## Choose the operation

| Request | Supported surface |
|---|---|
| Discover agents, list engagements, read status or a compact state summary | MCP `decepticon_list_graphs`, `decepticon_list_engagements`, `decepticon_engagement_status`, `decepticon_engagement_state` |
| Watch an engagement | MCP `decepticon_transcript` with `next_index`; `decepticon_watch` for a bounded live sample |
| Send a follow-up or stop a run | MCP `decepticon_send_message` or `decepticon_cancel_engagement` |
| Onboard, start, stop, update, inspect services | Host `decepticon` command |
| Start or stop a local Blue Cell sensor | Interactive CLI `/blue up`, `/blue stop` |
| Inspect a running Blue Cell | MCP `decepticon_blue_status`, `decepticon_blue_sources`, `decepticon_blue_events`, `decepticon_blue_incidents`, `decepticon_blue_notifications`, `decepticon_blue_body`, `decepticon_blue_search`, `decepticon_blue_timeline`; interactive CLI `/blue verify` and `/blue analyze` |
| List or toggle agent plugin bundles | MCP `decepticon_plugin_bundles`, `decepticon_plugin_enable`, `decepticon_plugin_disable`; interactive CLI `/plugins` |
| Control web dashboard or select the active CLI agent | Interactive CLI `/web`, `/agent` |

Do not invent MCP tools for Blue Cell setup or service
management. The slash commands above work inside Decepticon's interactive
terminal, not in the shell or the MCP bridge.

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
- `decepticon_start_engagement` exists, but the installed MCP bridge does not
  currently bind a new thread to the CLI's selected `/workspace` engagement.
  Use it only when the server's workspace mapping is explicitly configured and
  confirmed for that engagement. Otherwise start the engagement in the
  interactive CLI and observe its thread through MCP.
- A local repository path in MCP `targets` is passed as text. It is not copied
  into the sandbox; confirm the target code is actually mounted before asking
  Decepticon to analyze it.
- `decepticon_send_message` queues a new turn behind an active run. Do not tell
  the user it changed the current execution until a subsequent transcript or
  state confirms the effect.
- `decepticon_engagement_findings` reads a persisted `graph.json` only. An
  `available=false` result does not prove the absence of findings. For an
  installed CLI engagement, inspect the selected host workspace's
  `findings/FIND-*.md` and `report/` artifacts when available. Cite the
  actual artifact and verification status; do not invent a SARIF result.
- If a run is interrupted or fails, read the latest transcript and status
  before retrying. Stop polling when the user has the requested update or
  the run reaches a terminal state.

Read [reference.md](reference.md) for exact MCP arguments and
[examples.md](examples.md) for CLI and MCP handoffs.
