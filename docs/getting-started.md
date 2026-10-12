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

Starts the core management and sandbox services, then opens the interactive terminal UI. Specialist services such as the C2 server and web dashboard start on demand.

**Web Dashboard** (browser, optional):

From the terminal UI, run `/web` (or `/web up`) to start the dashboard. It is then reachable at `http://localhost:3000`.

---

## First Real Engagement

1. Launch Decepticon (`decepticon`). To use the browser UI, run `/web` and open <http://localhost:3000>.
2. Create an engagement with one root HTTP(S) URL, host, or CIDR target and an authorization reference. Choose **Red** to start with a direct, machine-enforced RoE, or **Plan** for deeper planning.
3. In Plan mode, Soundwave interviews you and writes the eight-document bundle. Review it, then select Red and approve the validated bundle.
4. The Red orchestrator builds objectives under the approved RoE and runs specialist agents. Use `/mode plan|red|blue` in the CLI, or the mode selector in the web live view. Blue requires a connected local sensor.

> **Important**: Only run Decepticon against systems you own or have explicit written authorization to test. See the disclaimer in the main README.

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
| Web dashboard features | [Web Dashboard](web-dashboard.md) |
| Contributing to Decepticon | [Contributing](contributing.md) |
