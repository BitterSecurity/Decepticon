# CLI workflows

Check `decepticon <command> --help` against the installed release before using
these examples. Values in angle brackets are placeholders.

## Diagnose an installation

```bash
decepticon --version
decepticon status --json
decepticon doctor --json
```

Read the diagnostic failures before proposing a fix. A missing installation
needs `decepticon onboard`; a stopped existing stack can use `decepticon start`.
`decepticon logs langgraph` follows logs until interrupted; use a terminal
timeout or stop reading after the relevant sample. Do not automatically reset,
update, or remove an installation to fix a connection failure.

## Open an authorized engagement

Start `decepticon start` in a persistent PTY. Read the engagement picker, then
select the intended saved workspace or create one with its name, authorized
targets and exclusions. Review the normalized scope and select any intended
discovery suggestions before confirming. Creation writes the machine RoE;
there is no separate `/mode red approve` step. Enter the objective after startup.

To select a supported business workflow at launch:

```bash
decepticon start --workflow pentest --workflow-version 1
```

Use `/help` inside the conversation to discover the installed slash commands.
`/mode plan` enters optional planning; it does not authorize a Red run.
`/mode red` returns to execution. Existing scope enforcement still applies.

`decepticon start --background` prepares the workspace and services without
opening the conversation. It can still prompt for setup/engagement input and
does not itself submit an objective or start an autonomous agent turn.

## Inspect, resume, or cancel

```bash
decepticon engagements
decepticon logs --engagement <name-or-thread-id> --limit 20
decepticon attach <engagement-name> --continue
# Or resume a known thread:
decepticon --resume=<thread-id>
```

The `logs --engagement` command reads conversation state without entering the
UI. `attach` and `--resume` open interactive sessions. Read the current state
before sending a follow-up; retrying a failed command does not prove the run
restarted. If the user asks to cancel the active agent turn:

```bash
decepticon cancel <name-or-thread-id>
```

Cancellation requests do not shut down services. For a stack shutdown requested
by the user, use `decepticon stop`.

## Blue Cell

Inside the interactive conversation, use `/blue status` to inspect a local
Blue Cell. `/blue up` and `/blue stop` control its lifecycle. `/blue verify`
and `/blue analyze` inspect coverage and request analysis. Read `/help` for the
version's supported arguments. No events with an unhealthy collector is not a
clean security result.

## Results

Use the selected host engagement workspace, not an arbitrary similarly named
folder. Read `findings/FIND-*.md` and `report/` when present, or use `/export`
in the interactive UI to save the conversation. Cite actual paths and separate
verified findings, hypotheses, interrupted runs, and missing evidence.

For optional MCP control, read [reference.md](reference.md). Keep its returned
thread ID and observation cursors; an MCP-created engagement name does not
rebind the sandbox to a different host workspace.
