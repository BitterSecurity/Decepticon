# CLI Reference

## Commands

| Command | Description |
|---------|-------------|
| `decepticon` | Start the core services and open the terminal UI |
| `decepticon onboard` | Interactive setup wizard (provider, API key, model profile, LangSmith) |
| `decepticon onboard --reset` | Reconfigure even if `.env` already exists |
| `decepticon stop` | Stop all services |
| `decepticon status` | Show service status |
| `decepticon doctor [--json]` | Read-only installation and connection diagnostics |
| `decepticon logs [service]` | Follow service logs (default: `langgraph`) |
| `decepticon kg-health` | Diagnose the Neo4j knowledge graph |
| `decepticon update` | Explicitly refresh config files, Docker images, and the launcher binary when a release is available |
| `decepticon remove` | Uninstall Decepticon completely |
| `decepticon --version` | Show installed version |

> **Web dashboard** is *dynamic-spawn* — it does **not** come up with the default stack. Start it from inside the CLI with the `/web` slash command (which runs `docker compose --profile web up -d web`), then open `http://localhost:3000` (configurable via `WEB_PORT` in `.env`). See [Web Dashboard](web-dashboard.md).

### Scope and workflow selection

New engagements ask for a name and target web URL. The launcher generates and
approves the matching RoE, then opens Red ready for the first request. Plan is
optional; no scope JSON or separate approval step is needed for new engagements.

Saved workspaces keep their existing scope. For legacy name-only workspaces that
have no scope file, the manual recovery path remains:

```text
/scope import plan/roe-authorized.json --confirm
/mode red approve
```

Import validates the reviewed JSON and creates missing scope without granting
approval. The second command approves the canonical current RoE; no optional
Plan documents are required. `/mode plan` explicitly opens Soundwave; publishing
its seven documents leaves you in Plan. See [RoE provisioning](roe-provisioning.md)
for path handling, the canonical schema and the Python/container alternative.

Select a business workflow at launch:

```bash
decepticon start --workflow pentest --workflow-version 1
```

`start` is optional. Execution also accepts `DECEPTICON_WORKFLOW_ID` and
`DECEPTICON_WORKFLOW_VERSION`. Optional Plan uses separate
`DECEPTICON_PLANNING_WORKFLOW_ID` (default `planning`) and
`DECEPTICON_PLANNING_WORKFLOW_VERSION`. The workflow is pinned to the execution
thread; changing the launch selection does not rewrite a resume pin. Use a new
execution/thread to change business process. See the
[extension contract](design/extension-contract.md).

### `decepticon logs` — Service names

```bash
decepticon logs             # langgraph (default)
decepticon logs litellm     # LiteLLM proxy
decepticon logs postgres    # PostgreSQL
decepticon logs neo4j       # Neo4j graph database
decepticon logs sandbox     # Kali Linux sandbox
decepticon logs web         # Web dashboard
```

---

## Interactive Terminal UI

The interactive CLI is built with React 19 + [Ink](https://github.com/vadimdemedes/ink). It streams events from LangGraph in real time.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| Enter | Send the prompt or confirm the selected command/menu item |
| Tab | Complete the selected command or argument |
| Shift+Enter / Alt+Enter / Ctrl+J | Insert a newline; `\` then Enter also works |
| Up / Down | Move through suggestions, multiline input, or prompt history |
| Ctrl+R | Search prompt history; Enter restores a match, Esc returns |
| Ctrl+G | Edit the prompt in `$VISUAL` or `$EDITOR` |
| Left / Right, Home / End | Move by character or to the current line start/end |
| Ctrl+Left / Right, Alt+B / F | Move by word; punctuation separates words |
| Backspace / Delete | Delete before/after the cursor |
| Ctrl/Alt+Backspace, Ctrl+Delete / Alt+D | Cut the previous/next word when the terminal reports modifiers |
| Ctrl+A / E | Current line start/end (terminal convention, not Select All) |
| Ctrl+K / U / W, Ctrl+Y | Cut to line end/start or previous whitespace token; paste the last cut |
| Ctrl+_ | Undo the last input edit and restore the cursor |
| Alt+P | Open the configured model picker, preserving the draft |
| `?` on empty input | Toggle keyboard help; `?` inside a prompt stays literal |
| Ctrl+D on empty idle input | Press twice within 800 ms to exit; with text, delete forward |
| Ctrl+O | Toggle compact output and transcript detail |
| Ctrl+C | Pause a run; press again within 500 ms to cancel. When idle, clear the draft before exiting |
| Esc | Close the focused menu/search first; otherwise pause an active run without discarding queued messages. Return from transcript before pausing; idle Esc can clear waiting messages |

Shift+Enter needs a distinct terminal key sequence. Compatible Kitty/WezTerm/Ghostty terminals negotiate enhanced keys automatically; unsupported terminals keep their existing input mode. If a terminal sends ordinary Enter for Shift+Enter, use `\` then Enter or Ctrl+J. Decepticon does not rewrite terminal settings.

Drafts and the latest 200 distinct prompts persist per API endpoint and engagement.
They are private local files beneath `DECEPTICON_STATE_DIR`, `DECEPTICON_HOME`, or
`~/.decepticon` (in that order). Large pasted prompts keep their full content while
the editor displays a small window around the cursor. Terminal IME composition
remains dependent on the user's terminal; use Ctrl+G for longer input if needed.

### Modes and monitoring

New and existing engagements open in **Red / Decepticon**. Creating an engagement
asks only for its name. Selecting Red does not authorize operations: existing
scope validation and approval still apply. `/mode` opens a searchable Plan/Red/Blue
picker. `/mode plan`, `/mode red`, `/mode blue`, and `/mode red approve` remain
available. The footer distinguishes mode, agent and connection state.

Blue monitoring is independent of the selected conversation. While you talk to Red,
Blue notices still arrive. Compact notices link to `/alerts`; full analysis appears
only when requested. The footer distinguishes monitor connectivity, collector
coverage, AI analysis state, and unread notices. Unknown coverage is not healthy
coverage. Up to 500 recent structured notices are held in memory; older incidents
remain available through `/blue incidents` and the monitor's APIs.

### Sessions and waiting messages

- `/resume` returns to a paused checkpoint or opens the session picker when idle.
- `/sessions` searches the current engagement, including conversations beyond the
  previous 20-entry limit. `/sessions all` lists all engagements; select a session
  from the corresponding engagement to resume it.
- `/sessions legacy` lists conversations created without scope metadata. Linking
  one requires an explicit confirmation and rejects a checkpoint with a different
  engagement/workspace. Conversations are retained; they are not deleted.
- `decepticon --resume` opens the picker; `decepticon --resume <thread-id>` restores
  a specific session; `decepticon --continue` restores the latest scoped session.
  Startup restoration preserves the assistant and pending operator question.
- Messages sent while the agent works form a FIFO queue. `/queue` removes a
  selected message, `/queue edit N <message>` edits it, and `/queue clear` clears
  waiting messages. A paused run retains its waiting messages.

### Slash commands

| Command | Description |
|---------|-------------|
| `/help`, `/?` | List registered commands, aliases and shortcuts |
| `/shortcuts` | Show the keyboard shortcut reference |
| `/version`, `/about` | Show the running deployment version and CLI runtime |
| `/pwd`, `/cwd` | Show the target workspace and CLI process directory separately |
| `/clear`, `/new` | Start a fresh conversation when idle with no queued messages; saved server-side conversations remain |
| `/file <path>` | Read a prompt from a file |
| `/resume [message]` | Continue a paused run, or choose an existing conversation |
| `/sessions [all \| legacy]` | Browse saved conversations |
| `/mode [plan \| red [approve] \| blue]` | Select the conversation mode |
| `/plan` | Enter optional Plan mode; send the planning message afterward |
| `/model [model-id \| clear]` | Search the model catalog and select an override |
| `/agent` | Inspect or select an orchestrator |
| `/plugins` | List or toggle agent bundles |
| `/status` | Inspect the session, routing override and connections |
| `/queue [clear]` | Manage waiting messages; `/queue edit N <message>` edits one |
| `/alerts [incident-id]` | Inspect retained Blue incidents and full investigation details |
| `/export [markdown \| json] [path]` | Write the current conversation to a new private file |
| `/web [up \| down \| url]` | Manage the optional web dashboard |
| `/blue up 127.0.0.1:3000` | Connect the local sensor to a running service |
| `/blue status \| verify \| sources \| events \| incidents \| metrics \| analyze \| stop` | Inspect or manage Blue collection |
| `/quit`, `/exit` | Exit the chat; services stay running |

`/model` displays a catalog, not a claim that all providers are authenticated.
The footer shows a session override when present; automatic routing is labeled as
such, rather than guessing which model will serve the next request.

Aliases appear in both `/help` and slash autocomplete. `/plan` takes no arguments;
send your planning message after the mode changes. `/new` takes no title or prompt
argument and resets the active conversation's event state and thread, like `/clear`.
Previously printed terminal scrollback remains available.
It does not delete saved sessions or change the engagement, target scope or mode.
Finish or cancel paused/active work and handle `/queue` before using it.
`/quit` and `/exit` disconnect the CLI stream; server work continues. Messages
still waiting in the local queue are not submitted after exit.

#### Example: optional planning, then a separate conversation

1. Enter `/plan`, then send a planning message after the mode changes.
2. Use `/status` to inspect the current session and connections.
3. Finish or cancel the run and review `/queue`. Use `/sessions` to find saved
   conversations if you need to return to them later.
4. Enter `/new` to start another conversation in the same mode and engagement.
   This does not switch back to Red; use `/mode red` explicitly when appropriate.

These commands describe current `main`, including unreleased changes. Compare
`/version` with [version history](../CHAGELOG.md); merging source does not update
an installed launcher or CLI image. See [upgrade behavior](cli-experience.md#upgrade-behavior).

These conveniences follow the official [Claude Code commands](https://code.claude.com/docs/en/commands),
[Codex CLI commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
and [Gemini CLI commands](https://geminicli.com/docs/reference/commands/).
Their similarly named commands can have broader behavior: Decepticon's `/plan`
selects its existing optional planner, `/about` reports version/runtime, and `/pwd`
distinguishes the mounted target workspace from the CLI process directory.
Context compaction, chat forking and permission controls require separate runtime
support; no shortcuts for those capabilities are registered here.

### Read-only diagnosis and export

Run `decepticon doctor` or `decepticon doctor --json` on the host. Required checks
cover configuration, authentication presence, Docker, Compose and LangGraph.
Opscontrol and Blue are reported separately as optional capabilities. Checks have
bounded timeouts and print recovery commands; they never start, stop or reset a
service, rotate credentials, or print secret values. Authentication presence does
not establish that a remote provider login remains valid. A failed required check
returns a nonzero exit status; optional unavailable components do not.

The `langgraph` check verifies host-side `GET /ok` and then
`POST /assistants/search` for an exact `graph_id: decepticon` registration.
Docker's internal healthcheck only verifies `/ok` inside its container. A healthy
container therefore does not establish host connectivity or assistant readiness.
Startup and doctor now report the failed endpoint, transport error or HTTP status,
invalid catalog, or missing graph separately. The startup deadline bounds both
requests and retry delays; `DECEPTICON_STARTUP_TIMEOUT_SECONDS` remains available
for genuinely slow graph initialization. Increasing it does not repair an
unreachable published port.

The same startup budget now controls LiteLLM and LangGraph's Compose healthcheck
grace, so a fixed shorter health window cannot end startup first. Export a positive
integer in the launcher's environment, for example
`DECEPTICON_STARTUP_TIMEOUT_SECONDS=1200 decepticon start`. The default is 600
seconds; invalid launcher values fall back to 600. A successful readiness probe
marks the service healthy immediately, and the launcher still fails when its
overall wait expires. Running Compose directly requires a valid duration value.

For a host connectivity failure, check `LANGGRAPH_PORT` in the installation's
`.env` and compare `docker port decepticon-langgraph 2024/tcp` with that value
(use the corresponding container name for a named stack). Then test the host
endpoint with `curl --connect-timeout 3 --max-time 5 http://127.0.0.1:2024/ok`,
substituting the configured port. Keep the containers and data in place for
diagnosis; do not reset credentials or database volumes to address a network
timeout.

`/export json` and `/export markdown` export the current conversation only. Default
exports go to the selected workspace's `exports/` directory. Explicit paths are
relative to the CLI process, which runs inside the container for installed users.
Files use owner-only permissions and existing files are never overwritten. Exports
can contain the complete local evidence supplied in the conversation. MCP remains
the structured agent execution interface; `doctor --json` is diagnostics, not a
headless agent runner.

See [CLI experience and compatibility](cli-experience.md) for upgrade details and
reference designs.

---

## Environment Variables

These can be set in your `.env` file (configure with `decepticon onboard`) or as shell environment variables.

### Required (at least one LLM key)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key |
| `OPENAI_API_KEY` | OpenAI API key (fallback) |
| `GEMINI_API_KEY` | Google Gemini API key (fallback) |
| `MINIMAX_API_KEY` | MiniMax API key (fallback) |

### Model Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DECEPTICON_MODEL_PROFILE` | `eco` | Tier preset: `eco` (per-agent), `max` (all HIGH), or `test` (all LOW) |
| `DECEPTICON_AUTH_PRIORITY` | (built-in order; see [Models](models.md)) | Comma-separated AuthMethod priority — first method primary, rest are fallbacks. When unset, the factory's built-in `_DEFAULT_AUTH_PRIORITY` order applies. Methods whose credential isn't configured are skipped at runtime. |
| `DECEPTICON_AUTH_CLAUDE_CODE` | `false` | Set `true` to route Anthropic models via Claude Code OAuth (`auth/claude-*` in LiteLLM) |
| `DECEPTICON_AUTH_CHATGPT` | `false` | Set `true` to route OpenAI models via ChatGPT subscription OAuth (`auth/gpt-*`) |
| `DECEPTICON_AUTH_GEMINI` | `false` | Set `true` to route Google models via Gemini Advanced OAuth (`gemini-sub/*`) |
| `DECEPTICON_AUTH_COPILOT` | `false` | Set `true` for Microsoft Copilot Pro OAuth (`copilot/*`) |
| `DECEPTICON_AUTH_GROK` | `false` | Set `true` for xAI SuperGrok OAuth (`grok-sub/*`) |
| `DECEPTICON_AUTH_PERPLEXITY` | `false` | Set `true` for Perplexity Pro OAuth (`pplx-sub/*`) |
| `OLLAMA_API_BASE` / `OLLAMA_MODEL` | unset | When set, registers `ollama_chat/<OLLAMA_MODEL>` and enables the `ollama_local` AuthMethod |

See [Models](models.md) for the full Tier × AuthMethod matrix and chain examples.

### Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_MASTER_KEY` | Generated during onboarding | LiteLLM proxy admin key |
| `LITELLM_SALT_KEY` | Generated during onboarding | Encrypts provider credentials stored in LiteLLM |
| `POSTGRES_PASSWORD` | Generated during onboarding | PostgreSQL password |
| `NEO4J_PASSWORD` | Generated during onboarding | Neo4j password |

Older installations that still use the public Compose fallback credentials are
upgraded on the next `decepticon start`. The launcher backs up `.env` to
`~/.decepticon/.env.before-credential-migration`, rotates the LiteLLM admin
key and database passwords without deleting existing data, and retries safely
if the upgrade is interrupted. Each Compose project sharing the same
`DECEPTICON_HOME` upgrades its own database volumes on its first start after
the shared `.env` changes. It retains an old LiteLLM encryption salt when
needed so credentials already stored in the database remain readable. The
backup contains secrets and is written with owner-only permissions.

### Ports (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGGRAPH_PORT` | `2024` | LangGraph API server port |
| `LITELLM_PORT` | `4000` | LiteLLM proxy port |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `WEB_PORT` | `3000` | Web dashboard port |
| `TERMINAL_PORT` | `3003` | Terminal WebSocket bridge for the embedded CLI |

Neo4j ports (`7474` browser, `7687` bolt) are fixed in `docker-compose.yml`.

### C2 Framework

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPOSE_PROFILES` | _(empty)_ | Explicit profile override. Leave empty for normal use — heavyweight workloads are spawned on demand by the orchestrator via `ops_start(...)` ([ADR-0006](adr/0006-agent-driven-container-lifecycle.md)). |

Set explicitly only when you want a workload up at launch — e.g. `COMPOSE_PROFILES=cli,c2-sliver,ad,reversing` for CI regression runs against the whole matrix.

Currently allowlisted workloads (the agent can call `ops_start("X")` for any of these): `ad`, `c2-sliver`, `c2-havoc`, `reversing`, `cloud`, `mobile`, `phishing`, `forensics`, `ics`, `iot`, `supply-chain`, `wireless`. Future C2 frameworks (Havoc, Mythic) plug in as additional `c2-*` profile services.

### Observability (optional)

| Variable | Description |
|----------|-------------|
| `LANGSMITH_TRACING` | Set to `true` to enable LangSmith tracing |
| `LANGSMITH_API_KEY` | LangSmith API key |
| `LANGSMITH_PROJECT` | LangSmith project name (default: `decepticon`) |

### Debug

| Variable | Description |
|----------|-------------|
| `DECEPTICON_DEBUG` | Set to `true` for verbose debug output |
