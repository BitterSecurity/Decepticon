# CLI Reference

## Commands

| Command | Description |
|---------|-------------|
| `decepticon` | Start the core services and open the terminal UI |
| `decepticon onboard` | Interactive setup wizard (provider, API key, model profile, LangSmith) |
| `decepticon onboard --reset` | Reconfigure even if `.env` already exists |
| `decepticon stop` | Stop all services |
| `decepticon status` | Show service status |
| `decepticon logs [service]` | Follow service logs (default: `langgraph`) |
| `decepticon kg-health` | Diagnose the Neo4j knowledge graph |
| `decepticon update` | Explicitly refresh config files, Docker images, and the launcher binary when a release is available |
| `decepticon remove` | Uninstall Decepticon completely |
| `decepticon --version` | Show installed version |

> **Web dashboard** is *dynamic-spawn* — it does **not** come up with the default stack. Start it from inside the CLI with the `/web` slash command (which runs `docker compose --profile web up -d web`), then open `http://localhost:3000` (configurable via `WEB_PORT` in `.env`). See [Web Dashboard](web-dashboard.md).

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

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+O` | Toggle Prompt ↔ Transcript mode |
| `Ctrl+G` | Cycle graph sidebar: Overview → Nodes → Flows |
| `Ctrl+B` | Toggle graph sidebar visibility |
| `Ctrl+C` | Cancel active stream / exit transcript / exit app |
| `Esc` | Exit transcript mode |

### View Modes

**Prompt Mode** (default)
- Compact view suitable for monitoring
- Sub-agent sessions collapsed
- Consecutive tool calls from the same agent are grouped
- Shows current objective and streaming agent output

**Transcript Mode** (`Ctrl+O`)
- Full event history
- Complete tool inputs and outputs
- All sub-agent details expanded
- Useful for debugging and reviewing what the agent actually did

### Graph Sidebar

The right-side panel visualizes the live Neo4j attack graph:

| View | Content |
|------|---------|
| **Overview** | High-level graph summary (node/edge counts, top hosts) |
| **Nodes** | Individual node list with type and properties |
| **Flows** | Attack chain paths discovered so far |

Cycle with `Ctrl+G`, hide/show with `Ctrl+B`. A Web Canvas auto-starts for pan/zoom interaction.

### Slash Commands

Available inside the interactive terminal UI:

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/?` | Show available commands and shortcuts |
| `/clear` | | Clear conversation history |
| `/file <path>` | `/f` | Load a prompt from a file and send it |
| `/resume [message]` | `/r`, `/continue` | Resume a paused run or continue previous session |
| `/model` | | Show or change the LLM model for this session |
| `/agent` | | Show or switch the active orchestrator for this session |
| `/plugins` | `/plugin` | List or toggle agent plugin bundles |
| `/web [up\|down\|url]` | `/dashboard` | Start, stop, or print the URL of the web dashboard |
| `/blue up 127.0.0.1:3000` | | Start the local web sensor for an already running service (Linux Docker) |
| `/blue status\|events\|incidents\|metrics\|analyze\|stop` | | Inspect live events and automatic incidents, run deeper Blue Cell analysis, or stop monitoring |
| `/quit` | `/exit` | Exit the CLI |

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
