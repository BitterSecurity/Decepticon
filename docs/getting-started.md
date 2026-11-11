# Getting Started

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2
- An LLM provider credential — one of:
  - **Tier-mapped API keys** (works out of the box): Anthropic, OpenAI, Google Gemini, MiniMax, DeepSeek, xAI, Mistral, OpenRouter, Nvidia NIM
  - **Local LLM**: Ollama (`OLLAMA_API_BASE` + `OLLAMA_MODEL`)
  - **Subscription OAuth** (no per-token billing): Claude Max/Pro/Team, ChatGPT Pro/Plus/Team, Gemini Advanced, Microsoft Copilot Pro, xAI SuperGrok, Perplexity Pro
  - **Other providers** (Groq, Cohere, Together, Fireworks, Perplexity API, Azure, AWS Bedrock, Replicate, custom OpenAI-compatible gateway): supported via `DECEPTICON_MODEL` / `DECEPTICON_LITELLM_MODELS` ad-hoc registration

That's it. Everything else runs inside containers.

---

## Install

```bash
curl -fsSL https://decepticon.red/install | bash
```

This installs the `decepticon` CLI to your system.

---

## Configure

```bash
decepticon onboard
```

The interactive setup wizard guides you through:

1. **Authentication** — API key, subscription OAuth (Claude / ChatGPT / Gemini / Copilot / SuperGrok / Perplexity), or local Ollama
2. **Provider** — choose one of the tier-mapped providers, configure OAuth, or point at a local Ollama
3. **Credentials** — API key, OAuth token, or endpoint URL (depending on auth method)
4. **Model Profile** — `eco` (balanced), `max` (performance), `test` (development)
5. **LangSmith** — Optional tracing for LLM observability

For detailed provider setup including OAuth configuration, see [Setup Guide](setup-guide.md).

Configuration is saved to `~/.decepticon/.env`. Run `decepticon onboard --reset` to reconfigure.

---

## Launch

**Terminal CLI** (default):
```bash
decepticon
```

Starts the core management and sandbox services, then opens the interactive terminal UI. Specialist services such as the C2 server start on demand.

---

## First Real Engagement

1. Launch Decepticon (`decepticon`).
2. In the CLI, choose **New engagement**, enter its name, in-scope targets and exclusions, then review the normalized scope. Optional discovery suggestions are included only when selected. The launcher writes the machine RoE and opens Red. No prewritten scope file or Plan session is required for this path. For an existing engagement with missing scope, import a reviewed RoE with `/scope import <file> --confirm`. There is no separate `/mode red approve` command.
3. Direct approved Red does not require planning documents. Use `/plan` (or `/mode plan`) explicitly for optional Soundwave discussion and planning, then send your planning message; publication leaves you in Plan. See [engagement workflow](engagement-workflow.md) and [RoE provisioning](roe-provisioning.md).
4. Use `/mode plan`, `/mode red`, or `/mode blue` within the CLI session, or the web live-view selector. Red runs specialist operations under the approved RoE; Blue investigation needs a connected local sensor.

The CLI accepts network targets such as domains, URLs, IPs/CIDRs and supported
globs, normalizes them to host-level scope, and previews dropped or expanded
entries. The web form has a separate stricter root-URL/IP-range validator;
its constraints are not the CLI's target contract. See
[engagement workflow](engagement-workflow.md) and [RoE provisioning](roe-provisioning.md).

> **Important**: Only run Decepticon against systems you own or have explicit written authorization to test. See the disclaimer in the main README.

### Find your way around the chat

Type `/` for the command menu, use Up/Down to select, and Enter to execute; Tab
completes a command. `/help` lists commands and aliases; `/shortcuts` lists keys.

| Task | Command |
|------|---------|
| Inspect version, session and connections | `/version`, `/status` |
| Check target workspace versus CLI directory | `/pwd` |
| Select a configured model | `/model` |
| Continue paused work or browse saved conversations | `/resume`, `/sessions` |
| Review or clear waiting messages | `/queue`, `/queue clear` |
| Start a fresh conversation in the same engagement and mode | `/new` |
| Leave the chat without stopping services | `/quit` or `/exit` |

`/new` accepts no arguments and requires idle state with no waiting messages.
Finish or cancel active/paused work first. It preserves saved conversations and
terminal scrollback. It does not create a new engagement. Leaving the chat
disconnects its stream; waiting local messages are not submitted after exit.

This guide describes current `main`; some controls are unreleased. Check
[version history](../CHAGELOG.md) and [upgrade behavior](cli-experience.md#upgrade-behavior)
against your installed version. See the [full command reference](cli-reference.md#slash-commands)
for aliases and Blue sensor commands.

---

## Stopping Services

```bash
decepticon stop     # Stop all services, keep data
make clean          # Stop + remove all volumes (resets everything)
```

---

## Check Service Status

```bash
decepticon status        # Show running services
decepticon logs          # Follow LangGraph logs (default)
decepticon logs litellm  # Follow a specific service's logs
decepticon kg-health     # Diagnose the Neo4j knowledge graph
```

---

## Next Steps

| Topic | Doc |
|-------|-----|
| All CLI commands and keyboard shortcuts | [CLI Reference](cli-reference.md) |
| All `make` targets | [Makefile Reference](makefile-reference.md) |
| Agent roles and middleware | [Agents](agents.md) |
| Model profiles and fallback chain | [Models](models.md) |
| Engagement workflow (RoE → Execution) | [Engagement Workflow](engagement-workflow.md) |
| Contributing to Decepticon | [Contributing](contributing.md) |
