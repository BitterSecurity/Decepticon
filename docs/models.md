# Models

Decepticon routes every LLM call through a [LiteLLM](https://github.com/BerriAI/litellm) proxy that abstracts Anthropic, OpenAI, Google, MiniMax, DeepSeek, xAI, Mistral, OpenRouter, Nvidia NIM, **local Ollama**, **self-hosted vLLM**, plus six subscription OAuth handlers (Claude Code / ChatGPT / Gemini Advanced / Copilot Pro / SuperGrok / Perplexity Pro) behind a single endpoint. The model assigned to each agent — and the model that takes over when the primary fails — is computed at startup from your **credentials inventory** plus the active **profile**.

You don't pick agent-by-agent models manually. You tell Decepticon which credentials you have, in what order of preference; it builds the chain.

---

## How model selection works

Three orthogonal axes:

| Axis        | Values                                                                                                | Decided by              |
|-------------|--------------------------------------------------------------------------------------------------------|--------------------------|
| **Tier**    | `HIGH` / `MID` / `LOW`                                                                                 | Agent (e.g. orchestrator → HIGH, recon → LOW), overridable by profile |
| **AuthMethod** | API: `anthropic_api` / `openai_api` / `google_api` / `minimax_api` / `deepseek_api` / `xai_api` / `mistral_api` / `openrouter_api` / `nvidia_api`<br/>OAuth: `anthropic_oauth` / `openai_oauth` / `google_oauth` / `copilot_oauth` / `grok_oauth` / `perplexity_oauth`<br/>Local: `ollama_local` | Your credentials inventory |
| **Profile** | `eco` / `max` / `test`                                                                                 | `DECEPTICON_MODEL_PROFILE` |

For each agent, Decepticon resolves a tier (from the profile) and walks your AuthMethod priority list, emitting the model identifier that method provides at that tier. The first hit is the primary; **every remaining hit is queued as a fallback in priority order**. langchain's `ModelFallbackMiddleware` walks the queue on primary failure, trying each method in turn until one succeeds.

### Tier × AuthMethod matrix

|                       | **HIGH**                                  | **MID**                                       | **LOW**                                       |
|-----------------------|-------------------------------------------|-----------------------------------------------|-----------------------------------------------|
| `anthropic_api`       | `anthropic/claude-opus-4-8`               | `anthropic/claude-sonnet-5`                   | `anthropic/claude-haiku-4-5`                  |
| `anthropic_oauth`     | `auth/claude-opus-4-8`                    | `auth/claude-sonnet-5`                        | `auth/claude-haiku-4-5`                       |
| `openai_api`          | `openai/gpt-5.5`                          | `openai/gpt-5.4`                              | `openai/gpt-5-nano`                           |
| `openai_oauth`        | `auth/gpt-5.5`                            | `auth/gpt-5.4`                                | `auth/gpt-5.4-mini`                           |
| `google_api`          | `gemini/gemini-2.5-pro`                   | `gemini/gemini-2.5-flash`                     | `gemini/gemini-2.5-flash-lite`                |
| `google_oauth`        | `gemini-sub/gemini-2.5-pro`               | `gemini-sub/gemini-2.5-flash`                 | — *(falls through)*                           |
| `minimax_api`         | `minimax/MiniMax-M3`                      | `minimax/MiniMax-M2.7-highspeed`              | — *(falls through)*                           |
| `deepseek_api`        | `deepseek/deepseek-v4-pro`                | `deepseek/deepseek-v4-flash`                   | `deepseek/deepseek-v4-flash`                   |
| `xai_api`             | `xai/grok-4.3`                            | `xai/grok-4-1-fast-reasoning`                 | — *(falls through)*                           |
| `grok_oauth`          | `grok-sub/grok-4.3`                       | `grok-sub/grok-4-1-fast-reasoning`            | — *(falls through)*                           |
| `mistral_api`         | `mistral/mistral-large-latest`            | `mistral/codestral-latest`                    | — *(falls through)*                           |
| `openrouter_api`      | `openrouter/anthropic/claude-opus-4-8`    | `openrouter/anthropic/claude-sonnet-4-6`      | `openrouter/anthropic/claude-haiku-4-5`       |
| `nvidia_api`          | `nvidia_nim/meta/llama-3.3-70b-instruct`  | `nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct` | `nvidia_nim/meta/llama-3.2-3b-instruct` |
| `copilot_oauth`       | `copilot/gpt-5.5`                         | `copilot/claude-sonnet-4-6`                   | `copilot/gpt-5.4-mini`                        |
| `perplexity_oauth`    | `pplx-sub/sonar-pro`                      | `pplx-sub/sonar`                              | — *(falls through)*                           |
| `ollama_local`        | `ollama_chat/<OLLAMA_MODEL>`              | `ollama_chat/<OLLAMA_MODEL>`                  | `ollama_chat/<OLLAMA_MODEL>`                  |

`ollama_local` collapses across tiers — local GPUs typically run a single model — and the slug is whatever you pulled (e.g. `qwen3-coder:30b`). When a method has no model at the requested tier (MiniMax LOW, Mistral LOW, ...), the resolver skips it and continues with the next method in your priority list.

The matrix above lists the primary tier-mapped providers. Decepticon resolves the same tier × AuthMethod chain for additional providers not shown above: managed cloud platforms (AWS Bedrock, GCP Vertex AI, Azure OpenAI), further direct APIs (Groq, Together, Fireworks, Cohere, Moonshot, Z.ai, DashScope, Cerebras, Xiaomi MiMo, Baidu Qianfan), multi-vendor gateways (GitHub Models, Hugging Face, OpenCode, Vercel AI Gateway, Cloudflare AI Gateway, Venice, NanoGPT, Synthetic, ZenMux), and local / self-hosted OpenAI-compatible servers (vLLM, Ollama Cloud, LM Studio, llama.cpp, and custom endpoints). `AuthMethod` and `METHOD_MODELS` in `packages/decepticon-core/decepticon_core/types/llm.py` are the authoritative source for the full provider list and each provider's tier→model mapping.

### Current direct API models (2026-10-04)

With the matching provider API key, these model IDs are available as explicit
`DECEPTICON_MODEL_<ROLE>` overrides:

| Provider | Model IDs |
|----------|-----------|
| OpenAI | `openai/gpt-6-astra`, `openai/gpt-6.1-sol`, `openai/gpt-6-luna` |
| Anthropic | `anthropic/claude-fable-5-1`, `anthropic/claude-opus-5-5`, `anthropic/claude-sonnet-5-5` |
| Google | `gemini/gemini-3.8-flash` |
| xAI | `xai/grok-4.7` |

For example, set `DECEPTICON_MODEL_DECEPTICON=openai/gpt-6.1-sol` and supply
`OPENAI_API_KEY`. The GPT-6 routes use the Responses API for agent tool calls.
These direct API IDs do not imply availability on ChatGPT, Claude Code, Gemini,
or Grok subscriptions. Existing tier defaults remain in place until their
cost and reliability are evaluated on Decepticon workloads.

---

## Profiles

`DECEPTICON_MODEL_PROFILE` (default: `eco`) controls which tier each agent runs at.

### `eco` — per-agent tier (production default)

Each agent runs at the tier suited to its workload:

| Tier  | Agents                                                                                          |
|-------|-------------------------------------------------------------------------------------------------|
| HIGH  | `decepticon`, `exploit`, `exploiter`, `patcher`, `contract_auditor`, `analyst`, `vulnresearch`   |
| MID   | `detector`, `verifier`, `blue_cell`, `postexploit`, `ad_operator`, `cloud_hunter`, `reverser`, `phisher`, `mobile_operator`, `iot_operator`, `ics_operator`, `forensicator`, `supply_chain_operator` |
| LOW   | `soundwave`, `recon`, `scanner`, `wireless_operator`, `osint_operator`                          |

### `max` — every agent on HIGH

For high-value targets where accuracy outweighs cost. Forces every agent to the HIGH tier.

### `test` — every agent on LOW

For development / CI. Forces every agent to the cheapest tier (Haiku-class).

---

## Credentials inventory

Your inventory is built at startup from environment variables, written by `decepticon onboard`.

```bash
# Priority order (first = preferred). When unset, the factory's built-in
# `_DEFAULT_AUTH_PRIORITY` order applies (OAuth subscriptions ahead of their
# paid per-token peers, hosted providers before local endpoints, with
# unconfigured methods skipped at runtime). See `_DEFAULT_AUTH_PRIORITY` in
# `decepticon/llm/factory.py` for the full ordered list.
DECEPTICON_AUTH_PRIORITY=anthropic_oauth,openai_api

# Set true if you have an active Claude Code OAuth subscription
# (anthropic_oauth in the priority list above).
DECEPTICON_AUTH_CLAUDE_CODE=true

# Per-method credentials. Placeholder values (`your-..-key-here`) are
# treated as "not configured" and silently dropped from the inventory.
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
MINIMAX_API_KEY=eyJ...
DEEPSEEK_API_KEY=sk-...
XAI_API_KEY=xai-...
MISTRAL_API_KEY=...
OPENROUTER_API_KEY=sk-or-...
NVIDIA_API_KEY=nvapi-...

# Local LLM (no API key — point at your Ollama server)
OLLAMA_API_BASE=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3-coder:30b
```

The factory walks the priority list, drops methods whose detection check fails (placeholder API key, or `DECEPTICON_AUTH_CLAUDE_CODE=false`), and uses what's left.

---

## Fallback chain examples

All examples assume the `eco` profile.

### Single API key (Anthropic only)

```
DECEPTICON_AUTH_PRIORITY=anthropic_api
ANTHROPIC_API_KEY=sk-ant-...
```

| Agent (tier)     | Primary                       | Fallback |
|------------------|-------------------------------|----------|
| decepticon (HIGH)| `anthropic/claude-opus-4-7`   | —        |
| detector (MID)   | `anthropic/claude-sonnet-4-6` | —        |
| recon (LOW)      | `anthropic/claude-haiku-4-5`  | —        |

No fallback — only one credential.

### Single API key (OpenAI only)

```
DECEPTICON_AUTH_PRIORITY=openai_api
OPENAI_API_KEY=sk-...
```

| Agent (tier)     | Primary               | Fallback |
|------------------|------------------------|----------|
| decepticon (HIGH)| `openai/gpt-5.5`      | —        |
| detector (MID)   | `openai/gpt-5.4`      | —        |
| recon (LOW)      | `openai/gpt-5-nano` | —        |

### Claude Code OAuth + Anthropic API (subscription primary, paid fallback)

```
DECEPTICON_AUTH_PRIORITY=anthropic_oauth,anthropic_api
DECEPTICON_AUTH_CLAUDE_CODE=true
ANTHROPIC_API_KEY=sk-ant-...
```

| Agent (tier)     | Primary                  | Fallback                         |
|------------------|---------------------------|----------------------------------|
| decepticon (HIGH)| `auth/claude-opus-4-7`   | `anthropic/claude-opus-4-7`     |
| detector (MID)   | `auth/claude-sonnet-4-6` | `anthropic/claude-sonnet-4-6`   |
| recon (LOW)      | `auth/claude-haiku-4-5`  | `anthropic/claude-haiku-4-5`    |

OAuth runs primary (no API cost). When the subscription quota hits, fallback drops to the paid API key — same model family, same quality.

### Mixed providers (Anthropic + OpenAI)

```
DECEPTICON_AUTH_PRIORITY=anthropic_api,openai_api
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

| Agent (tier)     | Primary                       | Fallback                |
|------------------|-------------------------------|-------------------------|
| decepticon (HIGH)| `anthropic/claude-opus-4-7`   | `openai/gpt-5.5`        |
| detector (MID)   | `anthropic/claude-sonnet-4-6` | `openai/gpt-5.4`        |
| recon (LOW)      | `anthropic/claude-haiku-4-5`  | `openai/gpt-5-nano`   |

Cross-provider fallback — when Anthropic hits a rate limit or outage, OpenAI takes over seamlessly.

### Local Ollama only (offline / cost-free)

```
DECEPTICON_AUTH_PRIORITY=ollama_local
OLLAMA_API_BASE=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3-coder:30b
```

| Agent (tier)     | Primary                          | Fallback |
|------------------|----------------------------------|----------|
| decepticon (HIGH)| `ollama_chat/qwen3-coder:30b`    | —        |
| detector (MID)   | `ollama_chat/qwen3-coder:30b`    | —        |
| recon (LOW)      | `ollama_chat/qwen3-coder:30b`    | —        |

Same model across all tiers because local hardware typically can't
run three different models simultaneously. The `ollama_chat/` provider
routes to Ollama's `/api/chat` endpoint, the only one that supports
tool/function calling — the legacy `ollama/` provider hits
`/api/generate` and is rejected at LiteLLM-config-merge time because
Decepticon agents always emit tool calls.

Two probes guard the wiring end-to-end:

1. **`decepticon onboard`** — the wizard hits `/api/tags` and
   `/api/show` on the host, filters to models that report `tools` in
   their capabilities, and presents only that list as the
   `OLLAMA_MODEL` choice. If Ollama is unreachable or has no
   tool-capable models pulled, the wizard refuses to write `.env`
   and prints the exact remediation steps.

2. **litellm container startup** — re-runs the same checks from
   inside the container, the only place that can detect
   `OLLAMA_HOST=127.0.0.1`-only bindings (which look fine to the
   wizard's host probe but are invisible from the container). Every
   diagnostic appears in `decepticon logs litellm` prefixed with
   `[decepticon ollama]`.

### Local Ollama + cloud fallback

```
DECEPTICON_AUTH_PRIORITY=ollama_local,anthropic_api
OLLAMA_API_BASE=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3-coder:30b
ANTHROPIC_API_KEY=sk-ant-...
```

| Agent (tier)     | Primary                          | Fallback                       |
|------------------|----------------------------------|--------------------------------|
| decepticon (HIGH)| `ollama_chat/qwen3-coder:30b`    | `anthropic/claude-opus-4-7`    |
| detector (MID)   | `ollama_chat/qwen3-coder:30b`    | `anthropic/claude-sonnet-4-6`  |
| recon (LOW)      | `ollama_chat/qwen3-coder:30b`    | `anthropic/claude-haiku-4-5`   |

Local model handles routine work; when the local model fails (OOM,
context overflow, hardware fault), Anthropic takes over for that
request only.


### Local llama.cpp (GGUF) only

```
DECEPTICON_AUTH_PRIORITY=llamacpp_local
LLAMACPP_API_BASE=http://host.docker.internal:8080/v1
LLAMACPP_MODEL=qwen2.5-coder-7b-instruct-q4_k_m
```

| Agent (tier)     | Primary                                                  | Fallback |
|------------------|----------------------------------------------------------|----------|
| decepticon (HIGH)| `llamacpp/qwen2.5-coder-7b-instruct-q4_k_m`              | —        |
| detector (MID)   | `llamacpp/qwen2.5-coder-7b-instruct-q4_k_m`              | —        |
| recon (LOW)      | `llamacpp/qwen2.5-coder-7b-instruct-q4_k_m`              | —        |

Run a GGUF model with llama.cpp's OpenAI-compatible server:

```bash
llama-server -m /path/to/qwen2.5-coder-7b-instruct-q4_k_m.gguf --port 8080
# server listens on http://localhost:8080/v1
```

Like Ollama, the model collapses across tiers — `llama-server` runs one
GGUF at a time. The `llamacpp/<model>` route remaps to LiteLLM's
`openai/` provider (since llama.cpp does not have a dedicated LiteLLM
provider) plus a custom `api_base` pointed at `LLAMACPP_API_BASE`. This
is the integration the maintainer recommended on issue #151 — it reuses
the existing LiteLLM provider machinery and avoids vendoring
`llama-cpp-python` as a runtime dependency.

Tool calling works as long as the GGUF was trained or fine-tuned with
function-calling support (Qwen 2.5 Coder, Llama 3.1 Instruct, Mistral
Small Instruct all qualify). The `llama-server` request format is
identical to OpenAI's; LiteLLM translates the agent's tool calls
through unchanged.

### MiniMax-only (LOW gap)

```
DECEPTICON_AUTH_PRIORITY=minimax_api
MINIMAX_API_KEY=eyJ...
```

| Agent (tier)     | Primary                | Notes                                     |
|------------------|------------------------|-------------------------------------------|
| decepticon (HIGH)| `minimax/MiniMax-M3` | OK                                        |
| detector (MID)   | `minimax/MiniMax-M2.7-highspeed` | OK                                        |
| recon (LOW)      | *(role unassigned)*    | MiniMax has no LOW model and no fallback method |

The Recon/Scanner/Soundwave roles fail to initialize. Add a second AuthMethod (e.g. `openai_api`) to fill the LOW slot.

---

## Failover behavior

`ModelFallbackMiddleware` (from `langchain.agents.middleware`) watches every LLM call. On primary failure (provider outage, 429 rate limit, context overflow, network error), it transparently retries each queued fallback in order until one succeeds. Agents see no interruption — same conversation history, same tool call.

The middleware receives the full chain `[primary, *fallbacks]` from `LLMFactory.get_fallback_models(role)`. If the user has all five AuthMethods configured, that's a five-deep chain; with a single credential it's primary-only and the middleware short-circuits. The chain length scales with credentials inventory — no upper cap, no silent truncation. Only when every method fails does the agent surface the error.

---

## LiteLLM proxy

All traffic flows through the LiteLLM container on port 4000. The proxy provides:

- **Unified API** — agents call one endpoint, model identifier picks the backend
- **Usage tracking** — tokens per model per agent role
- **Rate limiting** — per-provider knobs
- **Cost attribution** — billing data aggregated across providers

Configuration: `config/litellm.yaml`. Authentication: `LITELLM_MASTER_KEY` in `.env`.

---

## Adding a model

To wire in a new provider model:

1. Add a `model_list` entry to `config/litellm.yaml` with the LiteLLM `provider/model` identifier and the env var that holds the key.
2. Add the model identifier to the appropriate cell of `METHOD_MODELS` in `decepticon_core.types.llm`.
3. If introducing a new AuthMethod, also add it to `AuthMethod`, the factory's `_API_METHOD_ENV` map, and the onboard wizard's option list.

Tests in `tests/unit/llm/test_models.py` will catch dropped tiers or missing matrix entries.

### Dynamic model registration (Ollama, custom gateways, ad-hoc overrides)

You don't need to edit YAML to add an Ollama model. Set the env vars
below and `litellm_dynamic_config.py` registers the route at proxy
startup:

| Env var | What it does |
|---------|---|
| `OLLAMA_MODEL=<tag>` + `OLLAMA_API_BASE=<url>` | Registers `ollama_chat/<tag>` automatically. Used by the `ollama_local` AuthMethod. |
| `DECEPTICON_MODEL=<provider/model>` | Registers a global override (e.g. `groq/llama-3.3-70b-versatile`). |
| `DECEPTICON_MODEL_<ROLE>=<provider/model>` | Per-role override (e.g. `DECEPTICON_MODEL_RECON=ollama_chat/llama3.2`). |
| `DECEPTICON_LITELLM_MODELS=<a,b,c>` | Bulk register multiple ids without editing YAML. |
| `CUSTOM_OPENAI_API_BASE` + `CUSTOM_OPENAI_API_KEY` | OpenAI-compatible gateway. Use `custom/<model>` in the override env. |

The proxy logs `[decepticon] registered N dynamic model route(s)` at
startup so you can confirm what got picked up.

---

## Subscription OAuth Providers

Use monthly subscriptions instead of per-token API billing. All providers use custom LiteLLM handlers that authenticate via OAuth/session tokens. ChatGPT in particular reads from the Codex CLI credential store (`~/.codex/auth.json`), so a host-side `codex login` flows into the running container without a rebuild.

| Subscription | AuthMethod | Models | Handler |
|---|---|---|---|
| Claude Max/Pro/Team | `anthropic_oauth` | auth/claude-opus, sonnet, haiku | `claude_code_handler.py` |
| ChatGPT Pro/Plus/Team | `openai_oauth` | auth/gpt-5.5, gpt-5.4, gpt-5.4-mini (+ gpt-5.3-codex for code roles) | `codex_chatgpt_handler.py` (reads `~/.codex/auth.json`) |
| Gemini Advanced | `google_oauth` | gemini-sub/gemini-2.5-pro, flash | `gemini_handler.py` |
| Copilot Pro | `copilot_oauth` | copilot/gpt-5.5, claude-sonnet-4-6, gpt-5.4-mini (+ gpt-5.3-codex) | `copilot_handler.py` |
| SuperGrok | `grok_oauth` | grok-sub/grok-4.3, grok-4-1-fast-reasoning | `grok_handler.py` |
| Perplexity Pro | `perplexity_oauth` | pplx-sub/sonar-pro, sonar | `perplexity_handler.py` |

Enable in `.env`:

```bash
DECEPTICON_AUTH_CLAUDE_CODE=true     # Claude subscription
DECEPTICON_AUTH_CHATGPT=true         # ChatGPT subscription
DECEPTICON_AUTH_GEMINI=true          # Gemini Advanced
DECEPTICON_AUTH_COPILOT=true         # Copilot Pro
DECEPTICON_AUTH_GROK=true            # SuperGrok
DECEPTICON_AUTH_PERPLEXITY=true      # Perplexity Pro
```

For full setup instructions including token extraction, see [Setup Guide](setup-guide.md).
