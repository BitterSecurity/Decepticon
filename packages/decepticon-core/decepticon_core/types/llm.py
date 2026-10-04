"""LLM model definitions — tier-based, credentials-aware mappings.

Three orthogonal axes control model selection:

  Tier        — model power level (HIGH / MID / LOW)
  AuthMethod  — specific way to authenticate (anthropic_api,
                anthropic_oauth, openai_api, google_api, minimax_api,
                openrouter_api, nvidia_api)
  Profile     — eco (per-agent tier) / max (all HIGH) / test (all LOW)

The profile decides each agent's *tier*. The user's *credentials inventory*
— a list of AuthMethods in priority order — decides the fallback chain at
that tier. Each AuthMethod is a distinct credential: Anthropic API key
and Claude Code OAuth are two different methods, can both be configured,
and can be ordered independently in the priority list.

Examples
--------
User has [anthropic_api, openai_api]. Profile=eco.
  decepticon (HIGH) → primary=anthropic/claude-opus-4-8, fallback=openai/gpt-5.5
  recon (LOW)       → primary=anthropic/claude-haiku-4-5, fallback=openai/gpt-5-nano

User has [anthropic_oauth, anthropic_api]. Profile=eco.
  decepticon (HIGH) → primary=auth/claude-opus-4-8, fallback=anthropic/claude-opus-4-8
  (OAuth subscription primary, paid API as fallback when subscription quota hits.)

User has [anthropic_oauth] only. Profile=eco.
  decepticon (HIGH) → primary=auth/claude-opus-4-8, fallback=None

User has [openai_api]. Profile=eco.
  decepticon (HIGH) → primary=openai/gpt-5.5, fallback=None
  recon (LOW)       → primary=openai/gpt-5-nano, fallback=None

Tier × AuthMethod matrix
------------------------
                    HIGH                          MID                            LOW
  anthropic_api    claude-opus-4-8               claude-sonnet-4-6              claude-haiku-4-5
  anthropic_oauth  auth/claude-opus-4-8          auth/claude-sonnet-4-6         auth/claude-haiku-4-5
  openai_api       gpt-5.5                       gpt-5.4                        gpt-5-nano
  openai_oauth     auth/gpt-5.5                  auth/gpt-5.4                   auth/gpt-5.4-mini
  google_api       gemini-2.5-pro                gemini-2.5-flash               gemini-2.5-flash-lite
  minimax_api      MiniMax-M3                    MiniMax-M2.7-highspeed         — (falls through)
  openrouter_api   claude-opus-4-8               claude-sonnet-4-6              claude-haiku-4-5
  nvidia_api       llama-3.3-70b-instruct        nemotron-70b-instruct          llama-3.2-3b-instruct
  xai_api          grok-4.3                      grok-4-1-fast-reasoning        — (falls through)
  copilot_oauth    copilot/gpt-5.5               copilot/claude-sonnet-4-6      copilot/gpt-5.4-mini
  grok_oauth       grok-sub/grok-4.3             grok-sub/grok-4-1-fast-reasoning — (falls through)
  pplx_oauth       pplx-sub/sonar-pro            pplx-sub/sonar                 — (falls through)

OpenAI-compatible gateways / aggregators (oh-my-pi parity)
----------------------------------------------------------
None ship a native LiteLLM provider, so each routes through ``openai/``
with an api_base override (table-driven in
``litellm_dynamic_config.OPENAI_COMPAT_GATEWAYS``). The model alias keeps
the gateway prefix so two gateways exposing the same upstream slug never
collide in the model_list.

                    HIGH                          MID                            LOW
  opencode_api     opencode/claude-opus-4-6      opencode/gpt-5.4               opencode/glm-5-free
  vercel_gateway   vercel/anthropic/…opus-4.6    vercel/anthropic/…sonnet-4.6  vercel/anthropic/…haiku-4.5
  huggingface_api  hf/…/DeepSeek-V3.1            hf/…/Llama-3.3-70B-…Turbo     hf/openai/gpt-oss-120b
  venice_api       venice/claude-opus-4-6        venice/claude-sonnet-4-6      venice/deepseek-v4-flash
  nanogpt_api      nanogpt/…/claude-opus-4.6     nanogpt/…/claude-sonnet-4.6   nanogpt/…/claude-3-5-haiku
  synthetic_api    synthetic/hf:…/DeepSeek-V3.2  synthetic/hf:…/Llama-3.3-70B  synthetic/hf:openai/gpt-oss
  zenmux_api       zenmux/anthropic/…opus-4.6    zenmux/anthropic/…sonnet-4.6  zenmux/anthropic/…haiku-4.5
  qianfan_api      qianfan/ernie-4.5-turbo-128k  qianfan/ernie-4.5-turbo-32k   qianfan/ernie-speed-pro-128k
  cloudflare_gw    cfgateway/anthropic/…opus     cfgateway/anthropic/…sonnet   cfgateway/anthropic/…haiku

Code-heavy override
-------------------
For roles that benefit from OpenAI's agentic coding specialization (patcher,
exploiter, contract_auditor, reverser, verifier), set
``DECEPTICON_MODEL_<ROLE>`` to one of the registered Codex variant routes:
  - ``openai/gpt-5.3-codex``       (paid API key)
  - ``auth/gpt-5.3-codex``         (ChatGPT subscription via Codex backend)
  - ``copilot/gpt-5.3-codex``      (GitHub Copilot subscription)
These are NOT default tier picks — gpt-5.5 stays HIGH for general agent
balance — but are registered so per-role overrides work without yaml edits.

Profiles
--------
  eco   per-agent tier (production default)
  max   every agent on HIGH (high-value targets)
  test  every agent on LOW (development / CI)

Model identifiers verified against provider docs as of 2026-05-14.
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

# ── Tier and AuthMethod enums ───────────────────────────────────────────


class Tier(StrEnum):
    """Model power level — orthogonal to authentication method."""

    HIGH = "high"
    MID = "mid"
    LOW = "low"


class AuthMethod(StrEnum):
    """A specific way to authenticate to a model provider.

    Each method routes to its own model identifier in LiteLLM and
    requires its own credential. Anthropic has two methods (API key
    via x-api-key, and Claude Code OAuth via Bearer token); these are
    independent credentials and can both be configured and prioritized
    separately.
    """

    ANTHROPIC_API = "anthropic_api"
    ANTHROPIC_OAUTH = "anthropic_oauth"  # Claude Code subscription (Max/Pro/Team)
    OPENAI_API = "openai_api"
    OPENAI_OAUTH = "openai_oauth"  # ChatGPT Pro/Plus/Team subscription
    GOOGLE_API = "google_api"
    GOOGLE_OAUTH = "google_oauth"  # Gemini Advanced (Google One AI Premium)
    MINIMAX_API = "minimax_api"
    DEEPSEEK_API = "deepseek_api"
    XAI_API = "xai_api"
    MISTRAL_API = "mistral_api"
    OPENROUTER_API = "openrouter_api"
    NVIDIA_API = "nvidia_api"
    OLLAMA_LOCAL = "ollama_local"  # Local LLM via Ollama (no API key, OLLAMA_API_BASE)
    OLLAMA_CLOUD = "ollama_cloud"  # Ollama Cloud (API key, OLLAMA_CLOUD_API_BASE)
    COPILOT_OAUTH = "copilot_oauth"  # Microsoft Copilot Pro subscription
    GROK_OAUTH = "grok_oauth"  # xAI SuperGrok (X Premium+)
    PERPLEXITY_OAUTH = "perplexity_oauth"  # Perplexity Pro subscription
    # ── Cloud gateways (multi-vendor model hubs, API-key auth) ──
    BEDROCK_API = "bedrock_api"  # AWS Bedrock (Anthropic/Llama/Mistral via AWS)
    VERTEX_API = "vertex_api"  # GCP Vertex AI (Anthropic/Gemini via GCP)
    AZURE_API = "azure_api"  # Azure OpenAI Service
    GROQ_API = "groq_api"  # Groq Cloud (LPU inference)
    TOGETHER_API = "together_api"  # Together AI
    FIREWORKS_API = "fireworks_api"  # Fireworks AI
    COHERE_API = "cohere_api"  # Cohere Command
    MOONSHOT_API = "moonshot_api"  # legacy Moonshot AI platform (api.moonshot.ai)
    KIMI_API = "kimi_api"  # Kimi for Coding (api.kimi.com/coding)
    ZAI_API = "zai_api"  # Z.ai GLM-4.5
    DASHSCOPE_API = "dashscope_api"  # Alibaba DashScope (Qwen)
    GITHUB_MODELS_API = "github_models_api"  # GitHub Models (PAT auth)
    LMSTUDIO_LOCAL = "lmstudio_local"  # Local LM Studio (OpenAI-compatible)
    LLAMACPP_LOCAL = "llamacpp_local"  # Local llama.cpp llama-server (OpenAI-compatible)
    CUSTOM_OPENAI_API = "custom_openai_api"  # Custom OpenAI-compatible endpoint
    CEREBRAS_API = "cerebras_api"  # Cerebras Inference (OpenAI-compatible)
    XIAOMI_MIMO_API = "xiaomi_mimo_api"  # Xiaomi MiMo (OpenAI-compatible)
    # ── OpenAI-compatible gateways / aggregators (oh-my-pi parity) ──
    # None ship a native LiteLLM provider, so each is reached through the
    # ``openai/`` provider with an explicit api_base override — the same
    # path xiaomi_mimo / custom already use, table-driven in
    # ``litellm_dynamic_config.OPENAI_COMPAT_GATEWAYS``. The model alias
    # keeps the gateway prefix (``opencode/claude-opus-4-6``) so routes
    # never collide when two gateways expose the same upstream slug.
    OPENCODE_API = "opencode_api"  # OpenCode Zen gateway (opencode.ai)
    VERCEL_GATEWAY_API = "vercel_gateway_api"  # Vercel AI Gateway
    HUGGINGFACE_API = "huggingface_api"  # Hugging Face Router (Inference Providers)
    VENICE_API = "venice_api"  # Venice AI (privacy-first, no logs)
    NANOGPT_API = "nanogpt_api"  # NanoGPT (pay-as-you-go aggregator)
    SYNTHETIC_API = "synthetic_api"  # Synthetic (synthetic.new)
    ZENMUX_API = "zenmux_api"  # ZenMux multi-vendor gateway
    QIANFAN_API = "qianfan_api"  # Baidu Qianfan (ERNIE) v2 OpenAI-compatible
    CLOUDFLARE_GATEWAY_API = "cloudflare_gateway_api"  # Cloudflare AI Gateway


# ── Tier × AuthMethod → model_id matrix ─────────────────────────────────
# When a (method, tier) entry is missing (e.g. minimax_api LOW), the chain
# resolver skips that method for that tier and falls through to the next
# method in priority order.

METHOD_MODELS: dict[AuthMethod, dict[Tier, str]] = {
    AuthMethod.ANTHROPIC_API: {
        Tier.HIGH: "anthropic/claude-opus-4-8",
        Tier.MID: "anthropic/claude-sonnet-5",
        Tier.LOW: "anthropic/claude-haiku-4-5",
    },
    AuthMethod.ANTHROPIC_OAUTH: {
        Tier.HIGH: "auth/claude-opus-4-8",
        Tier.MID: "auth/claude-sonnet-5",
        Tier.LOW: "auth/claude-haiku-4-5",
    },
    AuthMethod.OPENAI_API: {
        Tier.HIGH: "openai/gpt-5.5",
        Tier.MID: "openai/gpt-5.4",
        Tier.LOW: "openai/gpt-5-nano",
    },
    AuthMethod.GOOGLE_API: {
        Tier.HIGH: "gemini/gemini-2.5-pro",
        Tier.MID: "gemini/gemini-2.5-flash",
        Tier.LOW: "gemini/gemini-2.5-flash-lite",
    },
    AuthMethod.MINIMAX_API: {
        Tier.HIGH: "minimax/MiniMax-M3",
        Tier.MID: "minimax/MiniMax-M2.7-highspeed",
    },
    AuthMethod.OPENAI_OAUTH: {
        Tier.HIGH: "auth/gpt-5.5",
        Tier.MID: "auth/gpt-5.4",
        # ChatGPT subscription doesn't include gpt-5-nano; the Codex CLI
        # exposes ``gpt-5.4-mini`` as its small-tier slot (codex-rs
        # models-manager/models.json, May 2026), so route LOW there.
        Tier.LOW: "auth/gpt-5.4-mini",
    },
    AuthMethod.GOOGLE_OAUTH: {
        Tier.HIGH: "gemini-sub/gemini-2.5-pro",
        Tier.MID: "gemini-sub/gemini-2.5-flash",
    },
    AuthMethod.DEEPSEEK_API: {
        Tier.HIGH: "deepseek/deepseek-v4-pro",
        Tier.MID: "deepseek/deepseek-v4-flash",
        Tier.LOW: "deepseek/deepseek-v4-flash",
    },
    AuthMethod.XAI_API: {
        # grok-3 / grok-3-mini retired by xAI on 2026-05-15 (12pm PT).
        # grok-4.3 is xAI's current general-purpose flagship; the 4-1-fast
        # reasoning variant gives a cheaper second-best with comparable
        # tool-call quality for the MID tier.
        Tier.HIGH: "xai/grok-4.3",
        Tier.MID: "xai/grok-4-1-fast-reasoning",
    },
    AuthMethod.MISTRAL_API: {
        Tier.HIGH: "mistral/mistral-large-latest",
        Tier.MID: "mistral/codestral-latest",
    },
    AuthMethod.OPENROUTER_API: {
        Tier.HIGH: "openrouter/anthropic/claude-opus-4-8",
        Tier.MID: "openrouter/anthropic/claude-sonnet-4-6",
        Tier.LOW: "openrouter/anthropic/claude-haiku-4-5",
    },
    AuthMethod.NVIDIA_API: {
        Tier.HIGH: "nvidia_nim/meta/llama-3.3-70b-instruct",
        Tier.MID: "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct",
        Tier.LOW: "nvidia_nim/meta/llama-3.2-3b-instruct",
    },
    AuthMethod.OLLAMA_LOCAL: {
        # Ollama collapses to a single user-chosen model across tiers. The
        # actual identifier is resolved at chain-build time from
        # ``OLLAMA_MODEL`` so an OSS user can pick anything they pulled
        # locally (qwen3-coder:30b, llama3.2, deepseek-r1, ...). The
        # ``ollama_chat/`` provider prefix routes to /api/chat — the
        # endpoint that supports tool/function calling, which Decepticon
        # agents depend on. The values below are placeholders kept so
        # METHOD_MODELS stays exhaustive; resolve_chain branches before
        # reading them when method == OLLAMA_LOCAL.
        Tier.HIGH: "ollama_chat/__OLLAMA_MODEL__",
        Tier.MID: "ollama_chat/__OLLAMA_MODEL__",
        Tier.LOW: "ollama_chat/__OLLAMA_MODEL__",
    },
    AuthMethod.OLLAMA_CLOUD: {
        # Same shape as OLLAMA_LOCAL — resolved dynamically from
        # OLLAMA_CLOUD_MODEL at chain-build time. Uses the same
        # ollama_chat/ provider prefix (tool-call-capable /api/chat
        # endpoint) but routes through the cloud API base + key.
        Tier.HIGH: "ollama_cloud/__OLLAMA_CLOUD_MODEL__",
        Tier.MID: "ollama_cloud/__OLLAMA_CLOUD_MODEL__",
        Tier.LOW: "ollama_cloud/__OLLAMA_CLOUD_MODEL__",
    },
    AuthMethod.COPILOT_OAUTH: {
        # gpt-4o / o1 / o3-mini retired from GitHub Copilot on 2025-10-23.
        # Current Copilot OpenAI lineup: gpt-5 mini, gpt-5.2, gpt-5.2-Codex,
        # gpt-5.3-Codex, gpt-5.4, gpt-5.4 mini, gpt-5.4 nano, gpt-5.5.
        # Claude lineup: Haiku 4.5, Opus 4.5/4.6/4.7, Sonnet 4.5/4.6.
        # Picks below avoid the LiteLLM main.py:2561 short-circuit by using
        # slugs that are NOT in ``open_ai_chat_completion_models``:
        #   - gpt-5.5            (general HIGH)
        #   - claude-sonnet-4-6  (cyber MID per Cybench, cross-vendor via Copilot)
        #   - gpt-5.4-mini       (cost-effective LOW)
        # For code-heavy roles (patcher, exploiter, contract_auditor), the
        # ``copilot/gpt-5.3-codex`` route is registered as an alternative in
        # ``litellm_dynamic_config`` (sentinel-aliased to dodge bypass) and
        # can be selected per-agent via ``DECEPTICON_MODEL_<ROLE>``.
        Tier.HIGH: "copilot/gpt-5.5",
        Tier.MID: "copilot/claude-sonnet-4-6",
        Tier.LOW: "copilot/gpt-5.4-mini",
    },
    AuthMethod.GROK_OAUTH: {
        # grok-3 / grok-3-mini retired by xAI on 2026-05-15.
        Tier.HIGH: "grok-sub/grok-4.3",
        Tier.MID: "grok-sub/grok-4-1-fast-reasoning",
    },
    AuthMethod.PERPLEXITY_OAUTH: {
        Tier.HIGH: "pplx-sub/sonar-pro",
        Tier.MID: "pplx-sub/sonar",
    },
    # ── Cloud gateways ──
    # AWS Bedrock — Anthropic models hosted on AWS. Uses bedrock/<model>
    # which LiteLLM resolves via the bedrock-runtime SDK using
    # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION.
    AuthMethod.BEDROCK_API: {
        # Bedrock model IDs verified May 2026 against AWS Bedrock model
        # cards. Opus 4.7 + Sonnet 4.6 dropped the -v1:0 suffix; Haiku
        # keeps a date+version suffix on bedrock-runtime/invoke.
        Tier.HIGH: "bedrock/anthropic.claude-opus-4-7",
        Tier.MID: "bedrock/anthropic.claude-sonnet-4-6",
        Tier.LOW: "bedrock/anthropic.claude-haiku-4-5-20251001-v1:0",
    },
    # GCP Vertex AI — Claude + Gemini hosted on Google Cloud. Uses
    # vertex_ai/<model> with GOOGLE_APPLICATION_CREDENTIALS service-account
    # JSON path + VERTEXAI_PROJECT + VERTEXAI_LOCATION.
    AuthMethod.VERTEX_API: {
        # Vertex Anthropic models use @latest aliases when no specific
        # snapshot is needed. Override per-role via DECEPTICON_MODEL_<ROLE>
        # to pin a specific @YYYYMMDD snapshot from the Model Garden.
        Tier.HIGH: "vertex_ai/claude-opus-4-7@latest",
        Tier.MID: "vertex_ai/claude-sonnet-4-6@latest",
        Tier.LOW: "vertex_ai/gemini-2.5-flash",
    },
    # Azure OpenAI Service — model names are deployment IDs, so the user
    # configures AZURE_API_KEY + AZURE_API_BASE + AZURE_API_VERSION + their
    # deployment names. The defaults below assume standard deployment
    # naming; users can override per role via DECEPTICON_MODEL_<ROLE>.
    AuthMethod.AZURE_API: {
        Tier.HIGH: "azure/gpt-5.5",
        Tier.MID: "azure/gpt-5.4",
        Tier.LOW: "azure/gpt-5-nano",
    },
    # Groq — LPU inference, fast Llama. groq/<model>.
    # llama-3.1-70b-versatile was retired in 2026; using Llama 4 Scout
    # (Groq production model) at MID since Llama 3.3 70B sits at HIGH.
    AuthMethod.GROQ_API: {
        Tier.HIGH: "groq/llama-3.3-70b-versatile",
        Tier.MID: "groq/meta-llama/llama-4-scout-17b-16e-instruct",
        Tier.LOW: "groq/llama-3.1-8b-instant",
    },
    # Together AI — together_ai/<model>.
    AuthMethod.TOGETHER_API: {
        Tier.HIGH: "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        Tier.MID: "together_ai/mistralai/Mixtral-8x22B-Instruct-v0.1",
        Tier.LOW: "together_ai/meta-llama/Llama-3.2-3B-Instruct-Turbo",
    },
    # Fireworks AI — fireworks_ai/<model>. All three default to Llama
    # variants because Fireworks-hosted Mixtral does not reliably honor
    # the OpenAI tools schema and Decepticon agents always emit tool
    # calls. Override via DECEPTICON_MODEL_<ROLE> for non-tool roles.
    AuthMethod.FIREWORKS_API: {
        Tier.HIGH: "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct",
        Tier.MID: "fireworks_ai/accounts/fireworks/models/llama-v3p1-70b-instruct",
        Tier.LOW: "fireworks_ai/accounts/fireworks/models/llama-v3p2-3b-instruct",
    },
    # Cohere Command — cohere_chat/<model> (v2 API, supports tool use).
    # The bare ``cohere/`` prefix routes to the legacy completion
    # endpoint which silently drops the ``tools`` parameter; Decepticon
    # agents always emit tool calls so v2 is the only viable route.
    AuthMethod.COHERE_API: {
        Tier.HIGH: "cohere_chat/command-a-03-2025",
        Tier.MID: "cohere_chat/command-r-plus",
        Tier.LOW: "cohere_chat/command-r",
    },
    # Moonshot Kimi K2 — moonshot/<model>. K2 generation uses a single
    # ``kimi-k2-instruct`` ID (context window negotiated at the
    # request level, not encoded in the model id). Older v1 models
    # keep their context-tier suffixes (8k/32k/128k).
    AuthMethod.MOONSHOT_API: {
        Tier.HIGH: "moonshot/kimi-k2-instruct",
        Tier.MID: "moonshot/moonshot-v1-128k",
        Tier.LOW: "moonshot/moonshot-v1-8k",
    },
    # Kimi for Coding is a separate platform from the legacy Moonshot
    # API above: own endpoint, own keys (KIMI_API_KEY), own model ids.
    # Routed via the kimi/ gateway in OPENAI_COMPAT_GATEWAYS. HIGH is
    # K3 (1M context), MID is the coding-tuned K2.7, LOW its fast tier.
    AuthMethod.KIMI_API: {
        Tier.HIGH: "kimi/k3",
        Tier.MID: "kimi/kimi-for-coding",
        Tier.LOW: "kimi/kimi-for-coding-highspeed",
    },
    # Z.ai GLM family — native ``zai/`` LiteLLM provider (no custom shim
    # needed since LiteLLM 1.55+). LOW = glm-4.5-flash, the free-tier
    # model.
    AuthMethod.ZAI_API: {
        Tier.HIGH: "zai/glm-4.5",
        Tier.MID: "zai/glm-4.5-air",
        Tier.LOW: "zai/glm-4.5-flash",
    },
    # Alibaba DashScope (Qwen) — dashscope/<model>.
    AuthMethod.DASHSCOPE_API: {
        Tier.HIGH: "dashscope/qwen-max",
        Tier.MID: "dashscope/qwen-plus",
        Tier.LOW: "dashscope/qwen-turbo",
    },
    # GitHub Models — github/<model>, GITHUB_TOKEN PAT auth.
    AuthMethod.GITHUB_MODELS_API: {
        Tier.HIGH: "github/gpt-5.5",
        Tier.MID: "github/gpt-5.4",
        Tier.LOW: "github/gpt-5-nano",
    },
    # LM Studio — local OpenAI-compatible server. Like OLLAMA_LOCAL the
    # tier collapses to a single user-chosen model resolved at
    # chain-build time from LMSTUDIO_MODEL.
    AuthMethod.LMSTUDIO_LOCAL: {
        Tier.HIGH: "lm_studio/__LMSTUDIO_MODEL__",
        Tier.MID: "lm_studio/__LMSTUDIO_MODEL__",
        Tier.LOW: "lm_studio/__LMSTUDIO_MODEL__",
    },
    # llama.cpp llama-server — local OpenAI-compatible server (GGUF
    # models). Issue #151. Tiers collapse to a single model resolved at
    # chain-build time from LLAMACPP_MODEL because llama-server runs one
    # GGUF at a time. The route prefix is ``llamacpp/`` (not a native
    # LiteLLM provider — remapped to ``openai/<model>`` plus a custom
    # api_base by ``litellm_dynamic_config.build_model_entry``); kept
    # distinct from ``custom/`` so users can have BOTH a generic
    # OpenAI-compatible gateway AND llama.cpp wired up at the same time.
    AuthMethod.LLAMACPP_LOCAL: {
        Tier.HIGH: "llamacpp/__LLAMACPP_MODEL__",
        Tier.MID: "llamacpp/__LLAMACPP_MODEL__",
        Tier.LOW: "llamacpp/__LLAMACPP_MODEL__",
    },
    # Custom OpenAI-compatible endpoint — collapses tiers, model id from
    # CUSTOM_OPENAI_MODEL, base URL from CUSTOM_OPENAI_API_BASE.
    AuthMethod.CUSTOM_OPENAI_API: {
        Tier.HIGH: "custom/__CUSTOM_OPENAI_MODEL__",
        Tier.MID: "custom/__CUSTOM_OPENAI_MODEL__",
        Tier.LOW: "custom/__CUSTOM_OPENAI_MODEL__",
    },
    AuthMethod.CEREBRAS_API: {
        # Cerebras Inference — OpenAI-compatible at
        # ``https://api.cerebras.ai/v1``. Single production model SKU
        # documented as of 2026-05-15.
        Tier.HIGH: "cerebras/llama3.1-8b",
        Tier.MID: "cerebras/llama3.1-8b",
        Tier.LOW: "cerebras/llama3.1-8b",
    },
    AuthMethod.XIAOMI_MIMO_API: {
        # Xiaomi MiMo Open Platform — OpenAI-compatible
        # (``/v1/chat/completions``, Bearer auth). Production model IDs:
        # ``mimo-vl`` (multimodal flagship), ``mimo-rl`` (reasoning),
        # ``mimo-7b`` (lightweight). Routed through LiteLLM's
        # ``openai/`` provider with api_base override so the request
        # shape stays standard OpenAI without depending on a native
        # ``xiaomi_mimo/`` LiteLLM provider that may not exist yet.
        Tier.HIGH: "openai/mimo-vl",
        Tier.MID: "openai/mimo-rl",
        Tier.LOW: "openai/mimo-7b",
    },
    # ── OpenAI-compatible gateways / aggregators (oh-my-pi parity) ──
    # Model slugs below are grounded in oh-my-pi's published provider
    # catalog (packages/ai/src/models.json) as of 2026-06. Each route is
    # rewritten to ``openai/<slug>`` + the gateway's api_base by
    # ``litellm_dynamic_config.build_model_entry`` and mirrored as a
    # static entry in ``config/litellm.yaml``.
    AuthMethod.OPENCODE_API: {
        # OpenCode Zen — https://opencode.ai/zen/v1. Catalog spans Claude,
        # GPT-5.x, GLM, Kimi. LOW uses the free GLM tier.
        Tier.HIGH: "opencode/claude-opus-4-6",
        Tier.MID: "opencode/gpt-5.4",
        Tier.LOW: "opencode/glm-5-free",
    },
    AuthMethod.VERCEL_GATEWAY_API: {
        # Vercel AI Gateway — https://ai-gateway.vercel.sh/v1. Model ids
        # are ``creator/model``; route the Anthropic family for tool-call
        # reliability parity with the native anthropic path.
        Tier.HIGH: "vercel/anthropic/claude-opus-4.6",
        Tier.MID: "vercel/anthropic/claude-sonnet-4.6",
        Tier.LOW: "vercel/anthropic/claude-haiku-4.5",
    },
    AuthMethod.HUGGINGFACE_API: {
        # Hugging Face Router — https://router.huggingface.co/v1, HF_TOKEN
        # bearer auth. gpt-oss-120b is the free serverless tier at LOW.
        Tier.HIGH: "hf/deepseek-ai/DeepSeek-V3.1",
        Tier.MID: "hf/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        Tier.LOW: "hf/openai/gpt-oss-120b",
    },
    AuthMethod.VENICE_API: {
        # Venice AI — https://api.venice.ai/api/v1. Subscription/credits
        # model (per-token cost not published → model_info pinned to $0).
        Tier.HIGH: "venice/claude-opus-4-6",
        Tier.MID: "venice/claude-sonnet-4-6",
        Tier.LOW: "venice/deepseek-v4-flash",
    },
    AuthMethod.NANOGPT_API: {
        # NanoGPT — https://nano-gpt.com/api/v1, pay-as-you-go aggregator
        # exposing ``creator/model`` slugs across most major vendors.
        Tier.HIGH: "nanogpt/anthropic/claude-opus-4.6",
        Tier.MID: "nanogpt/anthropic/claude-sonnet-4.6",
        Tier.LOW: "nanogpt/anthropic/claude-3-5-haiku-20241022",
    },
    AuthMethod.SYNTHETIC_API: {
        # Synthetic — https://api.synthetic.new/openai/v1. Open-weight
        # models behind an ``hf:`` slug prefix; flat plan → cost $0.
        Tier.HIGH: "synthetic/hf:deepseek-ai/DeepSeek-V3.2",
        Tier.MID: "synthetic/hf:meta-llama/Llama-3.3-70B-Instruct",
        Tier.LOW: "synthetic/hf:openai/gpt-oss-120b",
    },
    AuthMethod.ZENMUX_API: {
        # ZenMux — https://zenmux.ai/api/v1. Multi-vendor gateway; route
        # the Anthropic family for tool-call parity.
        Tier.HIGH: "zenmux/anthropic/claude-opus-4.6",
        Tier.MID: "zenmux/anthropic/claude-sonnet-4.6",
        Tier.LOW: "zenmux/anthropic/claude-haiku-4.5",
    },
    AuthMethod.QIANFAN_API: {
        # Baidu Qianfan v2 — https://qianfan.baidubce.com/v2, OpenAI-
        # compatible. Model ids verified against the Qianfan platform doc
        # (cloud.baidu.com/doc/qianfan/s/rmh4stp0j) which matches this base
        # URL — note ERNIE Speed 128k is ``ernie-speed-pro-128k`` there.
        # Pricing is region/currency-dependent → cost $0 (not per-token
        # enforced). Ids drift; override per role via DECEPTICON_MODEL_<ROLE>.
        Tier.HIGH: "qianfan/ernie-4.5-turbo-128k",
        Tier.MID: "qianfan/ernie-4.5-turbo-32k",
        Tier.LOW: "qianfan/ernie-speed-pro-128k",
    },
    AuthMethod.CLOUDFLARE_GATEWAY_API: {
        # Cloudflare AI Gateway — per-account base URL, so the endpoint
        # comes from CLOUDFLARE_AI_GATEWAY_API_BASE (the OpenAI-compat
        # ``.../compat`` path). Model slugs are the gateway's
        # ``provider/model`` form proxied to Anthropic.
        Tier.HIGH: "cfgateway/anthropic/claude-opus-4-6",
        Tier.MID: "cfgateway/anthropic/claude-sonnet-4-6",
        Tier.LOW: "cfgateway/anthropic/claude-haiku-4-5",
    },
}


# ── Per-agent tier and temperature ──────────────────────────────────────
# eco profile uses these directly. max promotes everything to HIGH.
# test demotes everything to LOW.

AGENT_TIERS: dict[str, Tier] = {
    # HIGH — deep reasoning, multi-step planning, source-level analysis,
    # long context accumulation. Failure cost is mission-critical.
    "decepticon": Tier.HIGH,
    "exploit": Tier.HIGH,
    "exploiter": Tier.HIGH,
    "patcher": Tier.HIGH,
    "contract_auditor": Tier.HIGH,
    "analyst": Tier.HIGH,
    "vulnresearch": Tier.HIGH,
    # MID — precision execution, code generation, structured judgment.
    # Tool-heavy with moderate iteration.
    "detector": Tier.MID,
    "verifier": Tier.MID,
    "finding_reporter": Tier.MID,
    "blue_cell": Tier.MID,
    "postexploit": Tier.MID,
    "ad_operator": Tier.MID,
    "cloud_hunter": Tier.MID,
    "reverser": Tier.MID,
    "phisher": Tier.MID,
    "mobile_operator": Tier.MID,
    "iot_operator": Tier.MID,
    "ics_operator": Tier.MID,
    "forensicator": Tier.MID,
    "supply_chain_operator": Tier.MID,
    # LOW — high-throughput, low reasoning depth. Recon / triage / docs.
    "soundwave": Tier.LOW,
    "autohunt": Tier.LOW,
    "recon": Tier.LOW,
    "scanner": Tier.LOW,
    "wireless_operator": Tier.LOW,
    "osint_operator": Tier.LOW,
}

AGENT_TEMPERATURES: dict[str, float] = {
    "decepticon": 0.4,
    "soundwave": 0.4,
    "autohunt": 0.4,
    "exploit": 0.3,
    "exploiter": 0.2,
    "detector": 0.2,
    "verifier": 0.2,
    "finding_reporter": 0.2,
    "blue_cell": 0.2,
    "patcher": 0.2,
    "postexploit": 0.3,
    "ad_operator": 0.2,
    "cloud_hunter": 0.2,
    "contract_auditor": 0.2,
    "reverser": 0.2,
    "analyst": 0.2,
    "scanner": 0.2,
    "vulnresearch": 0.4,
    "recon": 0.3,
    "phisher": 0.4,
    "mobile_operator": 0.2,
    "wireless_operator": 0.2,
    "osint_operator": 0.3,
    "iot_operator": 0.2,
    "ics_operator": 0.1,
    "forensicator": 0.2,
    "supply_chain_operator": 0.2,
}


# ── Profile ─────────────────────────────────────────────────────────────


class ModelProfile(StrEnum):
    """Tier preset that overlays AGENT_TIERS."""

    ECO = "eco"
    MAX = "max"
    TEST = "test"


def _resolve_tier(role: str, profile: ModelProfile) -> Tier:
    """Resolve the effective tier for a role under a profile."""
    if profile == ModelProfile.MAX:
        return Tier.HIGH
    if profile == ModelProfile.TEST:
        return Tier.LOW
    return AGENT_TIERS[role]


# ── Credentials ─────────────────────────────────────────────────────────


class Credentials(BaseModel):
    """User's available LLM credentials, in priority order.

    ``methods`` lists each configured AuthMethod, ordered by user
    preference. Each entry is an independent credential — having both
    ``ANTHROPIC_OAUTH`` and ``ANTHROPIC_API`` is valid and useful
    (subscription primary, paid API as fallback). Order in this list
    becomes the model fallback order at every tier.
    """

    methods: list[AuthMethod] = Field(default_factory=list)

    @classmethod
    def all_api_methods(cls) -> Credentials:
        """All API methods in default priority order. Used as a
        development convenience and for tests that want a fully-populated
        inventory without specifying it inline."""
        return cls(
            methods=[
                AuthMethod.ANTHROPIC_API,
                AuthMethod.OPENAI_API,
                AuthMethod.GOOGLE_API,
                AuthMethod.MINIMAX_API,
                AuthMethod.DEEPSEEK_API,
                AuthMethod.XAI_API,
                AuthMethod.MISTRAL_API,
                AuthMethod.OPENROUTER_API,
                AuthMethod.NVIDIA_API,
            ]
        )


_OLLAMA_DEFAULT_MODEL = "llama3.2"
_OLLAMA_CLOUD_DEFAULT_MODEL = "llama3.2"
_LMSTUDIO_DEFAULT_MODEL = "qwen2.5-coder-7b-instruct"
_LLAMACPP_DEFAULT_MODEL = "qwen2.5-coder-7b-instruct-q4_k_m"
_CUSTOM_OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


def _resolve_ollama_model() -> str | None:
    """Return the LiteLLM model id for the user's local Ollama, or None.

    The user picks the model via ``OLLAMA_MODEL`` (free-form, e.g.
    ``qwen3-coder:30b``). We normalize to the ``ollama_chat/`` provider
    prefix so requests hit Ollama's ``/api/chat`` endpoint — the only
    one that exposes tool/function calling, which every Decepticon agent
    relies on.

    Returns None when the user hasn't opted in (OLLAMA_API_BASE empty
    *and* OLLAMA_MODEL empty); resolve_chain then skips OLLAMA_LOCAL,
    keeping the chain honest. When OLLAMA_API_BASE is set but
    OLLAMA_MODEL is not, we fall back to a sensible default rather than
    failing — the user explicitly enabled Ollama, just didn't pick a tag.
    """
    base = os.getenv("OLLAMA_API_BASE", "").strip()
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not base and not model:
        return None
    if not model:
        model = _OLLAMA_DEFAULT_MODEL
    return f"ollama_chat/{model}"


def _resolve_ollama_cloud_model() -> str | None:
    """Return the LiteLLM model id for the user's Ollama Cloud, or None.

    Reads ``OLLAMA_CLOUD_API_BASE`` / the cloud API key (per Ollama Cloud
    docs at https://docs.ollama.com/cloud) and ``OLLAMA_CLOUD_MODEL`` from
    the environment. Falls back to ``_OLLAMA_CLOUD_DEFAULT_MODEL`` when
    the base URL is set but no model is specified. Returns None when no
    cloud endpoint is configured at all.

    The key is read from ``OLLAMA_CLOUD_API_KEY`` first (what the onboard
    wizard and setup docs write), falling back to ``OLLAMA_API_KEY`` (the
    official Ollama convention) — the dynamic-config builder applies the
    same precedence when wiring Bearer auth.

    Uses a distinct ``ollama_cloud/`` provider prefix (not ``ollama_chat/``)
    so the dynamic-config builder can route to the cloud's OpenAI-
    compatible endpoint (``https://ollama.com/v1``) with Bearer auth
    instead of pointing at the local Ollama instance's ``OLLAMA_API_BASE``.
    """
    base = os.getenv("OLLAMA_CLOUD_API_BASE", "").strip()
    key = os.getenv("OLLAMA_CLOUD_API_KEY", "").strip() or os.getenv("OLLAMA_API_KEY", "").strip()
    model = os.getenv("OLLAMA_CLOUD_MODEL", "").strip()
    if not base and not key and not model:
        return None
    if not model:
        model = _OLLAMA_CLOUD_DEFAULT_MODEL
    return f"ollama_cloud/{model}"


def _resolve_lmstudio_model() -> str | None:
    """Return the LiteLLM model id for the user's LM Studio, or None.

    LM Studio exposes an OpenAI-compatible server on
    ``LMSTUDIO_API_BASE`` (default ``http://host.docker.internal:1234/v1``).
    The model id comes from ``LMSTUDIO_MODEL``. Returns None when neither
    env var is set so resolve_chain skips the method without leaking the
    placeholder ``lm_studio/__LMSTUDIO_MODEL__`` into the chain.
    """
    base = os.getenv("LMSTUDIO_API_BASE", "").strip()
    model = os.getenv("LMSTUDIO_MODEL", "").strip()
    if not base and not model:
        return None
    if not model:
        model = _LMSTUDIO_DEFAULT_MODEL
    return f"lm_studio/{model}"


def _resolve_llamacpp_model() -> str | None:
    """Return the LiteLLM model id for the user's llama.cpp server, or None.

    llama.cpp's ``llama-server`` exposes an OpenAI-compatible REST API
    (default ``http://localhost:8080/v1``). The route prefix is
    ``llamacpp/`` and the actual model name comes from ``LLAMACPP_MODEL``
    — typically the GGUF file's logical name (e.g. ``qwen2.5-coder-7b-
    instruct-q4_k_m``). The model name is mostly cosmetic at the server
    side because ``llama-server`` runs one GGUF at a time, but it shows
    up in LiteLLM logs and dashboards so a meaningful name helps.

    Returns None when neither ``LLAMACPP_API_BASE`` nor ``LLAMACPP_MODEL``
    is set so resolve_chain skips the method without leaking the
    placeholder ``llamacpp/__LLAMACPP_MODEL__`` into the chain. When
    only the base URL is set, ``_LLAMACPP_DEFAULT_MODEL`` provides a
    sensible default — same fail-soft policy as the Ollama / LM Studio
    resolvers.
    """
    base = os.getenv("LLAMACPP_API_BASE", "").strip()
    model = os.getenv("LLAMACPP_MODEL", "").strip()
    if not base and not model:
        return None
    if not model:
        model = _LLAMACPP_DEFAULT_MODEL
    return f"llamacpp/{model}"


def _resolve_custom_openai_model() -> str | None:
    """Return the LiteLLM model id for the user's custom endpoint, or None.

    Routed via the dynamic ``custom/`` provider in
    ``litellm_dynamic_config`` which sets ``api_base`` from
    ``CUSTOM_OPENAI_API_BASE`` and ``api_key`` from
    ``CUSTOM_OPENAI_API_KEY``. The actual model name comes from
    ``CUSTOM_OPENAI_MODEL``.
    """
    base = os.getenv("CUSTOM_OPENAI_API_BASE", "").strip()
    model = os.getenv("CUSTOM_OPENAI_MODEL", "").strip()
    if not base and not model:
        return None
    if not model:
        model = _CUSTOM_OPENAI_DEFAULT_MODEL
    return f"custom/{model}"


def resolve_chain(tier: Tier, credentials: Credentials) -> list[str]:
    """Build the model chain (primary first, then fallbacks) for a tier.

    Walks ``credentials.methods`` in order, picking the model for each
    method at the given tier from ``METHOD_MODELS``. When a method has
    no entry at that tier (minimax_api LOW), it's skipped and the chain
    continues with the next method.

    ``OLLAMA_LOCAL`` is special-cased: a local LLM has no concept of
    HIGH/MID/LOW tiers (typically a single model the user pulled), so
    every tier resolves to the same id derived from ``OLLAMA_MODEL``.
    When the env isn't wired up, the entry is skipped — preventing a
    placeholder ``ollama_chat/__OLLAMA_MODEL__`` from leaking into the
    chain. ``OLLAMA_CLOUD`` follows the same pattern.
    """
    chain: list[str] = []
    for method in credentials.methods:
        if method == AuthMethod.OLLAMA_LOCAL:
            ollama_model = _resolve_ollama_model()
            if ollama_model is not None:
                chain.append(ollama_model)
            continue
        if method == AuthMethod.OLLAMA_CLOUD:
            ollama_cloud_model = _resolve_ollama_cloud_model()
            if ollama_cloud_model is not None:
                chain.append(ollama_cloud_model)
            continue
        if method == AuthMethod.LMSTUDIO_LOCAL:
            lmstudio_model = _resolve_lmstudio_model()
            if lmstudio_model is not None:
                chain.append(lmstudio_model)
            continue
        if method == AuthMethod.LLAMACPP_LOCAL:
            llamacpp_model = _resolve_llamacpp_model()
            if llamacpp_model is not None:
                chain.append(llamacpp_model)
            continue
        if method == AuthMethod.CUSTOM_OPENAI_API:
            custom_model = _resolve_custom_openai_model()
            if custom_model is not None:
                chain.append(custom_model)
            continue
        model = METHOD_MODELS[method].get(tier)
        if model is not None:
            chain.append(model)
    return chain


# ── Configuration models ────────────────────────────────────────────────


_PROXY_DEFAULT_LOCAL_KEY = "sk-decepticon-master"  # nosemgrep: decepticon-no-hardcoded-default-key


class ProxyConfig(BaseModel):
    """LiteLLM proxy connection settings.

    The ``api_key`` default is the well-known local-dev placeholder
    documented in .env.example. Production deployments MUST override it
    via DECEPTICON_LLM__PROXY_API_KEY. The placeholder exists so
    module-level agent constructors (Decepticon imports its agents at
    package init time for the ``decepticon.agents.plugins.*`` entry
    points to register) can build a ChatOpenAI instance against the
    LiteLLM proxy without credentials in the local-dev path.
    """

    url: str = "http://localhost:4000"
    api_key: str = _PROXY_DEFAULT_LOCAL_KEY
    timeout: int = 120
    max_retries: int = 2


class ModelAssignment(BaseModel):
    """Primary + ordered fallbacks for an agent role.

    ``fallbacks`` mirrors the credentials priority list past the
    primary: every method the user configured below the first one
    appears here, in order. langchain's ``ModelFallbackMiddleware``
    walks these in sequence on primary failure.
    """

    primary: str
    fallbacks: list[str] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int | None = None

    @property
    def fallback(self) -> str | None:
        """First fallback or None. Kept for callers that read the
        single-fallback shape; new code should use ``fallbacks``."""
        return self.fallbacks[0] if self.fallbacks else None

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("temperature must be between 0.0 and 2.0")
        return v


class LLMModelMapping(BaseModel):
    """Role → ModelAssignment, built from credentials + profile.

    Construct via :meth:`from_credentials_and_profile` or
    :meth:`from_profile` (which assumes all-API-methods credentials).
    """

    assignments: dict[str, ModelAssignment] = Field(default_factory=dict)

    def get_assignment(self, role: str, *, default_role: str | None = None) -> ModelAssignment:
        """Get model assignment for a role.

        Plugin orchestrators with a custom role (e.g. ``"decepticon-pro"``)
        that aren't part of the OSS ``AGENT_TIERS`` registry can pass
        ``default_role="decepticon"`` (or any OSS role) to inherit that
        role's assignment as fallback. The plugin can later register its
        own assignment via entry-point without changing the call site.

        Raises ``KeyError`` if ``role`` has no assignment AND
        ``default_role`` is either ``None`` or also unassigned.
        """
        if role in self.assignments:
            return self.assignments[role]
        if default_role is not None and default_role in self.assignments:
            return self.assignments[default_role]
        raise KeyError(f"No model assignment for role: {role}")

    @classmethod
    def from_credentials_and_profile(
        cls,
        credentials: Credentials,
        profile: ModelProfile | str = ModelProfile.ECO,
    ) -> LLMModelMapping:
        """Build a mapping from a credentials inventory + profile.

        For each agent in ``AGENT_TIERS``: resolve the effective tier
        under the profile, then build the chain from credentials. If
        credentials produce no chain at all (empty methods list), the
        role is skipped — callers will fail at LLM init time.
        """
        profile = ModelProfile(profile)
        assignments: dict[str, ModelAssignment] = {}
        for role in AGENT_TIERS:
            tier = _resolve_tier(role, profile)
            chain = resolve_chain(tier, credentials)
            if not chain:
                continue
            assignments[role] = ModelAssignment(
                primary=chain[0],
                fallbacks=chain[1:],
                temperature=AGENT_TEMPERATURES.get(role, 0.7),
            )
        return cls(assignments=assignments)

    @classmethod
    def from_profile(cls, profile: ModelProfile | str) -> LLMModelMapping:
        """Build with the default credentials (all API methods, default
        priority). Used by tests and dev convenience — production code
        should pass an explicit Credentials inventory."""
        return cls.from_credentials_and_profile(Credentials.all_api_methods(), profile)


# `from __future__ import annotations` defers field-annotation evaluation,
# so pydantic needs a final rebuild pass once every referenced symbol is
# in scope. Skipping this leaves Credentials/LLMModelMapping in a "not
# fully defined" state and any instantiation raises.
Credentials.model_rebuild()
LLMModelMapping.model_rebuild()
ModelAssignment.model_rebuild()
ProxyConfig.model_rebuild()
