---
name: decepticon
description: Operate the Decepticon security platform through its CLI when the user asks to set up, diagnose, run, resume, or inspect a Decepticon engagement or local Blue Cell. Use for Decepticon operations, not general security advice or standalone scanner commands.
license: Apache-2.0
metadata:
  homepage: "https://github.com/BitterSecurity/Decepticon"
---

# Decepticon CLI

Wrap the installed `decepticon` command. Check its version and help before
choosing commands; installed releases may differ from repository `main`.

```bash
decepticon --version
decepticon --help
decepticon status --json
decepticon doctor --json
```

If the command is missing, use the project's
[installation guide](https://github.com/BitterSecurity/Decepticon#quick-start).
Do not install a similarly named Python package as a launcher replacement.
For first-time configuration use `decepticon onboard`; it needs an interactive
terminal and provider input. Never include credentials in a report.

## Run and resume

`decepticon start` starts services and opens an interactive terminal UI. Run it
in a persistent PTY when the host agent supports terminal interaction. Read the
screen, enter the requested input, and inspect the resulting screen before
sending another action. Without interactive terminal access, hand the exact
command to the operator. Do not claim that a background shell launch completed
an engagement.

```bash
decepticon start
decepticon --resume             # choose a saved session
decepticon --resume=<thread-id> # resume a known session
decepticon --continue           # continue the latest session
```

New engagements prompt for a name, in-scope targets and exclusions, then show
the normalized scope for review. Optional discovery suggestions are included
only when selected. Use the user's authorization; ask only for missing scope
information. Saved scopes are retained. There is no separate
`/mode red approve` command or approval marker. Plan is optional. Enter `/help` inside
the terminal for commands supported by that running version.

Slash commands such as `/status`, `/sessions`, `/mode`, `/blue`, and
`/export` are terminal input, not shell subcommands. Do not invent shell forms
like `decepticon scan`, `decepticon blue up`, or `decepticon start --target`.
See [examples.md](examples.md) for terminal, service, and result workflows.

## Inspect and report

Use `decepticon status --json` for service state and `decepticon doctor --json`
for diagnostics. Use `decepticon engagements` to list local workspaces and
`decepticon logs --engagement <name-or-thread-id> --limit 20` to replay recent
conversation messages. `decepticon logs langgraph` follows service logs; bound
the observation with the host terminal timeout or interrupt it after the needed
sample. Consult `logs --help` first.
A running container does not establish engagement progress or completion.

Read the conversation and actual workspace `findings/FIND-*.md` and `report/`
artifacts when available. Report observed state, evidence paths, verification
status, and remaining work. Missing artifacts do not establish a clean result.
Exiting the UI leaves services running; use `decepticon stop` when the user
requests stack shutdown. `update`, `onboard --reset`, and `remove` change the
installation: use them only when the requested operation calls for them.

`decepticon cancel <name-or-thread-id>` requests cancellation of an active
agent run and leaves services running. Use it when the user requests a run stop,
then inspect the transcript to report the observed outcome.

## Optional MCP integration

The CLI workflow does not require MCP. If the user already has Decepticon MCP
tools and asks for programmatic background control or observation, use
[reference.md](reference.md) for their arguments, cursors, workspace constraints,
and findings limitations. Registering MCP is documented in the
[integration guide](https://github.com/BitterSecurity/Decepticon/blob/main/docs/integrations/external-agents.md).
Do not run `decepticon mcp serve` as a one-shot shell query; it is a persistent
stdio server. MCP tools and skill retrieval do not grant target authorization.
