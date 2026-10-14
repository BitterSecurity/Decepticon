# Setup Guide

Complete installation, authentication, and configuration reference.

---

## Table of Contents

- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Authentication Methods](#authentication-methods)
  - [API Keys](#api-keys)
  - [Claude Max/Pro Subscription (OAuth)](#claude-maxpro-subscription-oauth)
  - [ChatGPT Pro/Plus Subscription (OAuth)](#chatgpt-proplus-subscription-oauth)
- [Supported Providers](#supported-providers)
- [Model Profiles](#model-profiles)
- [Web Dashboard](#web-dashboard)
- [CLI Reference](#cli-reference)
- [Agentic Setup — End-to-End Walkthrough](#agentic-setup--end-to-end-walkthrough)
- [Advanced Configuration](#advanced-configuration)
- [Troubleshooting](#troubleshooting)

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Linux (x86_64, arm64), macOS (arm64, x86_64), WSL2 on Windows | Ubuntu 22.04+ / Kali 2024+ / macOS 14+ / WSL2 with Ubuntu or Kali |
| Container runtime | Docker Engine 24+ with Compose v2 **OR** Podman 4.4+ with `podman compose` **OR** nerdctl with compose plugin | Docker Desktop / Colima / Podman Desktop |
| RAM | 8 GB | 16 GB |
| Disk | 10 GB free | 20 GB free |
| Network | Outbound HTTPS | Low-latency connection |

Docker is the only hard dependency. Everything else runs in containers.

### Supported environments

OSS install targets and what we test against:

| Environment | Launcher binary | Notes |
|---|---|---|
| **macOS arm64** (Apple Silicon, M1–M4) | `darwin/arm64` | Native. Every container image is published multi-arch (linux/amd64 + linux/arm64), so Docker Desktop pulls the arm64 manifest and runs without Rosetta. |
| **macOS amd64** (Intel) | `darwin/amd64` | Native. |
| **Linux amd64** (Ubuntu, Debian, Fedora, Kali) | `linux/amd64` | Native. |
| **Linux arm64** (Raspberry Pi 5, Ampere, AWS Graviton, Asahi) | `linux/arm64` | Native — same multi-arch images as Apple Silicon. |
| **WSL2** (Windows + Ubuntu/Kali on WSL2) | `linux/amd64` | Use Docker Desktop with the WSL2 backend, or install Docker natively inside the WSL distro. See [WSL2 notes](#wsl2-notes) below. |
| **Windows amd64** (Windows 10/11, native) | `windows/amd64` | Native. Install via `irm https://decepticon.red/install.ps1 \| iex` in PowerShell. Requires Docker Desktop. |
| **Windows arm64** (Surface Pro X, ARM laptops) | `windows/arm64` | Native. Same PowerShell installer. |

Native Windows is supported alongside WSL2 — choose whichever fits your toolchain. The Go launcher detects the OS during `decepticon onboard` and adapts its remediation hints (Docker Desktop install URL, daemon-not-running fix, missing Compose v2).

### WSL2 notes

Decepticon runs end-to-end on WSL2 with two valid Docker setups:

1. **Docker Desktop with WSL2 backend** (the common path) — Docker Desktop registers `host.docker.internal` automatically.
2. **Native Docker inside the WSL distro** (no Docker Desktop) — Decepticon's `docker-compose.yml` adds the `host.docker.internal:host-gateway` mapping itself, so containers reach the host either way.

The default `OLLAMA_API_BASE=http://host.docker.internal:11434` is the right value in **all** environments — including the case where Ollama runs *inside* the same WSL distro as Decepticon. From inside a container, `localhost` is the container itself, so it can never reach Ollama on the host. Use `host.docker.internal`.

Ollama must additionally listen on all interfaces — the default `127.0.0.1` binding is invisible to containers. Launch it with:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Other WSL caveats:
- Install Decepticon under your WSL home (`~/.decepticon`), not on a Windows-mounted drive (`/mnt/c/...`) — bind-mounted I/O across the boundary is much slower.
- WSL2 mirrored networking (Windows 11 22H2+) collapses the *Windows host ↔ WSL distro* split, but Docker bridge networks remain isolated. The `host.docker.internal` requirement still applies.

#### WSL2 + Ollama troubleshooting flowchart

If `OLLAMA_API_BASE=http://host.docker.internal:11434` is failing
from the litellm container on WSL2, work through these checks in
order. The `[decepticon ollama]` log lines in `decepticon logs
litellm` will identify which class of failure you're hitting; this
section names each class and its fix.

**1. Class: connection refused** — Ollama is up but bound to
`127.0.0.1` only. The litellm container sees host.docker.internal
resolve fine, then the TCP handshake gets RST.

Verify from the WSL host (NOT inside the container):
```bash
netstat -tlnp 2>/dev/null | grep 11434
# Expected: tcp 0 0 0.0.0.0:11434 0.0.0.0:* LISTEN <pid>/ollama
# Wrong:    tcp 0 0 127.0.0.1:11434 0.0.0.0:* LISTEN <pid>/ollama
```

Fix:
```bash
# Foreground / current shell only
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# Persist across reboots — systemd unit override
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

**2. Class: DNS not resolved** — `host.docker.internal` doesn't
resolve inside the container. Common with native Docker in WSL
when the `extra_hosts` mapping is missing (e.g. a stale compose
override).

Verify from inside the container:
```bash
decepticon exec litellm getent hosts host.docker.internal
# Expected: 172.x.x.1  host.docker.internal
# Wrong:    (no output)
```

Fix: confirm `docker-compose.yml` litellm service has:
```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```
Workaround (if your override removed it): pin WSL distro IP in `.env`:
```bash
WSL_IP=$(ip -4 addr show eth0 | awk '/inet / {print $2}' | cut -d/ -f1)
echo "OLLAMA_API_BASE=http://$WSL_IP:11434" >> ~/.decepticon/.env
```
Note: the WSL IP changes across reboots. Prefer fixing the bridge
resolution.

**3. Class: request timed out** — Host resolves, port not answering.
Two sub-cases:

- **Windows Defender Firewall blocks inbound 11434.** Check in
  Windows admin PowerShell:
  ```powershell
  Get-NetFirewallRule -Direction Inbound |
    Where DisplayName -like '*Ollama*' |
    Format-Table DisplayName, Enabled, Profile
  ```
  Fix:
  ```powershell
  New-NetFirewallRule -DisplayName 'Ollama for WSL2' `
    -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
  ```

- **WSL2 mirrored networking misconfigured.** On Windows 11 22H2+
  mirrored mode requires `~/.wslconfig` (Windows-side):
  ```ini
  [wsl2]
  networkingMode=mirrored
  ```
  Apply via `wsl --shutdown` then restart your distro. After
  mirrored mode is active, `OLLAMA_API_BASE=http://localhost:11434`
  may work directly from the litellm container.

**4. Class: model not pulled** — `/api/show` returns 404 for
`OLLAMA_MODEL`. Probe surfaces: *"Ollama model X is not pulled on
this host."* Fix: `ollama pull <model>`.

**5. Class: model doesn't support tools** — `/api/show` capabilities
don't include `tools`. Pick a tool-capable model: `qwen3-coder`,
`llama3.3`, `mistral-small3`, `deepseek-r1`.

#### One-shot diagnostic

Run the probe directly inside the litellm container:
```bash
docker exec -e OLLAMA_API_BASE=$OLLAMA_API_BASE \
  -e OLLAMA_MODEL=$OLLAMA_MODEL \
  $(docker ps -qf name=litellm | head -1) \
  python3 -c "
from config.ollama_probe import probe
for line in probe('$OLLAMA_API_BASE', ['ollama_chat/$OLLAMA_MODEL']):
    print(line)
"
```
Each line is one operator-actionable hint. The probe now detects
WSL2 environments (via `/proc/version` and `WSL_*` env vars) and
emits WSL2-specific guidance for every error class.

---

## Installation

### One-Line Install

```bash
curl -fsSL https://decepticon.red/install | bash
```

This downloads the `decepticon` CLI binary for your platform and places it in your PATH.

### Using Podman instead of Docker

Decepticon detects Podman 4.4+ automatically. The launcher prefers Docker when both are available; set `DECEPTICON_CONTAINER_RUNTIME=podman` to force Podman:

```bash
# Linux (rootless Podman, recommended)
systemctl --user enable --now podman.socket          # enable the API socket
export DECEPTICON_CONTAINER_RUNTIME=podman           # force Podman selection
decepticon start                                      # launcher detects + uses podman compose
```

What the launcher handles for you:
- Selects `podman` if `docker` is absent (or if you set the override).
- Auto-discovers the Podman API socket (`$XDG_RUNTIME_DIR/podman/podman.sock` or `/run/user/$UID/podman/podman.sock` for rootless; `/run/podman/podman.sock` for rootful) and exports `DOCKER_HOST` so any nested Docker-API tooling (testcontainers, etc.) keeps working.
- Uses `podman compose` (Podman 4.4+ built-in); falls back to the `podman-compose` Python wrapper on older Podman.

Rootless Podman caveats:
- The `c2-sliver` profile binds privileged ports (443, 53). On rootless Podman you need either `sudo setcap CAP_NET_BIND_SERVICE=+eip $(which podman)` or add `net.ipv4.ip_unprivileged_port_start = 53` to `/etc/sysctl.conf`. Or just run rootful.
- Bind mounts under your home directory work transparently. Mounting `/var/run/...` requires `--security-opt label=disable` on SELinux distros (Fedora, RHEL).

### Using nerdctl

```bash
export DECEPTICON_CONTAINER_RUNTIME=nerdctl
decepticon start
```

Requires nerdctl ≥ 0.16 (built-in compose) and a running containerd (`containerd` or `Rancher Desktop` with the containerd engine).

### Manual Install (from source)

`make dogfood` reproduces the OSS launcher flow against locally-built images
— launcher onboard wizard, engagement picker, compose up, health checks,
and the CLI all execute exactly as the `curl | bash` install path. The
launcher and every service image come from the current checkout (tag
`:dev`), with an isolated `$DECEPTICON_HOME` under `.dogfood/` so your
real `~/.decepticon` is untouched.

```bash
git clone https://github.com/PurpleAILAB/Decepticon.git
cd Decepticon
make dogfood
```

### Verify Installation

```bash
decepticon version
```

---

## Authentication Methods

Decepticon supports three authentication modes. Choose one during `decepticon onboard`.

### API Keys

Standard pay-per-token access through provider APIs. Set the appropriate environment variable for your provider.

```bash
decepticon onboard
# Select: API Key
# Select: Your provider
# Enter: Your API key
```

Or edit `~/.decepticon/.env` directly:

```bash
DECEPTICON_AUTH_PRIORITY=anthropic_api,openai_api
ANTHROPIC_API_KEY=sk-ant-api03-...
OPENAI_API_KEY=sk-proj-...
```

**All supported API key providers:**

| Provider | Env Var | Key Format | Sign Up |
|----------|---------|------------|---------|
| Anthropic | `ANTHROPIC_API_KEY` | `sk-ant-...` | [console.anthropic.com](https://console.anthropic.com) |
| OpenAI | `OPENAI_API_KEY` | `sk-proj-...` | [platform.openai.com](https://platform.openai.com) |
| DeepSeek | `DEEPSEEK_API_KEY` | `sk-...` | [platform.deepseek.com](https://platform.deepseek.com) |
| Google | `GEMINI_API_KEY` | `AIza...` | [aistudio.google.com](https://aistudio.google.com) |
| xAI | `XAI_API_KEY` | `xai-...` | [console.x.ai](https://console.x.ai) |
| Mistral | `MISTRAL_API_KEY` | `...` | [console.mistral.ai](https://console.mistral.ai) |
| Cohere | `COHERE_API_KEY` | `...` | [dashboard.cohere.com](https://dashboard.cohere.com) |
| Groq | `GROQ_API_KEY` | `gsk_...` | [console.groq.com](https://console.groq.com) |
| Together | `TOGETHER_API_KEY` | `...` | [api.together.xyz](https://api.together.xyz) |
| Fireworks | `FIREWORKS_API_KEY` | `fw_...` | [fireworks.ai](https://fireworks.ai) |
| Perplexity | `PERPLEXITY_API_KEY` | `pplx-...` | [perplexity.ai](https://perplexity.ai) |
| MiniMax | `MINIMAX_API_KEY` | `eyJ...` | [minimax.io](https://minimax.io) |
| OpenRouter | `OPENROUTER_API_KEY` | `sk-or-...` | [openrouter.ai](https://openrouter.ai) |
| Replicate | `REPLICATE_API_TOKEN` | `r8_...` | [replicate.com](https://replicate.com) |
| NVIDIA NIM | `NVIDIA_API_KEY` | `nvapi-...` | [build.nvidia.com](https://build.nvidia.com) |
| Moonshot (Kimi K2) | `MOONSHOT_API_KEY` | `sk-...` | [platform.moonshot.cn](https://platform.moonshot.cn) |
| Kimi for Coding (K3 / K2.7) | `KIMI_API_KEY` | `sk-kimi-...` | [kimi.com](https://www.kimi.com) (api.kimi.com/coding) |
| Z.ai (GLM-4.5) | `ZAI_API_KEY` | `...` | [z.ai/manage](https://z.ai/manage) |
| Alibaba DashScope (Qwen) | `DASHSCOPE_API_KEY` | `sk-...` | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| GitHub Models | `GITHUB_TOKEN` | `github_pat_...` | [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens) |

**Cloud platform providers:**

| Provider | Required Env Vars |
|----------|-------------------|
| Azure OpenAI | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| GCP Vertex AI | `GOOGLE_APPLICATION_CREDENTIALS`, `VERTEXAI_PROJECT`, `VERTEXAI_LOCATION` |

**Self-hosted / OpenAI-compatible:**

| Provider | Required Env Vars |
|----------|-------------------|
| Ollama (local) | `OLLAMA_API_BASE` + `OLLAMA_MODEL` — see [Local LLM (Ollama)](#local-llm-ollama) below |
| Ollama Cloud | `OLLAMA_CLOUD_API_BASE`, `OLLAMA_CLOUD_API_KEY`, `OLLAMA_CLOUD_MODEL` |
| LM Studio (local) | `LMSTUDIO_API_BASE` + `LMSTUDIO_MODEL` (server on port 1234) |
| Custom gateway | `CUSTOM_OPENAI_API_KEY`, `CUSTOM_OPENAI_API_BASE`, `CUSTOM_OPENAI_MODEL` |

**OpenAI-compatible gateways / aggregators:**

Each routes through LiteLLM's `openai/` provider with a fixed base URL
(no extra config beyond the key). Set the key and the gateway joins the
default fallback chain; pick a specific route at runtime with `/model`
(e.g. `/model opencode/claude-opus-4-6`).

| Provider | Env Var | Console |
|----------|---------|---------|
| OpenCode Zen | `OPENCODE_API_KEY` | [opencode.ai/docs/zen](https://opencode.ai/docs/zen/) |
| Vercel AI Gateway | `VERCEL_AI_GATEWAY_API_KEY` | [vercel.com/docs/ai-gateway](https://vercel.com/docs/ai-gateway) |
| Hugging Face Router | `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |
| Venice AI | `VENICE_API_KEY` | [venice.ai/settings/api](https://venice.ai/settings/api) |
| NanoGPT | `NANOGPT_API_KEY` | [nano-gpt.com](https://nano-gpt.com) |
| Synthetic | `SYNTHETIC_API_KEY` | [synthetic.new](https://synthetic.new) |
| ZenMux | `ZENMUX_API_KEY` | [zenmux.ai](https://zenmux.ai) |
| Baidu Qianfan (ERNIE) | `QIANFAN_API_KEY` | [console.bce.baidu.com/qianfan](https://console.bce.baidu.com/qianfan) |
| Cloudflare AI Gateway | `CLOUDFLARE_AI_GATEWAY_API_KEY` + `CLOUDFLARE_AI_GATEWAY_API_BASE` | [dash.cloudflare.com](https://dash.cloudflare.com) |
| Cheaper Inference | `CHEAPER_INFERENCE_API_KEY` | [cheaperinference.com](https://cheaperinference.com) |

> Cloudflare's base URL is per-account — set it to your gateway's
> OpenAI-compat `…/compat` path. Qianfan model ids drift; override per
> role with `DECEPTICON_MODEL_<ROLE>` if a default 404s.

---

### Local LLM (Ollama)

Run Decepticon offline against a local Ollama server — no cloud API
billing, no key.

**Setup:**

1. Install Ollama on your host: <https://ollama.com/download>.
2. Pull a tool-capable model — Decepticon agents always call tools, so
   the chosen model **must** advertise the `tools` capability. Known
   working families: Qwen3-Coder, Llama 3.3, DeepSeek-R1, Mistral
   Small 3, Hermes-3:
   ```bash
   ollama pull qwen3-coder:30b
   ollama show qwen3-coder:30b   # capabilities should include "tools"
   ```
3. Start the Ollama server bound to all interfaces so the Decepticon
   container can reach it (the default `127.0.0.1` binding only accepts
   host-side connections):
   ```bash
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```
   On systems where Ollama runs as a service (e.g. systemd), set
   `Environment=OLLAMA_HOST=0.0.0.0:11434` in the unit and restart it.
4. Run `decepticon onboard` and pick **"Local LLM (Ollama)"**. The
   wizard prompts for:
   - `OLLAMA_API_BASE` — leave the default `http://host.docker.internal:11434`.
     This works on macOS, Linux, and WSL2 (with or without Docker
     Desktop) because `docker-compose.yml` adds the
     `host.docker.internal:host-gateway` mapping. **`localhost` is
     never the right answer** — from inside a container that's the
     container itself, not the host.
   - `OLLAMA_MODEL` — the wizard probes your running Ollama at the
     default URL, lists every pulled model whose `/api/show`
     capabilities include `tools`, and presents that filtered list as
     the only valid choice. You cannot type a tag manually: Decepticon
     agents always emit tool calls, so a non-tool-capable model would
     break on the first request. If the wizard finds nothing
     tool-capable (or cannot reach Ollama), it refuses to write `.env`
     and prints the exact remediation steps.
5. Run `decepticon`. A second probe inside the litellm container
   re-verifies reachability and tool-capability after the stack starts
   — the in-wizard host probe can't tell whether Ollama is bound to
   `0.0.0.0` (visible to the container) versus `127.0.0.1` only
   (invisible). Either probe failing prints a clear diagnostic in
   `decepticon logs litellm`.

**How it works:**

- LiteLLM dynamically registers `ollama_chat/<OLLAMA_MODEL>` at proxy
  startup (`config/litellm_dynamic_config.py`). No yaml edit needed.
- `ollama_chat/` (not `ollama/`) routes to Ollama's `/api/chat`
  endpoint — the only one that supports tool/function calling, which
  every Decepticon agent depends on.
- The `ollama_local` AuthMethod collapses HIGH/MID/LOW tiers to the
  same model — local hardware can't usually run three different models
  in parallel. Mix with cloud providers if you want tier degradation:
  set `DECEPTICON_AUTH_PRIORITY=ollama_local,anthropic_api` to lead
  with local and fall back to Anthropic on local-side errors.

**Per-role overrides:**

If you do have the GPU headroom, override individual agents to
different Ollama models without touching yaml:

```bash
DECEPTICON_MODEL_DECEPTICON=ollama_chat/qwen3-coder:30b   # HIGH agents
DECEPTICON_MODEL_RECON=ollama_chat/llama3.2:3b             # LOW agents
```

The dynamic config registrar picks these up at startup.

---

### Claude Max/Pro Subscription (OAuth)

Use your Claude Max, Pro, or Team subscription instead of API billing. Requests route through Claude Code's OAuth handler — no API cost.

**Supported tiers:**

| Tier | Models Available | Rate Limits |
|------|-----------------|-------------|
| Claude Free | Haiku only | Very limited |
| Claude Pro ($20/mo) | Opus, Sonnet, Haiku | Standard |
| Claude Max ($100/mo) | Opus, Sonnet, Haiku | 20x higher |
| Claude Team | Opus, Sonnet, Haiku | Organization-managed |

**Setup:**

1. Install Claude Code CLI and authenticate:

```bash
# Install Claude Code (if not already installed)
npm install -g @anthropic-ai/claude-code

# Authenticate — opens browser for OAuth login
claude login
```

2. Verify credentials exist:

```bash
cat ~/.claude/.credentials.json
# Should contain claudeAiOauth.accessToken starting with sk-ant-oat01-
```

3. Configure Decepticon to use OAuth:

```bash
decepticon onboard
# Select: Claude Sub
# Select: Claude Code
# Select: Profile (eco/max/test)
```

Or edit `~/.decepticon/.env`:

```bash
DECEPTICON_AUTH_PRIORITY=anthropic_oauth,anthropic_api
DECEPTICON_AUTH_CLAUDE_CODE=true
DECEPTICON_MODEL_PROFILE=eco
```

4. Launch:

```bash
decepticon
```

**How it works:**

- The `auth` provider remaps `anthropic/*` model names to `auth/*`
- LiteLLM routes `auth/*` through `claude_code_handler.py`
- The handler reads OAuth tokens from `~/.claude/.credentials.json`
- Requests hit `api.anthropic.com` with Bearer auth + Claude Code headers
- Tokens auto-refresh when expired using the stored refresh token
- Fallback models stay on API-key provider for redundancy

**Alternative token sources (in priority order):**

1. `ANTHROPIC_OAUTH_TOKEN` env var — direct access token
2. `~/.claude/.credentials.json` — Claude Code CLI (current format)
3. `~/.config/anthropic/q/tokens.json` — Legacy format

**Custom credentials path:**

```bash
CLAUDE_CODE_CREDENTIALS_PATH=/custom/path/credentials.json
```

---

### ChatGPT Pro/Plus Subscription (OAuth)

Use your ChatGPT Pro, Plus, or Team subscription instead of OpenAI API billing.

**Supported tiers:**

| Tier | Models Available (via `auth/gpt-5.x` route) |
|------|--------------------------------------------|
| ChatGPT Plus ($20/mo) | `auth/gpt-5.5`, `auth/gpt-5.4`, `auth/gpt-5.4-mini` |
| ChatGPT Pro ($200/mo) | `auth/gpt-5.5`, `auth/gpt-5.4`, `auth/gpt-5.4-mini` |
| ChatGPT Team | `auth/gpt-5.5`, `auth/gpt-5.4`, `auth/gpt-5.4-mini` + admin controls |

**Setup:**

1. Configure Decepticon:

```bash
decepticon onboard
# Select: ChatGPT
# Select: Profile (eco/max/test)
```

Or edit `~/.decepticon/.env`:

```bash
DECEPTICON_AUTH_CHATGPT=true
```

2. Launch:

```bash
decepticon
```

**How it works:**

- Decepticon exposes ChatGPT subscription models as `auth/gpt-5.5`
  and `auth/gpt-5.4`.
- LiteLLM dynamic config maps those aliases through Decepticon's custom
  `auth/` handler (`codex_chatgpt_handler`) only when
  `DECEPTICON_AUTH_CHATGPT=true`.
- `docker-compose.yml` mounts the host's Codex CLI credential file
  (`~/.codex/auth.json`) into the LiteLLM container at
  `/root/.codex/auth.json` (read-write, so the in-container refresh path
  can persist rotated tokens back to the host).
- The `auth/` handler reads and writes the same `auth.json` the Codex CLI
  itself uses, so a host-side `codex login` is visible to the running
  container without a restart, and a refresh inside the container is
  visible to the host CLI on the next call.

**Setup:**

```bash
# Install the Codex CLI and run the device-code login on the host:
codex login
# Confirm the file exists:
ls ~/.codex/auth.json
```

---

### Google Gemini Advanced (OAuth)

Use your Google One AI Premium subscription ($20/mo).

**Setup:**

1. Extract OAuth token from gemini.google.com browser session, or use Google Cloud OAuth2 credentials
2. Configure:

```bash
DECEPTICON_AUTH_GEMINI=true
GEMINI_ACCESS_TOKEN=<your-google-oauth-token>
```

Or use session cookies:
```bash
GEMINI_SESSION_COOKIES={"__Secure-1PSID":"value","__Secure-1PSIDTS":"value"}
```

Token file: `~/.config/gemini/tokens.json`

---

### Microsoft Copilot Pro (OAuth)

Use your Copilot Pro subscription ($20/mo) for GPT-4o/o1 access.

**Setup:**

1. Extract tokens from copilot.microsoft.com browser session
2. Configure:

```bash
DECEPTICON_AUTH_COPILOT=true
COPILOT_ACCESS_TOKEN=eyJ...your-ms-token
```

Or with auto-refresh:
```bash
COPILOT_REFRESH_TOKEN=M.C507_BAY...
COPILOT_CLIENT_ID=your-app-client-id
```

Token file: `~/.config/copilot/tokens.json`

---

### xAI SuperGrok (OAuth)

Use your X Premium+ subscription for Grok-3 access.

**Setup:**

1. Extract `auth_token` cookie from grok.x.ai or x.com
2. Configure:

```bash
DECEPTICON_AUTH_GROK=true
GROK_SESSION_TOKEN=your-x-auth-token
```

Token file: `~/.config/grok/tokens.json`

---

### Perplexity Pro (OAuth)

Use your Perplexity Pro subscription ($20/mo) for Sonar Pro access.

**Setup:**

1. Extract `next-auth.session-token` cookie from perplexity.ai
2. Configure:

```bash
DECEPTICON_AUTH_PERPLEXITY=true
PERPLEXITY_SESSION_TOKEN=your-session-token
```

Token file: `~/.config/perplexity/tokens.json`

---

## Supported Providers

Complete list of all supported LLM providers and their pre-configured models:

| Provider | Models | Auth Type | Cost |
|----------|--------|-----------|------|
| **Subscriptions (OAuth — no API billing)** | | | |
| Claude Max/Pro/Team | Opus, Sonnet, Haiku | OAuth | $20–$100/mo |
| ChatGPT Pro/Plus/Team | `auth/gpt-5.6-sol`, `auth/gpt-5.6-terra`, `auth/gpt-5.6-luna`, plus GPT-5.5/5.4 routes | OAuth | $20–$200/mo |
| Gemini Advanced | Gemini 2.5 Pro/Flash | OAuth | $20/mo |
| Copilot Pro | `copilot/gpt-5.5`, `copilot/claude-sonnet-4-6`, `copilot/gpt-5.4-mini` (+ `copilot/gpt-5.3-codex`) | OAuth | $20/mo |
| SuperGrok | Grok 4.3, Grok 4-1 Fast Reasoning | OAuth | X Premium+ |
| Perplexity Pro | Sonar Pro, Sonar | OAuth | $20/mo |
| **API Key Providers (pay-per-token)** | | | |
| Anthropic | Claude Opus 5.5, Sonnet 5.5, Haiku 4.5, earlier routes | API key | Per token |
| OpenAI | GPT-6 Astra/6.1 Sol/6 Luna, GPT-5.6 family, earlier routes | API key | Per token |
| DeepSeek | DeepSeek V4 Pro/Flash, Chat, Reasoner | API key | Per token |
| Google | Gemini 3.8 Flash, Gemini 3.7/3.6 Flash, Gemini 2.5 family | API key | Per token |
| xAI | Grok 4.7/4.5/4.3, Grok 4-1 Fast Reasoning | API key | Per token |
| Mistral | Mistral Large, Codestral | API key | Per token |
| Cohere | Command R+, Command R | API key | Per token |
| Groq | Llama 3.3 70B, Llama 3.1 8B | API key | Per token |
| Together AI | Llama 3.3 70B Turbo + any | API key | Per token |
| Fireworks AI | Llama 405B + any | API key | Per token |
| Perplexity | Sonar Pro, Sonar | API key | Per token |
| MiniMax | MiniMax-M3, MiniMax-M2.7-highspeed | API key | Per token |
| OpenRouter | Any model via routing | API key | Per token |
| Azure OpenAI | Any Azure-deployed model | API key + endpoint | Per token |
| AWS Bedrock | Any Bedrock model | AWS credentials | Per token |
| Replicate | Any Replicate-hosted model | API token | Per token |
| **Self-Hosted** | | | |
| Ollama | Any locally-served model | Local endpoint | Free |
| Custom Gateway | Any OpenAI-compatible server | API key + base URL | Varies |

**Adding models not in the static config:**

Current production LiteLLM routes missing from the base config are included in
`config/litellm_prod_catalog.json`. At startup, OSS registers a catalog
route when its provider API key is configured in `~/.decepticon/.env`;
`runyour/*` also needs `RUNYOUR_API_BASE`. The `/model` command lists
the current model IDs. A listed route still requires its provider credential
and provider-side access. The catalog omits SaaS tenant and admin aliases.
The existing tier defaults stay in place; select a current route with
`/model <model-id>` or `DECEPTICON_MODEL_<ROLE>`.

Set `DECEPTICON_MODEL` or `DECEPTICON_LITELLM_MODELS` and Decepticon auto-generates the LiteLLM route at container startup:

```bash
DECEPTICON_MODEL_PROFILE=custom
DECEPTICON_MODEL=openrouter/anthropic/claude-3.7-sonnet
DECEPTICON_LITELLM_MODELS=groq/llama-3.3-70b-versatile,together/deepseek-ai/DeepSeek-R1
```

---

## Model Profiles

| Profile | Use Case | Cost |
|---------|----------|------|
| `eco` | Production engagements — balanced mix | $$ |
| `max` | High-value targets — Opus everywhere | $$$$ |
| `test` | Development and CI — Haiku only | $ |
| `custom` | Bring your own model via `DECEPTICON_MODEL` | Varies |

Set in `~/.decepticon/.env`:

```bash
DECEPTICON_MODEL_PROFILE=eco
```

Per-role overrides (any profile):

```bash
DECEPTICON_MODEL_RECON=ollama_chat/qwen3-coder:30b
DECEPTICON_MODEL_EXPLOIT=anthropic/claude-opus-4-7
DECEPTICON_MODEL_EXPLOIT_TEMPERATURE=0.2
```

See [Models](models.md) for the full role-to-model mapping.

---

## Web Dashboard

The web dashboard is optional. Start it from the interactive CLI with `/web`, then open:

```
http://localhost:3000
```

**Features:**

- Real-time engagement monitoring
- Agent activity and conversation timeline
- Attack chain visualization (Neo4j knowledge graph)
- Model usage and cost tracking
- Terminal access to the sandbox environment

**Custom port:**

```bash
# In ~/.decepticon/.env
WEB_PORT=8080
```

See [Web Dashboard](web-dashboard.md) for the full feature reference.

---

## CLI Reference

### Core Commands

```bash
decepticon                  # Launch the core stack + interactive CLI
decepticon onboard          # Setup wizard (auth, provider, profile)
decepticon onboard --reset  # Re-run setup from scratch
decepticon stop             # Stop all services, keep data
decepticon status           # Show running services
decepticon logs [service]   # Follow service logs
decepticon update           # Explicitly refresh config/images and upgrade when available
decepticon remove           # Uninstall Decepticon completely
decepticon --version        # Show installed version
```

### Service Management

```bash
decepticon logs litellm     # LiteLLM proxy logs
decepticon logs langgraph   # LangGraph agent logs
decepticon logs neo4j       # Knowledge graph logs
decepticon kg-health        # Neo4j connection diagnostics
```

See [CLI Reference](cli-reference.md) for the complete command list.

---

## Agentic Setup — End-to-End Walkthrough

Complete walkthrough from zero to a running autonomous engagement, covering every component.

### Step 1: Install the CLI

```bash
# One-line install (Linux/macOS)
curl -fsSL https://decepticon.red/install | bash

# Or from source
git clone https://github.com/PurpleAILAB/Decepticon.git
cd Decepticon
make install
```

Verify:

```bash
decepticon version
```

### Step 2: Run the Setup Wizard

```bash
decepticon onboard
```

The wizard walks through 6 screens:

| Step | Screen | What you configure |
|------|--------|-------------------|
| 1 | Authentication | API Key, Claude Sub, ChatGPT Sub, Gemini Sub, Copilot Pro, SuperGrok, or Perplexity Pro |
| 2 | Provider | Which LLM provider powers the agents (18+ options) |
| 3 | Credentials | API key, OAuth token, or endpoint URL |
| 4 | Model | Primary model ID (auto-detected for Anthropic presets) |
| 5 | Profile | `eco` (balanced), `max` (performance), `test` (dev), `custom` (any model) |
| 6 | Observability | Optional LangSmith tracing |

Configuration saves to `~/.decepticon/.env`. Re-run anytime with `decepticon onboard --reset`.

### Step 3: Launch the Platform

```bash
decepticon
```

This single command:

1. **Validates** your `.env` configuration
2. **Pulls** Docker images (first run only, ~2 GB)
3. **Starts** 7 services in dependency order:
   - PostgreSQL → LiteLLM proxy → Neo4j → Sandbox → LangGraph → Web Dashboard → CLI
4. **Waits** for all healthchecks to pass (~60-120s first run, ~10s subsequent)
5. **Shows** the engagement picker
6. **Launches** the interactive terminal CLI

### Step 4: Service Architecture

Once running, you have:

| Service | Port | Purpose |
|---------|------|---------|
| **LiteLLM** | 4000 | LLM API gateway — routes to providers, tracks usage, handles fallback |
| **LangGraph** | 2024 | Agent runtime — hosts all 17 agents as a streaming API |
| **Neo4j** | 7474/7687 | Knowledge graph — persistent attack chain memory |
| **Sandbox** | (internal) | Isolated Kali Linux — runs all offensive tools |
| **Web Dashboard** | 3000 | Browser UI — real-time monitoring, graph visualization |
| **Terminal Server** | 3003 | WebSocket bridge — embeds CLI in the web dashboard |
| **PostgreSQL** | 5432 | Persistence — LiteLLM usage logs, web dashboard data |

### Step 5: Create Your First Engagement

**Option A — Terminal CLI:**

```bash
# Decepticon shows the engagement picker after launch
# Select "New engagement" and enter only its name
# Inside the session, use /mode plan, /mode red, or /mode blue
```

**Option B — Web Dashboard:**

1. Open `http://localhost:3000`
2. Click "New Engagement"
3. Enter a name, one root URL or IP/CIDR target, and an authorization reference
4. Confirm authorization, choose Red, Plan, or Blue, and click "Create"

### Step 6: Optional Soundwave Plan

The CLI opens a new engagement in Plan mode, where Soundwave gathers target
and scope through conversation. The web dashboard can also create a direct
authorized RoE from its setup form. Soundwave's planning interview generates
the engagement package:

```
Questions you'll answer:
├── Target scope (IPs, URLs, domains)
├── Threat actor profile (nation-state, criminal, insider)
├── Authorized actions (scanning, exploitation, lateral movement)
├── Exclusions (production DBs, critical infra)
├── Testing window (hours, days, timezone)
├── OPSEC requirements (noise level, detection avoidance)
└── Acceptance criteria (what does "done" look like)
```

Soundwave generates an eight-document engagement bundle from your answers:

| Document | Purpose |
|----------|---------|
| **RoE** | Legal authorization, scope, exclusions, escalation contacts |
| **Threat Profile** | MITRE-mapped adversary persona and key TTPs |
| **CONOPS** | Threat model and kill-chain phases, scoped to the RoE |
| **Deconfliction Plan** | Source IPs, time windows, SOC coordination codes |
| **Contact Plan** | Operator and escalation roster, plus the abort-signal recipient |
| **Data Handling Plan** | Evidence retention, encryption, and chain-of-custody |
| **Abort Plan** | Halt triggers and AI-aware safety gates |
| **Cleanup Plan** | Expected artifacts and per-phase removal commands |

The orchestrator builds the **OPPLAN** (objectives, phases, MITRE ATT&CK
mapping) from the bundle — Soundwave does not write it.

### Step 7: Autonomous Execution

After direct Red authorization or approval of the optional Plan bundle,
Decepticon can build the OPPLAN and begin execution:

```
Decepticon (Orchestrator)
├── Reads OPPLAN objectives
├── Dispatches to specialist agents:
│   ├── Recon → port scan, service enum, OSINT
│   ├── Scanner → vulnerability scanning, CVE mapping
│   ├── Exploit → initial access, payload delivery
│   ├── Post-Exploit → privesc, lateral movement, C2
│   ├── AD Operator → Active Directory attack chains
│   └── Cloud Hunter → cloud infrastructure attacks
├── Tracks progress in Neo4j knowledge graph
├── Adapts strategy based on findings
└── Generates final report via Analyst agent
```

All commands execute inside the **sandboxed Kali container** — zero host exposure.

### Step 8: Monitor in Real Time

**Terminal CLI:**
- Live streaming of agent activity, tool calls, sub-agent dispatch
- `Ctrl+O` to toggle transcript mode (full event history)
- `Ctrl+C` to pause (resume with `/resume`)

**Web Dashboard (`http://localhost:3000`):**
- Live attack graph visualization (Neo4j-backed)
- Agent activity timeline
- OPPLAN progress tracker
- Findings table with severity ratings
- Embedded terminal (same CLI, in-browser)

### Step 9: Post-Engagement

```bash
# View findings
ls ~/.decepticon/workspace/<engagement>/findings/

# View generated documents
ls ~/.decepticon/workspace/<engagement>/plan/

# Export knowledge graph
decepticon logs neo4j

# Stop services (preserves data)
decepticon stop

# Full reset (removes all data)
cd ~/.decepticon && docker compose down -v
```

### Quick Reference — Common Workflows

**Resume a previous engagement:**

```bash
decepticon           # Shows engagement picker → select existing
# Or from CLI: /resume → select from session list
```

**Switch model provider mid-session:**

```bash
decepticon stop
# Edit ~/.decepticon/.env → change DECEPTICON_AUTH_PRIORITY, DECEPTICON_AUTH_* toggles, or API keys
decepticon
```

**Check service health:**

```bash
decepticon status       # Container status (docker compose ps)
decepticon kg-health    # Knowledge graph diagnostics (LangGraph + Neo4j connection)
```

**View logs for specific service:**

```bash
decepticon logs              # LangGraph (default)
decepticon logs litellm      # LLM proxy
decepticon logs neo4j        # Knowledge graph
decepticon logs web          # Web dashboard
```

---

## Advanced Configuration

### Multiple API Keys

You can configure multiple providers simultaneously. The model profile controls which is used for each agent role, and fallbacks automatically use the next provider:

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...
DEEPSEEK_API_KEY=sk-...
GEMINI_API_KEY=AIza...
```

### Hybrid Auth (OAuth + API Keys)

Set `DECEPTICON_AUTH_CLAUDE_CODE=true` to route Anthropic models through Claude Code OAuth while keeping API-key fallbacks active. The `DECEPTICON_AUTH_PRIORITY` list controls order:

```bash
DECEPTICON_AUTH_PRIORITY=anthropic_oauth,anthropic_api,openai_api,google_api
DECEPTICON_AUTH_CLAUDE_CODE=true      # Primary: Claude via OAuth (auth/* in LiteLLM)
ANTHROPIC_API_KEY=sk-ant-...          # Fallback: Anthropic API
OPENAI_API_KEY=sk-proj-...            # Fallback: GPT via API key
GEMINI_API_KEY=AIza...                # Fallback: Gemini via API key
```

### LangSmith Tracing

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=decepticon
```

### Custom Ports

```bash
LANGGRAPH_PORT=2024   # Agent runtime
LITELLM_PORT=4000     # LLM proxy
POSTGRES_PORT=5432    # Database
WEB_PORT=3000         # Dashboard
```

### Debug Mode

```bash
DECEPTICON_DEBUG=true
```

---

## Troubleshooting

### Authentication Issues

**Claude OAuth: "No Claude Code OAuth tokens found"**

```bash
# Re-authenticate Claude Code CLI
claude login

# Verify token exists
cat ~/.claude/.credentials.json | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('claudeAiOauth',{}).get('accessToken','').startswith('sk-ant-oat01-') else 'MISSING')"
```

**ChatGPT OAuth: missing or expired auth**

The Decepticon `auth/` ChatGPT handler reads the Codex CLI credential
store at `~/.codex/auth.json`. If the file is missing or the refresh
token is rejected, run `codex login` on the host and retry. The handler
picks up the refreshed file automatically (no container restart needed).

**API Key: "401 Unauthorized"**

```bash
# Verify key format
grep _API_KEY ~/.decepticon/.env | head -5

# Test directly
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}'
```

### Docker Issues

**Apple Silicon (M1/M2/M3/M4): "no matching manifest for linux/arm64/v8"**

Every container image is published multi-arch (linux/amd64 + linux/arm64), so this error should not reproduce on a fresh install. If you do hit it, you're almost certainly running an old `docker-compose.yml` from a previous install.

**Solution:** pull the latest config files:

```bash
decepticon update
```

If your host arch is *not* amd64 or arm64 (rare — armv7, ppc64le, ...), the manifest list won't match. Force the amd64 fallback by adding `platform: linux/amd64` under the `sandbox:` and `c2-sliver:` services in `~/.decepticon/docker-compose.yml` and enable "Use Rosetta for x86_64/amd64 emulation" in Docker Desktop settings (or QEMU on Linux).

**Services won't start:**

```bash
decepticon status          # Which services are down?
decepticon logs litellm    # Check LiteLLM for config errors
docker compose ps          # Raw container status
```

**LiteLLM can't reach provider:**

```bash
# Check inside the container
docker compose exec litellm curl -s https://api.anthropic.com/v1/messages -I
```

**Neo4j connection refused:**

```bash
decepticon kg-health
```

### Reset Everything

```bash
decepticon stop
cd ~/.decepticon && docker compose down -v   # Remove all volumes
decepticon onboard --reset                    # Re-run setup
decepticon                                    # Fresh start
```
