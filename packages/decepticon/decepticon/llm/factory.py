"""LLM Factory — creates ChatModel instances via LiteLLM proxy.

All LLM calls route through the LiteLLM Docker proxy for provider abstraction.
Provider API keys are configured in .env / docker-compose.yml.

Architecture:
    LLMFactory(proxy, mapping)
      → get_model("recon")  → ChatOpenAI(model="anthropic/claude-haiku-4-5")
      → get_fallback_models("recon") → [ChatOpenAI(model="openai/gpt-5-nano")]
                                         ↓
                        LiteLLM proxy → Anthropic/OpenAI/Google/etc.

Profile-aware: when no explicit mapping is provided, builds a
credentials-aware mapping from environment variables. The factory
inspects which credentials are configured (non-placeholder API keys
plus the OAuth toggle) and respects ``DECEPTICON_AUTH_PRIORITY`` for
ordering AuthMethods in the fallback chain.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from decepticon.llm.router import ModelRouter
from decepticon_core.registry import RoleRegistry
from decepticon_core.types.llm import (
    AGENT_TIERS,
    AuthMethod,
    Credentials,
    LLMModelMapping,
    ModelAssignment,
    ModelProfile,
    ProxyConfig,
    Tier,
    resolve_chain,
)
from decepticon_core.utils.logging import get_logger

log = get_logger("llm.factory")


# Output-token cap for every model created through this factory.
# We must set one explicitly: the LiteLLM proxy applies the Anthropic default
# of 4096 when the client sends no ``max_tokens``, and a single large
# ``write_file`` tool call (e.g. a full finding report or a recon target model)
# then truncates mid-argument — the tool-call JSON never closes, so the
# ``content`` arg is dropped and the write fails. The cap is per-model: we hand
# each model its OWN max-output ceiling so a big deliverable completes in one
# call. It is a ceiling, not a forced value — short replies cost nothing extra.
# Values above 64k require the streaming API path (the SDK already streams).
# Authoritative caps (see ``_model_max_output_tokens``): Claude Opus 4.x and
# Sonnet (4.6/5) = 128000; Claude Haiku 4.5 = 64000. Unknown models fall back
# to the safe 64k default. Override with ``DECEPTICON_LLM_MAX_TOKENS`` (an
# explicit value wins over the per-model resolution).
DEFAULT_LLM_MAX_TOKENS = 64000
LLM_MAX_TOKENS_ENV = "DECEPTICON_LLM_MAX_TOKENS"

DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS = 600
LLM_TIMEOUT_ENV = "DECEPTICON_LLM_TIMEOUT_SECONDS"
# `.env.example` documents the pydantic-settings nested form
# (``DECEPTICON_LLM__TIMEOUT``) because that's what the rest of the
# settings tree uses (``LLMConfig.timeout`` → httpx transport). The
# whole-coroutine guard below is a separate piece of plumbing — but
# from the user's point of view there is only one "LLM timeout" knob,
# so we honor the documented name too. Precedence:
# ``DECEPTICON_LLM_TIMEOUT_SECONDS`` (explicit, whole-coroutine) >
# ``DECEPTICON_LLM__TIMEOUT`` (the published env knob) > default.
LLM_TIMEOUT_ENV_ALIAS = "DECEPTICON_LLM__TIMEOUT"
LLM_EXTRA_HEADERS_ENV = "DECEPTICON_LLM_EXTRA_HEADERS"
LLM_DISABLE_STREAMING_ENV = "DECEPTICON_LLM_DISABLE_STREAMING"
_PROTECTED_EXTRA_HEADERS = frozenset({"authorization", "host", "content-length"})


def _model_max_output_tokens(model: str) -> int:
    """Return ``model``'s own max-output-token ceiling.

    Match on the model slug suffix (last path segment) so every namespace we
    route through resolves the same — ``anthropic/claude-opus-4-8``,
    ``auth/claude-opus-4-8``, ``openrouter/anthropic/claude-sonnet-4-6``.
    Opus 4.x and Sonnet (4.6/5) support 128000 output tokens; Haiku 4.5
    supports 64000. Unknown / non-Claude models fall back to the safe 64k
    default rather than an over-large value the upstream might reject.
    """
    slug = model.rsplit("/", 1)[-1].lower()
    if "opus" in slug or "sonnet" in slug or slug.startswith("claude-fable-5"):
        return 128000
    if "haiku" in slug:
        return 64000
    return DEFAULT_LLM_MAX_TOKENS


def _resolve_max_tokens(model: str) -> int:
    """Output-token cap for a factory-created model.

    ``DECEPTICON_LLM_MAX_TOKENS`` (explicit override) wins when set to a
    positive int; otherwise the model's own max-output ceiling is used so a
    large single-call deliverable (finding report, recon model) never truncates
    at the proxy's 4096 default.
    """
    raw = os.environ.get(LLM_MAX_TOKENS_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return _model_max_output_tokens(model)


class LLMTimeoutError(RuntimeError):
    """Raised when an async LLM request exceeds the configured per-call timeout.

    Distinct from generic ``asyncio.TimeoutError`` so middleware and retry
    layers can identify request-timeout failures without catching every
    cancellation in the loop. The timeout is whole-coroutine, not transport-
    level (``ProxyConfig.timeout`` already covers transport).
    """


def _resolve_llm_timeout_seconds() -> float:
    """Resolve the per-call LLM request timeout.

    Precedence: ``DECEPTICON_LLM_TIMEOUT_SECONDS`` env >
    ``DECEPTICON_LLM__TIMEOUT`` env (the pydantic-settings nested form
    advertised in ``.env.example``) > default ``600``. The alias is what
    users actually set when following the published docs — without it,
    changing the documented knob silently has no effect on the whole-
    coroutine guard and long generations still trip the 600 s default
    (see issue #540).

    Rejects non-positive or non-numeric env values with ``ValueError``
    so misconfiguration fails loudly rather than silently disabling the
    guard.
    """
    raw = os.getenv(LLM_TIMEOUT_ENV, "").strip()
    source = LLM_TIMEOUT_ENV
    if not raw:
        raw = os.getenv(LLM_TIMEOUT_ENV_ALIAS, "").strip()
        source = LLM_TIMEOUT_ENV_ALIAS
    if raw:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{source} must be a number (got {raw!r})") from exc
    else:
        value = float(DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS)
    if value <= 0:
        raise ValueError(f"{source} must be greater than 0")
    return value


def _resolve_extra_headers() -> dict[str, str] | None:
    raw = os.getenv(LLM_EXTRA_HEADERS_ENV, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{LLM_EXTRA_HEADERS_ENV} must be a JSON object") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in parsed.items()
    ):
        raise ValueError(f"{LLM_EXTRA_HEADERS_ENV} must be a JSON object of string headers")
    protected = sorted(name for name in parsed if name.lower() in _PROTECTED_EXTRA_HEADERS)
    if protected:
        raise ValueError(f"{LLM_EXTRA_HEADERS_ENV} cannot override {', '.join(protected)}")
    return parsed


def _resolve_disable_streaming() -> bool:
    raw = os.getenv(LLM_DISABLE_STREAMING_ENV, "").strip().lower()
    if not raw or raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    raise ValueError(f"{LLM_DISABLE_STREAMING_ENV} must be a boolean")


async def call_with_timeout(coro: Awaitable[Any], timeout: float) -> Any:
    """Wrap ``coro`` in :func:`asyncio.wait_for` and re-raise as :class:`LLMTimeoutError`.

    The translation preserves the original ``asyncio.TimeoutError`` via
    ``__cause__`` so debugging traces remain intact, while letting upstream
    code distinguish provider-stall timeouts from generic cancellation.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise LLMTimeoutError(f"LLM request timed out after {timeout:g} seconds") from exc


# Default ordering when DECEPTICON_AUTH_PRIORITY is not set. Every OAuth
# (subscription) method precedes its closest paid-API peer so a subscription
# is spent first and only falls back to the metered API when its quota hits —
# never the other way. Copilot (broad multi-model subscription) sits just
# after the OpenAI slot; Perplexity (search-augmented, niche for general
# agent work) sits low. Local/cloud-local methods sit at the end: hosted
# providers are usually preferred when both are available; locals are the
# last-resort fallback.
#
# NOTE: all six subscription methods are listed here so a credential wired
# without an explicit DECEPTICON_AUTH_PRIORITY is actually routed. Before,
# google/copilot/grok/perplexity OAuth were absent — a configured
# subscription was silently never used (surfaced by `decepticon-cli auth`).
_DEFAULT_AUTH_PRIORITY: tuple[AuthMethod, ...] = (
    AuthMethod.ANTHROPIC_OAUTH,
    AuthMethod.ANTHROPIC_API,
    AuthMethod.OPENAI_OAUTH,
    AuthMethod.OPENAI_API,
    AuthMethod.COPILOT_OAUTH,
    AuthMethod.GOOGLE_OAUTH,
    AuthMethod.GOOGLE_API,
    AuthMethod.MINIMAX_API,
    AuthMethod.DEEPSEEK_API,
    AuthMethod.GROK_OAUTH,
    AuthMethod.XAI_API,
    AuthMethod.MISTRAL_API,
    AuthMethod.OPENROUTER_API,
    AuthMethod.NVIDIA_API,
    AuthMethod.GROQ_API,
    AuthMethod.TOGETHER_API,
    AuthMethod.FIREWORKS_API,
    AuthMethod.COHERE_API,
    AuthMethod.MOONSHOT_API,
    AuthMethod.KIMI_API,
    AuthMethod.ZAI_API,
    AuthMethod.DASHSCOPE_API,
    AuthMethod.GITHUB_MODELS_API,
    AuthMethod.BEDROCK_API,
    AuthMethod.VERTEX_API,
    AuthMethod.AZURE_API,
    AuthMethod.PERPLEXITY_OAUTH,
    AuthMethod.LMSTUDIO_LOCAL,
    AuthMethod.LLAMACPP_LOCAL,
    AuthMethod.CUSTOM_OPENAI_API,
    AuthMethod.CEREBRAS_API,
    AuthMethod.XIAOMI_MIMO_API,
    # OpenAI-compatible gateways / aggregators (oh-my-pi parity). Placed
    # after the first-party API providers and before the local endpoints:
    # a hosted gateway is a reasonable fallback but shouldn't preempt a
    # direct vendor key the user also configured.
    AuthMethod.OPENCODE_API,
    AuthMethod.VERCEL_GATEWAY_API,
    AuthMethod.ZENMUX_API,
    AuthMethod.NANOGPT_API,
    AuthMethod.VENICE_API,
    AuthMethod.SYNTHETIC_API,
    AuthMethod.HUGGINGFACE_API,
    AuthMethod.QIANFAN_API,
    AuthMethod.CLOUDFLARE_GATEWAY_API,
    AuthMethod.OLLAMA_LOCAL,
    AuthMethod.OLLAMA_CLOUD,
)

# Each AuthMethod's detection rule:
#   - API methods: presence of a non-placeholder env var
#   - OAuth methods: an explicit "true" boolean env var (set by the
#     onboard wizard after a successful OAuth handshake)
#   - Local methods: their own env signal (OLLAMA_API_BASE for Ollama)
_API_METHOD_ENV: dict[AuthMethod, str] = {
    AuthMethod.ANTHROPIC_API: "ANTHROPIC_API_KEY",
    AuthMethod.OPENAI_API: "OPENAI_API_KEY",
    AuthMethod.GOOGLE_API: "GEMINI_API_KEY",
    AuthMethod.MINIMAX_API: "MINIMAX_API_KEY",
    AuthMethod.DEEPSEEK_API: "DEEPSEEK_API_KEY",
    AuthMethod.XAI_API: "XAI_API_KEY",
    AuthMethod.MISTRAL_API: "MISTRAL_API_KEY",
    AuthMethod.OPENROUTER_API: "OPENROUTER_API_KEY",
    AuthMethod.NVIDIA_API: "NVIDIA_API_KEY",
    # Cloud gateways added in the OpenClaude provider migration. Each
    # routes through LiteLLM's native provider implementation when the
    # corresponding API key is present and not a placeholder.
    AuthMethod.GROQ_API: "GROQ_API_KEY",
    AuthMethod.TOGETHER_API: "TOGETHER_API_KEY",
    AuthMethod.FIREWORKS_API: "FIREWORKS_API_KEY",
    AuthMethod.COHERE_API: "COHERE_API_KEY",
    AuthMethod.MOONSHOT_API: "MOONSHOT_API_KEY",
    AuthMethod.KIMI_API: "KIMI_API_KEY",
    AuthMethod.ZAI_API: "ZAI_API_KEY",
    AuthMethod.DASHSCOPE_API: "DASHSCOPE_API_KEY",
    AuthMethod.GITHUB_MODELS_API: "GITHUB_TOKEN",
    AuthMethod.BEDROCK_API: "AWS_ACCESS_KEY_ID",
    # Vertex uses a service-account JSON path; treat the path env var as
    # the credential signal so onboard's "real key" check works on it.
    AuthMethod.VERTEX_API: "GOOGLE_APPLICATION_CREDENTIALS",
    AuthMethod.AZURE_API: "AZURE_API_KEY",
    AuthMethod.CEREBRAS_API: "CEREBRAS_API_KEY",
    AuthMethod.XIAOMI_MIMO_API: "XIAOMI_MIMO_API_KEY",
    # OpenAI-compatible gateways / aggregators (oh-my-pi parity). Each is
    # detected by the presence of a non-placeholder bearer key. The base
    # URL is fixed per gateway (see config/litellm.yaml) except Cloudflare,
    # whose per-account CLOUDFLARE_AI_GATEWAY_API_BASE must also be set —
    # documented in .env.example; a key without the base 404s at call time.
    AuthMethod.OPENCODE_API: "OPENCODE_API_KEY",
    AuthMethod.VERCEL_GATEWAY_API: "VERCEL_AI_GATEWAY_API_KEY",
    AuthMethod.HUGGINGFACE_API: "HF_TOKEN",
    AuthMethod.VENICE_API: "VENICE_API_KEY",
    AuthMethod.NANOGPT_API: "NANOGPT_API_KEY",
    AuthMethod.SYNTHETIC_API: "SYNTHETIC_API_KEY",
    AuthMethod.ZENMUX_API: "ZENMUX_API_KEY",
    AuthMethod.QIANFAN_API: "QIANFAN_API_KEY",
    AuthMethod.CLOUDFLARE_GATEWAY_API: "CLOUDFLARE_AI_GATEWAY_API_KEY",
}

_OAUTH_METHOD_ENV: dict[AuthMethod, str] = {
    AuthMethod.ANTHROPIC_OAUTH: "DECEPTICON_AUTH_CLAUDE_CODE",
    AuthMethod.OPENAI_OAUTH: "DECEPTICON_AUTH_CHATGPT",
    AuthMethod.GOOGLE_OAUTH: "DECEPTICON_AUTH_GEMINI",
    AuthMethod.COPILOT_OAUTH: "DECEPTICON_AUTH_COPILOT",
    AuthMethod.GROK_OAUTH: "DECEPTICON_AUTH_GROK",
    AuthMethod.PERPLEXITY_OAUTH: "DECEPTICON_AUTH_PERPLEXITY",
}

# Vendor-specific API key prefix hints. When the method has a known
# canonical prefix, ``_is_real_key`` requires the value to start with one
# of these strings — protects against placeholder strings the launcher
# didn't emit (e.g. a user pasting ``sk-ant-not-used`` into .env).
_KEY_PREFIX_HINTS: dict[AuthMethod, tuple[str, ...]] = {
    AuthMethod.ANTHROPIC_API: ("sk-ant-",),
    AuthMethod.OPENAI_API: ("sk-",),
    AuthMethod.GOOGLE_API: ("AIza",),
    AuthMethod.XAI_API: ("xai-",),
    AuthMethod.GROQ_API: ("gsk_",),
    AuthMethod.OPENROUTER_API: ("sk-or-",),
    AuthMethod.NVIDIA_API: ("nvapi-",),
    AuthMethod.DEEPSEEK_API: ("sk-",),
    AuthMethod.GITHUB_MODELS_API: ("ghp_", "github_pat_", "gho_", "ghs_"),
    # Kimi for Coding keys ship as sk-kimi-...; accept plain sk- too.
    AuthMethod.KIMI_API: ("sk-kimi-", "sk-"),
}

# Substring tokens that mark a value as obviously not a real key.
# Catches creative placeholder values that don't match the launcher's
# ``your-…-key-here`` template.
_PLACEHOLDER_TOKENS: tuple[str, ...] = (
    "placeholder",
    "not-used",
    "not_used",
    "dummy",
    "fake",
    "example",
)

# Minimum length for any value that should be treated as a real key. All
# vendor-issued keys exceed this — Anthropic ``sk-ant-api03-…`` ≈ 100 chars,
# OpenAI ``sk-…`` ≥ 48 chars, Google ``AIza…`` 39 chars. 24 leaves headroom
# for vendors with shorter formats (Mistral, etc.) without admitting
# obviously-junk values.
_KEY_MIN_LENGTH = 24

# OAuth methods carry a host-side credentials file. Booleans like
# ``DECEPTICON_AUTH_CLAUDE_CODE=true`` are intent (the user enabled the
# subscription) — they don't guarantee the actual file exists. The
# factory verifies file presence + valid JSON before adding a method to
# the chain so a user who ran ``codex logout`` without flipping the
# boolean back doesn't generate a noisy 401-fallback storm.
#
# Each tuple is ordered: primary path first, legacy paths after. Env-var
# overrides take precedence over the literal default. The langgraph
# compose service mounts the Claude + Codex paths read-only so this
# check sees the same files the LiteLLM handlers will read.
_OAUTH_CREDENTIAL_PATHS: dict[AuthMethod, tuple[tuple[str, str], ...]] = {
    AuthMethod.ANTHROPIC_OAUTH: (
        ("CLAUDE_CODE_CREDENTIALS_PATH", "~/.claude/.credentials.json"),
        ("", "~/.config/anthropic/q/tokens.json"),  # legacy emulator path
    ),
    AuthMethod.OPENAI_OAUTH: (("CODEX_AUTH_PATH", "~/.codex/auth.json"),),
    AuthMethod.GOOGLE_OAUTH: (("GEMINI_TOKENS_PATH", "~/.config/gemini/tokens.json"),),
    AuthMethod.COPILOT_OAUTH: (("COPILOT_TOKENS_PATH", "~/.config/copilot/tokens.json"),),
    AuthMethod.GROK_OAUTH: (("GROK_TOKENS_PATH", "~/.config/grok/tokens.json"),),
    AuthMethod.PERPLEXITY_OAUTH: (("PERPLEXITY_TOKENS_PATH", "~/.config/perplexity/tokens.json"),),
}

# Env vars each LiteLLM OAuth handler accepts as a credential *in place of*
# its on-disk token file (see config/*_handler.py ``_load_tokens`` /
# ``_env_override_tokens`` / ``_resolve_source_token``). A non-empty value in
# any of these means the handler can authenticate without a file, so the
# method must still count as configured. Codex (OPENAI_OAUTH) exposes no
# env-token override — only the file-path env vars already covered by
# ``_OAUTH_CREDENTIAL_PATHS`` — so it is intentionally absent here.
_OAUTH_ENV_CREDENTIALS: dict[AuthMethod, tuple[str, ...]] = {
    AuthMethod.ANTHROPIC_OAUTH: ("ANTHROPIC_OAUTH_TOKEN",),
    AuthMethod.GOOGLE_OAUTH: ("GEMINI_ACCESS_TOKEN", "GEMINI_SESSION_COOKIES"),
    AuthMethod.COPILOT_OAUTH: ("COPILOT_ACCESS_TOKEN", "COPILOT_REFRESH_TOKEN"),
    AuthMethod.GROK_OAUTH: ("GROK_ACCESS_TOKEN", "GROK_SESSION_TOKEN"),
    AuthMethod.PERPLEXITY_OAUTH: ("PERPLEXITY_ACCESS_TOKEN", "PERPLEXITY_SESSION_TOKEN"),
}


def _ollama_cloud_configured() -> bool:
    """Return True when the user has wired up Ollama Cloud.

    Any of ``OLLAMA_CLOUD_API_BASE`` (preferred — explicit endpoint),
    ``OLLAMA_CLOUD_MODEL``, or a cloud API key
    (``OLLAMA_CLOUD_API_KEY`` / ``OLLAMA_API_KEY``) is enough to opt in.
    The key is included so a user who pasted only their key into
    `decepticon onboard` (relying on the default base + model) is still
    detected as a cloud user instead of silently falling through.
    """
    return bool(
        os.getenv("OLLAMA_CLOUD_API_BASE", "").strip()
        or os.getenv("OLLAMA_CLOUD_MODEL", "").strip()
        or os.getenv("OLLAMA_CLOUD_API_KEY", "").strip()
        or os.getenv("OLLAMA_API_KEY", "").strip()
    )


def _ollama_local_configured() -> bool:
    """Return True when the user has wired up local Ollama.

    Either ``OLLAMA_API_BASE`` (preferred — explicit endpoint) or
    ``OLLAMA_MODEL`` (a pulled model id) is enough to opt in. Both
    blank → not configured. Empty/whitespace strings are treated as
    "not set" so a stray ``OLLAMA_API_BASE=`` line in .env doesn't
    silently enable the method.
    """
    return bool(os.getenv("OLLAMA_API_BASE", "").strip() or os.getenv("OLLAMA_MODEL", "").strip())


def _lmstudio_local_configured() -> bool:
    """Return True when the user has wired up local LM Studio."""
    return bool(
        os.getenv("LMSTUDIO_API_BASE", "").strip() or os.getenv("LMSTUDIO_MODEL", "").strip()
    )


def _llamacpp_local_configured() -> bool:
    """Return True when the user has wired up local llama.cpp llama-server.

    Either ``LLAMACPP_API_BASE`` (preferred — explicit endpoint, e.g.
    ``http://localhost:8080/v1``) or ``LLAMACPP_MODEL`` (a logical model
    name) is enough to opt in. ``LLAMACPP_API_KEY`` is *not* required —
    llama-server accepts any string by default and an unset key resolves
    to a literal placeholder via LiteLLM's env interpolation, which the
    server happily accepts. See issue #151.
    """
    return bool(
        os.getenv("LLAMACPP_API_BASE", "").strip() or os.getenv("LLAMACPP_MODEL", "").strip()
    )


def _custom_openai_configured() -> bool:
    """Return True when the user has wired up a custom OpenAI-compatible
    endpoint. Both ``CUSTOM_OPENAI_API_BASE`` (URL) and
    ``CUSTOM_OPENAI_API_KEY`` (real, non-placeholder) are required —
    a base URL alone won't authenticate, and a key without a URL has
    nowhere to point.
    """
    base = os.getenv("CUSTOM_OPENAI_API_BASE", "").strip()
    key = os.getenv("CUSTOM_OPENAI_API_KEY", "")
    return bool(base) and _is_real_key(key)


def _is_real_key(value: str, method: AuthMethod | None = None) -> bool:
    """Validate that ``value`` looks like a real provider API key.

    Layers, in order:
      1. Strip whitespace; reject empty.
      2. Reject anything shorter than ``_KEY_MIN_LENGTH`` (24 chars) —
         every vendor-issued key exceeds this, while typical placeholders
         (``sk-ant-test``, ``not-set``) do not.
      3. Reject the launcher's template strings (``your-…-key-here``).
      4. Reject values containing obvious placeholder tokens
         (``placeholder``, ``not-used``, ``dummy``, …) — guards against
         creative .env values that escape the launcher template.
      5. When ``method`` is given and ``_KEY_PREFIX_HINTS`` defines a
         canonical prefix for it, require ``value`` to start with one of
         the prefixes. Catches mis-pasted keys (e.g. an OpenAI key in
         the Anthropic slot) before they propagate into the chain.

    ``method=None`` skips the prefix check — kept for callers like
    ``_custom_openai_configured`` where the vendor's expected prefix is
    deployment-specific (any OpenAI-compatible gateway).
    """
    v = value.strip()
    if not v or len(v) < _KEY_MIN_LENGTH:
        return False
    lower = v.lower()
    if lower.startswith("your-") or lower.endswith("-key-here"):
        return False
    if any(token in lower for token in _PLACEHOLDER_TOKENS):
        return False
    if method is not None:
        prefixes = _KEY_PREFIX_HINTS.get(method)
        if prefixes and not any(v.startswith(prefix) for prefix in prefixes):
            return False
    return True


def _oauth_credentials_present(method: AuthMethod) -> bool:
    """Return True if the host-side credential file for ``method`` exists.

    The factory layer reads this to keep the credentials inventory
    honest — without it, ``DECEPTICON_AUTH_CLAUDE_CODE=true`` plus a
    deleted ``~/.claude/.credentials.json`` would still place the OAuth
    method in every fallback chain, generating one 401 per request.

    Each path is checked in order. ``/dev/null`` (the docker-compose
    fallback when no credentials volume is wired) parses as empty, so
    the JSON-validation step fails closed.
    """
    candidates = _OAUTH_CREDENTIAL_PATHS.get(method)
    if not candidates:
        # Method has no documented file path — fall back to the boolean
        # flag alone for forward compatibility.
        return True
    for env_var, default in candidates:
        raw = os.environ.get(env_var, "").strip() if env_var else ""
        path = Path(raw).expanduser() if raw else Path(default).expanduser()
        try:
            text = path.read_text()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError):
            continue
        except OSError:
            # ``/dev/null`` reads to empty without raising; other transient
            # I/O errors fall through to the next candidate.
            text = ""
        text = text.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data:
            return True
    return False


def _oauth_env_credentials_present(method: AuthMethod) -> bool:
    """Return True if any env var the handler accepts as a credential is set.

    Env-only OAuth setups (e.g. ``GEMINI_ACCESS_TOKEN`` with no token file)
    are valid: the LiteLLM handler authenticates straight from the env. Such
    a method must not be dropped from the fallback chain just because no file
    exists on disk.
    """
    return any(os.getenv(env_var, "").strip() for env_var in _OAUTH_ENV_CREDENTIALS.get(method, ()))


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


def _parse_auth_priority() -> tuple[list[AuthMethod], bool]:
    """Parse ``DECEPTICON_AUTH_PRIORITY`` into an ordered AuthMethod list.

    Returns ``(priority, priority_explicit)``: the ordered method list and a
    flag for whether the env var was set non-empty. When it is unset/blank
    the default ordering (``_DEFAULT_AUTH_PRIORITY``) is returned. Unknown
    tokens are logged and skipped.
    """
    priority_raw = os.getenv("DECEPTICON_AUTH_PRIORITY", "")
    priority_explicit = bool(priority_raw.strip())
    if priority_explicit:
        priority: list[AuthMethod] = []
        for token in priority_raw.split(","):
            token = token.strip().lower()
            if not token:
                continue
            try:
                priority.append(AuthMethod(token))
            except ValueError:
                log.warning("Unknown method in DECEPTICON_AUTH_PRIORITY: %s", token)
    else:
        priority = list(_DEFAULT_AUTH_PRIORITY)
    return priority, priority_explicit


def _method_is_configured(method: AuthMethod) -> bool:
    """Return True if ``method``'s credential-detection rule passes.

    Single source of truth shared by ``_detect_available_methods`` (which
    builds the runtime fallback chain) and ``auth_inventory`` (which reports
    per-method status to the CLI / SDK / dashboard).
    """
    if method in _API_METHOD_ENV:
        return _is_real_key(os.getenv(_API_METHOD_ENV[method], ""), method)
    if method in _OAUTH_METHOD_ENV:
        # OAuth methods need the boolean intent AND a usable credential. The
        # credential may live in a token file OR in the handler's env-var
        # override — env-only setups are valid and must not be dropped.
        if not _is_truthy(os.getenv(_OAUTH_METHOD_ENV[method], "")):
            return False
        return _oauth_credentials_present(method) or _oauth_env_credentials_present(method)
    if method == AuthMethod.OLLAMA_LOCAL:
        return _ollama_local_configured()
    if method == AuthMethod.OLLAMA_CLOUD:
        return _ollama_cloud_configured()
    if method == AuthMethod.LMSTUDIO_LOCAL:
        return _lmstudio_local_configured()
    if method == AuthMethod.LLAMACPP_LOCAL:
        return _llamacpp_local_configured()
    if method == AuthMethod.CUSTOM_OPENAI_API:
        return _custom_openai_configured()
    return False


def _detect_available_methods(priority: list[AuthMethod]) -> list[AuthMethod]:
    """Return the subset of ``priority`` whose credential-detection rule passes.

    - API methods: their key env var is set to a non-placeholder
    - OAuth methods: their boolean env var is truthy AND the credential
      file is present
    - Local methods: their own env signal is configured
    """
    return [method for method in priority if _method_is_configured(method)]


def _fallback_credentials(*, priority_explicit: bool) -> Credentials:
    """Build the fallback Credentials when no priority method was detected.

    A user who wired only a local/cloud OpenAI-compatible endpoint (Ollama,
    LM Studio, llama.cpp, custom) but authored no priority list gets a
    single-method chain for it. Otherwise falls back to all API methods so
    module-level agent constructors stay importable; ``priority_explicit``
    only changes the log severity (ERROR vs INFO).
    """
    # Local-only or cloud-only OSS path: a user who set Ollama env vars
    # but didn't write a priority list gets a single-method Ollama chain.
    if _ollama_local_configured():
        log.info(
            "Only OLLAMA_API_BASE/OLLAMA_MODEL detected; running against local Ollama exclusively"
        )
        return Credentials(methods=[AuthMethod.OLLAMA_LOCAL])
    if _ollama_cloud_configured():
        log.info(
            "Only OLLAMA_CLOUD_API_BASE/OLLAMA_CLOUD_MODEL detected; "
            "running against Ollama Cloud exclusively"
        )
        return Credentials(methods=[AuthMethod.OLLAMA_CLOUD])
    if _lmstudio_local_configured():
        log.info("Only LMSTUDIO_API_BASE/LMSTUDIO_MODEL detected; using LM Studio")
        return Credentials(methods=[AuthMethod.LMSTUDIO_LOCAL])
    if _llamacpp_local_configured():
        log.info("Only LLAMACPP_API_BASE/LLAMACPP_MODEL detected; using llama.cpp")
        return Credentials(methods=[AuthMethod.LLAMACPP_LOCAL])
    if _custom_openai_configured():
        log.info("Only CUSTOM_OPENAI_* detected; using custom OpenAI-compatible endpoint")
        return Credentials(methods=[AuthMethod.CUSTOM_OPENAI_API])
    if priority_explicit:
        # User expressed clear intent (set DECEPTICON_AUTH_PRIORITY) but
        # every listed method failed detection. Surface the root cause
        # at ERROR level — otherwise the silent fallback to
        # all_api_methods() runs through providers the user doesn't
        # have, producing a confusing 401 cascade (often masked as a
        # downstream "rate limit (429)" once the routed-to provider
        # cools down). Return behavior preserved so module imports
        # stay green; real model calls still surface a remediation
        # hint via _reraise_with_actionable_message.
        log.error(
            "DECEPTICON_AUTH_PRIORITY=%r set but no listed method has "
            "detectable credentials. Verify: (1) API keys are "
            "non-placeholder (e.g. ANTHROPIC_API_KEY starts with "
            "'sk-ant-'), (2) OAuth flag matches credential file "
            "(e.g. DECEPTICON_AUTH_CLAUDE_CODE=true requires "
            "~/.claude/.credentials.json to exist and contain a valid "
            "JSON object — a /dev/null mount fails this check). "
            "Falling back to all-API-methods so module imports "
            "remain importable; every model call will 401 until "
            "the priority chain is fixed.",
            os.getenv("DECEPTICON_AUTH_PRIORITY", ""),
        )
    else:
        log.info(
            "No credentials detected in environment; using all-API-methods "
            "fallback so module-level agent constructors stay importable"
        )
    return Credentials.all_api_methods()


def _resolve_credentials() -> Credentials:
    """Build Credentials from environment variables.

    Walks ``DECEPTICON_AUTH_PRIORITY`` (comma-separated AuthMethod
    values; defaults to ``_DEFAULT_AUTH_PRIORITY``) and includes only
    methods whose detection rule passes:

      - API methods: their key env var is set to a non-placeholder
      - OAuth methods: their boolean env var is set truthy

    When **nothing** is detected — typical of CI / dev shells where
    onboard hasn't run — falls back to all four API methods. This keeps
    module-level ``graph = create_X_agent()`` calls importable so the
    test suite (and tools like langgraph Studio) can load agents
    without API keys present. Real LLM calls under that fallback will
    fail at request time with a provider 401, which is the correct
    surface for that misconfiguration.
    """
    priority, priority_explicit = _parse_auth_priority()

    methods = _detect_available_methods(priority)

    if not methods:
        return _fallback_credentials(priority_explicit=priority_explicit)

    return Credentials(methods=methods)


# ── Auth inventory (headless introspection for CLI / SDK / dashboard) ─────

_SUBSCRIPTION_METHODS: frozenset[AuthMethod] = frozenset(_OAUTH_METHOD_ENV)
_LOCAL_METHODS: frozenset[AuthMethod] = frozenset(
    {
        AuthMethod.OLLAMA_LOCAL,
        AuthMethod.LMSTUDIO_LOCAL,
        AuthMethod.LLAMACPP_LOCAL,
        AuthMethod.CUSTOM_OPENAI_API,
    }
)

# Signal env var for local/cloud endpoints (API + OAuth methods get theirs
# from _API_METHOD_ENV / _OAUTH_METHOD_ENV).
_LOCAL_METHOD_ENV: dict[AuthMethod, str] = {
    AuthMethod.OLLAMA_LOCAL: "OLLAMA_API_BASE",
    AuthMethod.OLLAMA_CLOUD: "OLLAMA_CLOUD_API_BASE",
    AuthMethod.LMSTUDIO_LOCAL: "LMSTUDIO_API_BASE",
    AuthMethod.LLAMACPP_LOCAL: "LLAMACPP_API_BASE",
    AuthMethod.CUSTOM_OPENAI_API: "CUSTOM_OPENAI_API_BASE",
}

_METHOD_LABEL: dict[AuthMethod, str] = {
    AuthMethod.ANTHROPIC_API: "Anthropic — API key",
    AuthMethod.ANTHROPIC_OAUTH: "Anthropic — Claude Code (Max/Pro/Team)",
    AuthMethod.OPENAI_API: "OpenAI — API key",
    AuthMethod.OPENAI_OAUTH: "OpenAI — ChatGPT (Pro/Plus/Team)",
    AuthMethod.GOOGLE_API: "Google — Gemini API key",
    AuthMethod.GOOGLE_OAUTH: "Google — Gemini Advanced (Google One)",
    AuthMethod.MINIMAX_API: "MiniMax — API key",
    AuthMethod.DEEPSEEK_API: "DeepSeek — API key",
    AuthMethod.XAI_API: "xAI — API key",
    AuthMethod.MISTRAL_API: "Mistral — API key",
    AuthMethod.OPENROUTER_API: "OpenRouter — API key",
    AuthMethod.NVIDIA_API: "NVIDIA NIM — API key",
    AuthMethod.GROQ_API: "Groq — API key",
    AuthMethod.TOGETHER_API: "Together AI — API key",
    AuthMethod.FIREWORKS_API: "Fireworks AI — API key",
    AuthMethod.COHERE_API: "Cohere — API key",
    AuthMethod.MOONSHOT_API: "Moonshot (Kimi) — API key",
    AuthMethod.KIMI_API: "Kimi for Coding (K3/K2.7) — API key",
    AuthMethod.ZAI_API: "Z.ai (GLM) — API key",
    AuthMethod.DASHSCOPE_API: "Alibaba DashScope (Qwen) — API key",
    AuthMethod.GITHUB_MODELS_API: "GitHub Models — PAT",
    AuthMethod.BEDROCK_API: "AWS Bedrock — AWS credentials",
    AuthMethod.VERTEX_API: "GCP Vertex AI — service account",
    AuthMethod.AZURE_API: "Azure OpenAI — API key",
    AuthMethod.CEREBRAS_API: "Cerebras — API key",
    AuthMethod.XIAOMI_MIMO_API: "Xiaomi MiMo — API key",
    AuthMethod.OPENCODE_API: "OpenCode Zen — API key",
    AuthMethod.VERCEL_GATEWAY_API: "Vercel AI Gateway — API key",
    AuthMethod.HUGGINGFACE_API: "Hugging Face Router — token",
    AuthMethod.VENICE_API: "Venice AI — API key",
    AuthMethod.NANOGPT_API: "NanoGPT — API key",
    AuthMethod.SYNTHETIC_API: "Synthetic — API key",
    AuthMethod.ZENMUX_API: "ZenMux — API key",
    AuthMethod.QIANFAN_API: "Baidu Qianfan (ERNIE) — API key",
    AuthMethod.CLOUDFLARE_GATEWAY_API: "Cloudflare AI Gateway — API key + base URL",
    AuthMethod.COPILOT_OAUTH: "GitHub Copilot — Pro subscription",
    AuthMethod.GROK_OAUTH: "xAI SuperGrok — X Premium+",
    AuthMethod.PERPLEXITY_OAUTH: "Perplexity — Pro subscription",
    AuthMethod.OLLAMA_LOCAL: "Ollama — local",
    AuthMethod.OLLAMA_CLOUD: "Ollama Cloud — API key",
    AuthMethod.LMSTUDIO_LOCAL: "LM Studio — local",
    AuthMethod.LLAMACPP_LOCAL: "llama.cpp — local",
    AuthMethod.CUSTOM_OPENAI_API: "Custom OpenAI-compatible endpoint",
}


def _method_kind(method: AuthMethod) -> str:
    if method in _SUBSCRIPTION_METHODS:
        return "subscription"
    if method in _LOCAL_METHODS:
        return "local"
    return "api"


def _method_env_var(method: AuthMethod) -> str:
    if method in _API_METHOD_ENV:
        return _API_METHOD_ENV[method]
    if method in _OAUTH_METHOD_ENV:
        return _OAUTH_METHOD_ENV[method]
    return _LOCAL_METHOD_ENV.get(method, "")


def _method_detail(method: AuthMethod, *, configured: bool) -> str:
    """One-line hint: confirmation when configured, remediation otherwise."""
    env = _method_env_var(method)
    if method in _OAUTH_METHOD_ENV:
        flag = _OAUTH_METHOD_ENV[method]
        if configured:
            if _oauth_env_credentials_present(method):
                return f"{flag}=true and credential present (env)"
            return f"{flag}=true and credential file present"
        if not _is_truthy(os.getenv(flag, "")):
            return f"set {flag}=true and provide the subscription credential"
        paths = ", ".join(default for _env, default in _OAUTH_CREDENTIAL_PATHS.get(method, ()))
        env_vars = ", ".join(_OAUTH_ENV_CREDENTIALS.get(method, ()))
        sources = paths or "token"
        if env_vars:
            sources = f"{sources} or {env_vars}"
        return f"{flag}=true but no credential ({sources})"
    if method in _LOCAL_METHODS or method == AuthMethod.OLLAMA_CLOUD:
        return f"{env} set" if configured else f"set {env} (and the matching *_MODEL)"
    if configured:
        return f"{env} set"
    prefixes = _KEY_PREFIX_HINTS.get(method)
    hint = f" (starts with {prefixes[0]!r})" if prefixes else ""
    return f"set {env}{hint}"


@dataclass(frozen=True, slots=True)
class AuthMethodStatus:
    """Configuration status of one :class:`AuthMethod`."""

    method: AuthMethod
    kind: str  # "api" | "subscription" | "local"
    label: str
    env_var: str
    configured: bool
    in_priority: bool
    active: bool  # configured AND on the resolved fallback chain
    detail: str


@dataclass(frozen=True, slots=True)
class AuthInventory:
    """Snapshot of every auth method's status + the resolved fallback chain."""

    statuses: tuple[AuthMethodStatus, ...]
    resolved_chain: tuple[AuthMethod, ...]
    priority_explicit: bool

    @property
    def configured(self) -> tuple[AuthMethodStatus, ...]:
        return tuple(s for s in self.statuses if s.configured)

    @property
    def any_active(self) -> bool:
        return bool(self.resolved_chain)

    @property
    def configured_but_idle(self) -> tuple[AuthMethodStatus, ...]:
        """Configured methods NOT on the resolved chain — almost always
        because the method is missing from ``DECEPTICON_AUTH_PRIORITY``
        (google_oauth / copilot_oauth / grok_oauth / perplexity_oauth are
        not in the default priority), so the credential is wired but the
        runtime will never route to it."""
        return tuple(s for s in self.statuses if s.configured and not s.active)


def auth_inventory() -> AuthInventory:
    """Introspect provider/auth configuration from the environment.

    Pure env + credential-file inspection (no network, no model
    construction), safe to call from the CLI, SDK, web dashboard, or a CI
    preflight. Reuses the same detection rules the runtime factory uses, so
    what this reports is exactly what the agent will route to.
    """
    priority, priority_explicit = _parse_auth_priority()
    priority_set = set(priority)
    resolved = _detect_available_methods(priority)
    resolved_set = set(resolved)

    statuses = [
        AuthMethodStatus(
            method=method,
            kind=_method_kind(method),
            label=_METHOD_LABEL.get(method, method.value),
            env_var=_method_env_var(method),
            configured=_method_is_configured(method),
            in_priority=method in priority_set,
            active=method in resolved_set,
            detail=_method_detail(method, configured=_method_is_configured(method)),
        )
        for method in AuthMethod
    ]
    return AuthInventory(
        statuses=tuple(statuses),
        resolved_chain=tuple(resolved),
        priority_explicit=priority_explicit,
    )


class _ProxiedChatOpenAI(ChatOpenAI):
    """Translate opaque transport/upstream errors into actionable RuntimeError
    messages so LangGraph's serde surfaces something the user can fix instead
    of the generic 'An internal error occurred' wrapper they see in the OSS
    issue tracker.

    Two failure surfaces matter:

      1. **Connection errors** — proxy unreachable. Almost always a Docker
         networking or container-health problem; we point the user at the
         logs.
      2. **Upstream provider errors** — 4xx returned by the actual model
         provider (Anthropic/OpenAI/Ollama/...) and bubbled through LiteLLM.
         These carry a meaningful message but hit the LangGraph runner as
         a generic ``openai.BadRequestError`` whose serialized form gets
         flattened to 'internal error' on the way back to the CLI. We pull
         out the original message and rewrap it.
    """

    def invoke(self, *args, **kwargs):
        try:
            result = super().invoke(*args, **kwargs)
        except Exception as exc:
            _reraise_with_actionable_message(exc, self.model_name)
            raise
        _log_served_model(self.model_name, result)
        return result

    async def ainvoke(self, *args, **kwargs):
        # Resolve the timeout *before* creating the request coroutine. A
        # misconfigured ``DECEPTICON_LLM_TIMEOUT_SECONDS`` raises ValueError;
        # if that were evaluated as the second argument to
        # ``call_with_timeout`` the ``super().ainvoke(...)`` coroutine would
        # already exist and leak un-awaited ("coroutine was never awaited").
        timeout = _resolve_llm_timeout_seconds()
        try:
            result = await call_with_timeout(super().ainvoke(*args, **kwargs), timeout)
        except LLMTimeoutError:
            raise
        except Exception as exc:
            _reraise_with_actionable_message(exc, self.model_name)
            raise
        _log_served_model(self.model_name, result)
        return result


def _log_served_model(requested: str, result: object) -> None:
    """Best-effort attribution log: which provider/model the LiteLLM proxy
    actually routed to. When fallback fires (primary -> fallback) the
    requested model id and the served model id diverge — surfacing the
    delta lets operators trace cost/perf/debug back to the real upstream.

    Never raise from this path: attribution is observability, not control
    flow. Any extraction failure is swallowed silently.
    """
    try:
        metadata = getattr(result, "response_metadata", None) or {}
        served = (
            metadata.get("model_name")
            or metadata.get("model")
            or getattr(result, "model_name", None)
        )
        if served and served != requested:
            log.info("llm.attribution requested=%s served=%s", requested, served)
        elif served:
            log.debug("llm.attribution requested=%s served=%s", requested, served)
    except Exception:
        pass


def _model_drops_temperature(model: str) -> bool:
    """Return True if the LiteLLM model id rejects the ``temperature`` param.

    Anthropic deprecated ``temperature`` for Claude Opus 4.7 — the request
    gets a 400 from the upstream API regardless of the proxy path. Match
    on the Opus 4.x family across every namespace we route through:

      anthropic/claude-opus-4-7
      auth/claude-opus-4-7
      openrouter/anthropic/claude-opus-4-7

    Rather than enumerate paths we look at the model slug suffix, which
    keeps this honest for openrouter-mirrored variants and any future
    Opus 4.x build added to METHOD_MODELS.
    """
    slug = model.rsplit("/", 1)[-1].lower()
    return (
        slug.startswith(("claude-opus-4", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5"))
        or _model_is_kimi_coding(model)
        or _model_uses_responses_api(model)
    )


def _model_uses_responses_api(model: str) -> bool:
    """GPT-6 agent tool calls require the Responses API, not Chat Completions."""
    return model.startswith("openai/gpt-6-") or model.startswith("openai/gpt-6.1-")


def _model_is_kimi_coding(model: str) -> bool:
    """Kimi for Coding models reject any temperature other than exactly 1.

    Verified against all four ids on api.kimi.com/coding (2026-07).
    Dropping the param lets the platform default (=1) apply.
    """
    slug = model.rsplit("/", 1)[-1].lower()
    return slug.startswith("kimi-for-coding") or slug in ("k3", "k3-256k")


def _model_is_deepseek_thinking(model: str) -> bool:
    """Return True for DeepSeek V4 models and legacy deepseek-reasoner.

    DeepSeek V4 (pro **and** flash) plus legacy deepseek-reasoner use
    thinking mode by default and return ``reasoning_content`` in assistant
    messages. The API **requires** ``reasoning_content`` to be passed back
    in subsequent tool turns — omitting it triggers the upstream 400:
        "The reasoning_content in the thinking mode must be passed back to the API."
    DeepSeek's own docs state ``deepseek-reasoner`` is the deprecated alias
    for ``deepseek-v4-flash`` thinking mode, so flash also needs this
    treatment. Closes #201, #220.
    See: https://api-docs.deepseek.com/guides/thinking_mode
    """
    slug = model.rsplit("/", 1)[-1].lower()
    return slug in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner")


def _model_is_nvidia_nim(model: str) -> bool:
    """Return True for any nvidia_nim/* route.

    NVIDIA NIM's OpenAI-compatible endpoint rejects ``messages[].content``
    as a list-of-parts (the OpenAI v1 multimodal shape) with:
        400 invalid_request_error
        loc=('body','messages',0,'content')
        msg="Input should be a valid string"
    LangChain ChatOpenAI serializes structured content (tool results,
    multipart system blocks) as that exact list shape, so every agent
    run on an NIM route 400s on the first turn. Flatten list-of-text
    parts to a single string before the request leaves the proxy.
    """
    return model.lower().startswith("nvidia_nim/")


class _DeepSeekThinkingChatOpenAI(_ProxiedChatOpenAI):
    """ChatOpenAI subclass that preserves DeepSeek ``reasoning_content``.

    DeepSeek V4 Pro's thinking mode returns ``reasoning_content`` alongside
    ``content`` in assistant messages. When tool calls are present, this field
    **must** be passed back in all subsequent API requests. LangChain's default
    message converters silently drop it in both directions:

    1. Response → AIMessage: ``reasoning_content`` is not extracted
    2. AIMessage → request dict: ``reasoning_content`` is not serialized

    This class patches both directions by:
    - Storing ``reasoning_content`` in ``AIMessage.additional_kwargs``
    - Injecting it back into request dicts for assistant messages
    - Passing ``extra_body={"thinking": {"type": "enabled"}}`` and
      ``reasoning_effort="high"`` on every request
    """

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Inject reasoning_content into outbound assistant messages."""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # Inject DeepSeek thinking mode params
        extra_body = payload.get("extra_body") or {}
        extra_body["thinking"] = {"type": "enabled"}
        payload["extra_body"] = extra_body
        payload["reasoning_effort"] = "high"

        # Walk the messages array and inject reasoning_content from
        # additional_kwargs back into assistant message dicts so the
        # DeepSeek API sees them.
        for msg in payload.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            # The source AIMessage stashes reasoning_content in
            # additional_kwargs; _convert_message_to_dict does not
            # serialize it. Find the original AIMessage and inject.
            # We also check if the dict already has it (future-proofing
            # in case LangChain adds native support).
            if "reasoning_content" not in msg:
                # Try to find matching AIMessage from the input
                if isinstance(input_, list):
                    for lc_msg in input_:
                        if isinstance(lc_msg, AIMessage) and lc_msg.additional_kwargs.get(
                            "reasoning_content"
                        ):
                            # Match by content — the dict's content came from this message
                            msg_content = msg.get("content") or ""
                            lc_content = lc_msg.content or ""
                            if str(msg_content) == str(lc_content) or (
                                msg.get("tool_calls") and lc_msg.tool_calls
                            ):
                                msg["reasoning_content"] = lc_msg.additional_kwargs[
                                    "reasoning_content"
                                ]
                                break

        return payload

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        """Wrap _generate to preserve reasoning_content in the response."""
        result = super()._generate(messages, *args, **kwargs)
        # _create_chat_result already handled extraction; this is a no-op safety net.
        return result

    async def _agenerate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        """Wrap _agenerate to preserve reasoning_content in the response."""
        # Resolve the timeout before creating the request coroutine so a
        # misconfigured timeout env raises ValueError without leaving the
        # ``super()._agenerate(...)`` coroutine un-awaited.
        timeout = _resolve_llm_timeout_seconds()
        result = await call_with_timeout(
            super()._agenerate(messages, *args, **kwargs),
            timeout,
        )
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> Any:
        """Intercept streaming chunks to capture ``reasoning_content``.

        DeepSeek sends ``reasoning_content`` inside ``choices[0].delta``
        during streaming.  LangChain's ``_convert_delta_to_message_chunk``
        ignores it, so it never reaches ``AIMessageChunk.additional_kwargs``.

        We call the parent to build the ``ChatGenerationChunk`` normally,
        then fish ``reasoning_content`` out of the raw delta dict and inject
        it into the chunk message's ``additional_kwargs``.  When LangChain
        aggregates chunks via ``AIMessageChunk.__add__``, ``merge_dicts``
        concatenates the string fragments into the full reasoning trace,
        which ``_get_request_payload`` then injects back into the API
        request on subsequent turns.
        """
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None

        # Extract reasoning_content from the raw delta
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if choices:
            delta = choices[0].get("delta") or {}
            rc = delta.get("reasoning_content")
            if rc and isinstance(gen_chunk.message, AIMessage):
                gen_chunk.message.additional_kwargs.setdefault("reasoning_content", "")
                gen_chunk.message.additional_kwargs["reasoning_content"] += rc

        return gen_chunk

    def _create_chat_result(self, response: Any, generation_info: dict | None = None) -> Any:
        """Override to capture ``reasoning_content`` from the response dict.

        ``_create_chat_result`` receives either the raw OpenAI ``ChatCompletion``
        object or its ``.model_dump()`` dict.  Either way, each choice's
        ``message`` dict contains ``reasoning_content`` (the OpenAI SDK v1.x
        preserves it via ``model_extra``).  LangChain's ``_convert_dict_to_message``
        ignores it, so we fish it out of the response dict and inject it into
        the resulting ``AIMessage.additional_kwargs`` after the parent builds
        the ``ChatResult``.
        """
        # Get the response as a dict so we can access reasoning_content
        import openai as _openai

        if isinstance(response, _openai.BaseModel):
            response_dict = response.model_dump(
                exclude={"choices": {"__all__": {"message": {"parsed"}}}}
            )
        elif isinstance(response, dict):
            response_dict = response
        else:
            response_dict = {}

        result = super()._create_chat_result(response, generation_info)

        # Pair up choices with generations and inject reasoning_content
        choices = response_dict.get("choices") or []
        for choice, generation in zip(choices, result.generations):
            msg = getattr(generation, "message", None)
            if not isinstance(msg, AIMessage):
                continue
            if msg.additional_kwargs.get("reasoning_content"):
                continue
            rc = (choice.get("message") or {}).get("reasoning_content")
            if rc:
                msg.additional_kwargs["reasoning_content"] = rc

        return result


class _NvidiaNIMChatOpenAI(_ProxiedChatOpenAI):
    """ChatOpenAI subclass that flattens content to a string for NVIDIA NIM.

    NIM's OpenAI-compat endpoint requires ``messages[].content`` to be a
    plain string. LangChain ChatOpenAI emits the OpenAI v1 multipart shape
    (``[{"type":"text","text":"..."}, ...]``) whenever a message has more
    than one part (tool result + reasoning preamble, multi-block system).
    NIM 400s on that shape. Re-pack each list into the concatenated text
    just before the request leaves the proxy. Image/audio parts are
    preserved untouched — those still get rejected upstream, but the
    error then becomes the upstream feature-gap rather than a contract
    mismatch.
    """

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for msg in payload.get("messages", []):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            text_parts: list[str] = []
            non_text: list[Any] = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") in (None, "text", "input_text", "output_text"):
                        text = part.get("text") or part.get("content") or ""
                        if isinstance(text, str) and text:
                            text_parts.append(text)
                        continue
                    non_text.append(part)
                elif isinstance(part, str):
                    if part:
                        text_parts.append(part)
                else:
                    non_text.append(part)
            if non_text:
                # Leave multimodal parts to the upstream provider's
                # rejection — collapsing them would silently drop data.
                continue
            msg["content"] = "".join(text_parts)
        return payload


# Patterns matching secret-shaped substrings that providers may echo back
# inside HTTP error bodies. ``str(exc)`` for LiteLLM/openai errors is built
# from that body, so it can reflect the Authorization/x-api-key/Bearer
# credential we sent. Each pattern below scrubs one shape before the text is
# interpolated into a user-facing RuntimeError that lands in CLI output/logs.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "Bearer <token>" auth scheme (RFC 6750), case-insensitive.
    (re.compile(r"(?i)\bBearer\s+[\w.\-+/=]+"), "Bearer [REDACTED]"),
    # Provider key prefixes: Anthropic sk-ant-* and OpenAI-style sk-* (any
    # remaining long token after the prefix).
    (re.compile(r"\bsk-(?:ant-)?[\w.\-]{8,}"), "[REDACTED]"),
    # Header/field values for authorization / api_key / x-api-key, whether
    # JSON ("authorization": "...") or kwarg (api_key=...). Keeps the key
    # name so the guidance stays readable; only the value is scrubbed.
    (
        re.compile(
            r"(?i)(['\"]?(?:authorization|x-api-key|api[_-]?key)['\"]?\s*[:=]\s*)"
            r"(['\"]?)[^'\"\s,}]+\2"
        ),
        r"\1\2[REDACTED]\2",
    ),
    # Generic long opaque tokens (>=24 chars of key-ish alphabet) that survive
    # the targeted passes above — e.g. provider keys without an sk- prefix.
    (re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"), "[REDACTED]"),
)

# High-confidence credential shapes used to decide whether an *unclassified*
# provider error would leak a secret if its text were re-raised verbatim.
# Deliberately excludes the broad ``>=24-char`` catch-all from
# ``_SECRET_PATTERNS`` (that one also matches long model ids / hashes and
# would over-trigger), keeping only the unambiguous ``Bearer``/``sk-``/
# ``authorization:`` shapes.
_CREDENTIAL_SHAPE = re.compile(
    r"(?i)\bBearer\s+[\w.\-+/=]+"
    r"|\bsk-(?:ant-)?[\w.\-]{8,}"
    r"|['\"]?(?:authorization|x-api-key|api[_-]?key)['\"]?\s*[:=]\s*['\"]?[^'\"\s,}]+"
)


def _redact_url_credentials(url: str) -> str:
    """Strip ``user:pass@`` userinfo from a URL before logging.

    Operators may configure the proxy URL with embedded credentials
    (``http://user:pass@litellm:4000``); those must not reach INFO logs.
    """
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or "@" not in parts.netloc:
        return url
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))


def _redact_secrets(text: str) -> str:
    """Scrub credential-shaped substrings from upstream error text.

    LiteLLM/openai surface the provider's raw HTTP response body via
    ``str(exc)``; that body can echo the ``Authorization``/``x-api-key``/
    ``Bearer`` value we sent. Redacting here keeps the actionable guidance
    (status code, model id, human hint) intact while preventing the secret
    from leaking into CLI output and logs.
    """
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


ProviderErrorClass = Literal["retryable", "fatal"]

# HTTP status → class. 408/425/429 + 5xx are worth a retry / fallback hop;
# 4xx auth/config (400/401/403/404) won't be fixed by another attempt.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_FATAL_STATUS = frozenset({400, 401, 403, 404})

# Last-resort message scrape when the exception lacks ``status_code`` —
# LiteLLM/openai surface the upstream status only in the body text.
_STATUS_IN_MSG = re.compile(r"(?:error\s*code|status(?:_code)?|http)\D{0,8}(\d{3})", re.I)


def _provider_status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    nested_status = getattr(getattr(exc, "response", None), "status_code", None)
    return nested_status if isinstance(nested_status, int) else None


def _classify_provider_error(exc: BaseException) -> ProviderErrorClass:
    """Tag a provider exception as ``retryable`` or ``fatal``.

    Inspection order (cheapest signal first):
      1. Our own ``LLMTimeoutError`` and httpx transport errors → retryable.
      2. ``status_code`` attribute (openai.APIStatusError shape).
      3. ``response.status_code`` (httpx.HTTPStatusError shape).
      4. Regex over ``str(exc)`` — LiteLLM nests upstream status in the body.

    Default for unrecognised shapes is ``retryable`` so we preserve today's
    behavior (LiteLLM ``num_retries`` keeps spinning). Only flip to ``fatal``
    on a positive 4xx signal.
    """
    if isinstance(exc, LLMTimeoutError):
        return "retryable"
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return "retryable"

    status = _provider_status_code(exc)
    if status is not None:
        if status in _FATAL_STATUS:
            return "fatal"
        if status in _RETRYABLE_STATUS:
            return "retryable"

    match = _STATUS_IN_MSG.search(str(exc))
    if match is not None:
        code = int(match.group(1))
        if code in _FATAL_STATUS:
            return "fatal"
        if code in _RETRYABLE_STATUS:
            return "retryable"

    return "retryable"


def _reraise_if_connection_error(exc: Exception) -> None:
    err_type = type(exc).__name__
    if any(
        kw in err_type.lower() for kw in ("connect", "timeout", "refused", "unreachable")
    ) or any(
        kw in str(exc).lower()
        for kw in ("connection refused", "connect error", "proxy", "unreachable")
    ):
        raise RuntimeError(
            f"LLM proxy unreachable ({err_type}): {_redact_secrets(str(exc))}. "
            f"Check 'decepticon logs litellm' for details."
        ) from exc


def _reraise_with_actionable_message(exc: Exception, model_name: str) -> None:
    """Translate transport + upstream errors into a useful RuntimeError.

    Connection failures still go through ``_reraise_if_connection_error``
    (the original signal). For 4xx errors that LiteLLM forwards from the
    upstream provider, we extract the inner message — LiteLLM nests it
    inside the response payload — and re-raise with a clear "model X
    failed because Y" framing plus a remediation hint.

    Critically, this is the place to disambiguate the four classes the
    OSS user actually sees as 'internal error':

      - 400 BadRequestError (e.g. deprecated param, model_group not found)
      - 401 AuthenticationError (key missing/invalid for the routed provider)
      - 404 NotFoundError (model not registered in litellm.yaml)
      - 429 RateLimitError (provider quota hit)
    """
    _reraise_if_connection_error(exc)

    err_type = type(exc).__name__
    msg = str(exc)
    msg_lower = msg.lower()
    fatal_prefix = (
        "non-retryable provider error: " if _classify_provider_error(exc) == "fatal" else ""
    )
    # Match on the raw text (status codes / keywords are not secret-shaped),
    # but interpolate the scrubbed copy so an echoed credential never reaches
    # the user-facing message — see _redact_secrets.
    safe_msg = _redact_secrets(msg)
    status = _provider_status_code(exc)
    status_match = _STATUS_IN_MSG.search(msg)
    is_bad_request = (
        "badrequest" in err_type.lower()
        or status == 400
        or (status_match is not None and int(status_match.group(1)) == 400)
    )

    if is_bad_request and "invalid model name" in msg_lower:
        raise RuntimeError(
            f"{fatal_prefix}Model '{model_name}' is not available through the LiteLLM "
            f"proxy. Verify its static route in config/litellm.yaml or dynamic environment "
            f"configuration (DECEPTICON_MODEL*, DECEPTICON_LITELLM_MODELS, "
            f"CUSTOM_OPENAI_*, or OLLAMA_MODEL). Restart the proxy, inspect its startup "
            f"logs, and query /v1/models for the models available to your key.\n"
            f"Underlying: {safe_msg}"
        ) from exc

    # LiteLLM puts a recognizable prefix in the inner message when the
    # proxy ran out of fallback options for a model_group — issue #107.
    # Surface this distinctly so users know *why* the request couldn't be
    # retried somewhere else.
    if "no fallback model group found" in msg_lower:
        raise RuntimeError(
            f"Model '{model_name}' failed and no provider fallback was "
            f"available for it. Either configure another auth method in "
            f"DECEPTICON_AUTH_PRIORITY or fix the upstream error.\n"
            f"Underlying: {safe_msg}"
        ) from exc

    if is_bad_request:
        raise RuntimeError(
            f"{fatal_prefix}Model '{model_name}' rejected the request (400). "
            f"This usually means a parameter the model no longer supports "
            f"(e.g. temperature on Claude Opus 4.7). Underlying: {safe_msg}"
        ) from exc

    if (
        "authentication" in err_type.lower()
        or "code: 401" in msg_lower
        or "invalid_api_key" in msg_lower
    ):
        raise RuntimeError(
            f"{fatal_prefix}Model '{model_name}' rejected your credentials (401). "
            f"Check the API key for that provider in ~/.decepticon/.env, "
            f"or run 'decepticon onboard --reset'.\nUnderlying: {safe_msg}"
        ) from exc

    if (
        "permissiondenied" in err_type.lower()
        or "forbidden" in err_type.lower()
        or "code: 403" in msg_lower
    ):
        raise RuntimeError(
            f"{fatal_prefix}Model '{model_name}' refused access (403). The "
            f"provider accepted your credentials but forbids this model "
            f"(region lock, plan tier, or org policy). Verify your account "
            f"can use that model, or route to another method via "
            f"DECEPTICON_AUTH_PRIORITY.\nUnderlying: {safe_msg}"
        ) from exc

    if "ratelimit" in err_type.lower() or "code: 429" in msg_lower:
        raise RuntimeError(
            f"Model '{model_name}' hit the provider's rate limit (429). "
            f"Add another method to DECEPTICON_AUTH_PRIORITY so the agent "
            f"can fall back when this happens.\nUnderlying: {safe_msg}"
        ) from exc

    if "notfound" in err_type.lower() or "code: 404" in msg_lower:
        raise RuntimeError(
            f"{fatal_prefix}Model '{model_name}' is not registered in the LiteLLM proxy "
            f"(404). For local Ollama, set OLLAMA_MODEL to something you "
            f"actually pulled ('ollama list'). For cloud providers, check "
            f"that the model id matches config/litellm.yaml.\n"
            f"Underlying: {safe_msg}"
        ) from exc

    # Final safety net for *unclassified* provider errors. None of the
    # branches above matched, so the caller's trailing ``raise`` would
    # re-raise ``exc`` verbatim. If its text carries a credential-shaped
    # substring (e.g. an echoed Authorization header on a 500/422/region
    # error we don't special-case), re-raising raw would leak the secret
    # into CLI output and logs. Re-wrap with the scrubbed copy. Errors with
    # no credential shape fall through untouched so their original traceback
    # is preserved (see test_unmatched_error_passes_through).
    if _CREDENTIAL_SHAPE.search(msg):
        raise RuntimeError(
            f"{fatal_prefix}Model '{model_name}' failed with an unclassified "
            f"provider error. Underlying: {safe_msg}"
        ) from exc


def _tiers_required(profile: ModelProfile) -> dict[Tier, list[str]]:
    if profile == ModelProfile.MAX:
        return {Tier.HIGH: list(AGENT_TIERS)}
    if profile == ModelProfile.TEST:
        return {Tier.LOW: list(AGENT_TIERS)}
    grouped: dict[Tier, list[str]] = {}
    for role, tier in AGENT_TIERS.items():
        grouped.setdefault(tier, []).append(role)
    return grouped


def _validate_chain_coverage(credentials: Credentials, profile: ModelProfile) -> None:
    """Fail fast when ``credentials`` resolve to an EMPTY chain for any
    tier the factory must serve under ``profile``.

    ``LLMModelMapping.from_credentials_and_profile`` silently skips a role
    whose resolved chain is empty (no configured AuthMethod has a
    ``(method, tier)`` entry in ``METHOD_MODELS``). Downstream this surfaces
    as a confusing ``KeyError`` at ``get_model`` time or a primary-less
    ``ModelFallbackMiddleware([])``. Surface it once, here, with the
    offending tier(s) + the configured inventory so the operator can fix
    config immediately rather than chase a runtime failure.
    """
    empty: list[tuple[Tier, list[str]]] = [
        (tier, roles)
        for tier, roles in _tiers_required(profile).items()
        if not resolve_chain(tier, credentials)
    ]
    if not empty:
        return
    details = "; ".join(
        f"{tier.value} (roles: {', '.join(sorted(roles))})" for tier, roles in empty
    )
    inventory = ", ".join(m.value for m in credentials.methods) or "<none>"
    tier_list = ", ".join(tier.value for tier, _ in empty)
    raise ValueError(
        f"LLMFactory: no model available for tier(s) [{tier_list}] under "
        f"profile {profile.value!r}. Empty chains: {details}. Configured "
        f"credentials: [{inventory}]. Add a credential whose AuthMethod "
        f"has an entry for the listed tier(s) in METHOD_MODELS "
        f"(packages/decepticon-core/decepticon_core/types/llm.py), or "
        f"adjust DECEPTICON_AUTH_PRIORITY / DECEPTICON_MODEL_PROFILE."
    )


class LLMFactory:
    """Creates and caches LangChain ChatModel instances per agent role.

    Routes all models through LiteLLM proxy. Supports primary + fallback
    model resolution via ModelRouter.

    When constructed without an explicit mapping, builds one from the
    user's credentials inventory and the model profile from
    DecepticonConfig (env: ``DECEPTICON_MODEL_PROFILE``).
    """

    def __init__(
        self,
        proxy: ProxyConfig | None = None,
        mapping: LLMModelMapping | None = None,
        credentials: Credentials | None = None,
        profile: ModelProfile | str | None = None,
        apply_role_overrides: bool = True,
    ):
        self._proxy = proxy or self._resolve_proxy_config()
        if mapping is not None:
            self._mapping = mapping
        else:
            creds = credentials if credentials is not None else _resolve_credentials()
            raw_profile = profile if profile is not None else self._resolve_profile()
            resolved_profile = ModelProfile(raw_profile)
            _validate_chain_coverage(creds, resolved_profile)
            self._mapping = LLMModelMapping.from_credentials_and_profile(creds, resolved_profile)
        self._router = ModelRouter(self._mapping)
        self._cache: dict[str, BaseChatModel] = {}
        self._apply_role_overrides = apply_role_overrides

    @staticmethod
    def _compose_assignment(role: str, assignment: ModelAssignment) -> ModelAssignment:
        """Apply a composed per-role model override to ``assignment``.

        Precedence (env > ``PluginBundle.models`` > tier default) is owned
        by ``decepticon.agents.build.resolve_role_model``. The displaced
        tier primary is kept as the head of the fallback chain so a bad
        override still degrades to the vetted default. Applied per get, so
        it also re-tiers custom plugin roles that resolve via a fallback
        role rather than living in ``AGENT_TIERS``. Imported lazily —
        build → factory is a normal edge; the reverse must stay absent.
        """
        from decepticon.agents.build import resolve_role_model

        override = resolve_role_model(role)
        if not override or override == assignment.primary:
            return assignment
        return ModelAssignment(
            primary=override,
            fallbacks=[assignment.primary, *(m for m in assignment.fallbacks if m != override)],
            temperature=assignment.temperature,
        )

    @staticmethod
    def _resolve_proxy_config() -> ProxyConfig:
        """Resolve proxy config from DecepticonConfig (env vars)."""
        from decepticon_core.utils.config import load_config

        config = load_config()
        return ProxyConfig(
            url=config.llm.proxy_url,
            api_key=config.llm.proxy_api_key,
            timeout=config.llm.timeout,
            max_retries=config.llm.max_retries,
        )

    @staticmethod
    def _resolve_profile() -> ModelProfile:
        """Resolve the model profile from DecepticonConfig (env var)."""
        from decepticon_core.utils.config import load_config

        return load_config().model_profile

    @property
    def proxy_url(self) -> str:
        return self._proxy.url

    @property
    def router(self) -> ModelRouter:
        return self._router

    def _resolve_default_role(self, role: str, explicit: str | None) -> str | None:
        """Pick ``default_role`` for ``ModelRouter.get_assignment``.

        Resolution order:
          1. caller-supplied ``explicit`` wins (existing behavior)
          2. otherwise consult ``RoleRegistry`` for an
             ``llm_role_fallback`` registered against ``role``
          3. otherwise None (router will raise if it can't resolve)

        Closes spec §8 gap #5 — plugin-shipped roles (e.g. a downstream
        ``apt``) no longer need every call site to thread
        ``default_role`` through manually; the registration spec
        carries it.
        """
        if explicit is not None:
            return explicit
        spec = RoleRegistry.get(role)
        if spec is None:
            return None
        return spec.llm_role_fallback

    def get_model(self, role: str, *, default_role: str | None = None) -> BaseChatModel:
        """Get the primary ChatModel for a role. Cached per role.

        ``default_role`` lets plugin orchestrators inherit an OSS role's
        assignment when their custom role is not in ``AGENT_TIERS`` —
        e.g. ``LLMFactory().get_model("decepticon-pro", default_role="decepticon")``.

        When the caller leaves ``default_role`` unset, the factory consults
        ``decepticon_core.registry.RoleRegistry`` for the role's registered
        ``llm_role_fallback`` and uses that. This is how plugin-shipped
        roles such as a downstream ``apt`` orchestrator stop needing every call
        site to thread ``default_role="decepticon"`` through manually
        (closes spec §8 gap #5).
        """
        if role in self._cache:
            return self._cache[role]

        default_role = self._resolve_default_role(role, default_role)
        assignment = self._router.get_assignment(role, default_role=default_role)
        if self._apply_role_overrides:
            assignment = self._compose_assignment(role, assignment)
        log.info(
            "Creating LLM for role '%s' → model '%s' via %s",
            role,
            assignment.primary,
            _redact_url_credentials(self._proxy.url),
        )

        model = self._create_chat_model(assignment.primary, assignment.temperature)
        self._cache[role] = model
        return model

    def get_fallback_models(
        self, role: str, *, default_role: str | None = None
    ) -> list[BaseChatModel]:
        """Build the full ordered list of fallback ChatModel instances.

        Each entry mirrors one entry from the agent's credentials chain
        beyond the primary. The agent passes the result via
        ``ModelFallbackMiddleware(*models)``, which tries them in order
        until one succeeds. ``default_role`` works as in ``get_model``.
        """
        default_role = self._resolve_default_role(role, default_role)
        assignment = self._compose_assignment(
            role, self._router.get_assignment(role, default_role=default_role)
        )
        if not assignment.fallbacks:
            return []

        log.info(
            "Creating %d fallback LLM(s) for role '%s' → %s",
            len(assignment.fallbacks),
            role,
            assignment.fallbacks,
        )
        return [
            self._create_chat_model(model, assignment.temperature) for model in assignment.fallbacks
        ]

    def _create_chat_model(self, model: str, temperature: float) -> BaseChatModel:
        """Create a proxied ChatOpenAI instance routed through LiteLLM proxy.

        Claude Opus 4.7+ rejects ``temperature`` with a 400 invalid_request
        error regardless of how the request is routed — the model is wired
        to extended-thinking defaults that don't accept the parameter.
        Match every Opus 4.7 surface (anthropic/, auth/, openrouter/...)
        and drop the field from the OpenAI request payload via
        ``disabled_params``. Just omitting our own kwarg is not enough —
        ChatOpenAI synthesizes a default temperature when the field is
        unset, so the param still goes on the wire.

        The LiteLLM proxy also drops temperature for opus entries via
        ``additional_drop_params`` (config/litellm.yaml) — that's the
        belt-and-suspenders for any future client that bypasses this
        factory.
        """
        disable_streaming = _resolve_disable_streaming()
        extra_headers = _resolve_extra_headers()
        kwargs: dict[str, Any] = {
            "model": model,
            "base_url": self._proxy.url,
            "api_key": SecretStr(self._proxy.api_key),
            "timeout": self._proxy.timeout,
            "max_retries": self._proxy.max_retries,
            # Per-model cap so large single-call deliverables (finding reports,
            # recon target models) don't truncate at the proxy's 4096 default.
            "max_tokens": _resolve_max_tokens(model),
            # Stream the completion even when the agent calls ``invoke()``.
            # LangChain only emits ``on_llm_new_token`` when the underlying
            # request streams, and LangGraph's "messages" stream mode is built
            # on those callbacks — without this, every consumer (CLI renderer,
            # web run console) receives each agent message as one finished
            # block instead of token deltas.
            "streaming": not disable_streaming,
            # Streaming responses omit usage unless it is explicitly requested,
            # and the measurement/cost path reads ``usage_metadata`` off the
            # aggregated message (middleware/event_logging.py). Enabling this
            # alongside ``streaming`` keeps per-run spend + prompt-cache
            # accounting intact.
            "stream_usage": not disable_streaming,
        }
        if model.lower().startswith("ollama_chat/"):
            # LiteLLM <= 1.89 loses the ``tool_calls`` finish reason when
            # Ollama emits tool calls before its final streaming chunk. Buffer
            # only tool-bound requests; ordinary text responses still stream.
            kwargs["disable_streaming"] = "tool_calling"
        if extra_headers is not None:
            kwargs["default_headers"] = extra_headers
        if _model_uses_responses_api(model):
            kwargs["use_responses_api"] = True
        if _model_drops_temperature(model):
            kwargs["disabled_params"] = {"temperature": None}
        elif _model_is_deepseek_thinking(model):
            # DeepSeek V4 Pro thinking mode rejects temperature.
            kwargs["disabled_params"] = {"temperature": None}
        else:
            kwargs["temperature"] = temperature
        if _model_is_deepseek_thinking(model):
            return _DeepSeekThinkingChatOpenAI(**kwargs)
        if _model_is_nvidia_nim(model):
            return _NvidiaNIMChatOpenAI(**kwargs)
        return _ProxiedChatOpenAI(**kwargs)

    async def health_check(self) -> bool:
        """Check if the LiteLLM proxy is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._proxy.url}/health")
                return resp.status_code == 200
        except Exception:
            return False


def create_llm(
    role: object,
    config: object | None = None,
    profile: ModelProfile | str | None = None,
) -> BaseChatModel:
    """Convenience function — creates primary model for a role.

    Backward-compatible wrapper around LLMFactory.
    The `config` parameter is accepted but ignored (kept for call-site compat).
    Pass `profile` to override the config-level model profile.
    """
    _ = config
    factory = LLMFactory(profile=profile)
    role_str = str(role.value) if isinstance(role, Enum) else str(role)
    return factory.get_model(role_str)
