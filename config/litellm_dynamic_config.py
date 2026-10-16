"""Dynamic LiteLLM config helpers for user-supplied model IDs.

The checked-in ``config/litellm.yaml`` contains the default Decepticon routes.
Operators can additionally set ``DECEPTICON_MODEL`` / per-role overrides to any
LiteLLM model string (for example ``openrouter/anthropic/claude-3.7-sonnet`` or
``ollama_chat/qwen3-coder:30b``).  This module appends only those requested routes
at container startup so the proxy accepts the same model names the agents use.

For Ollama only the ``ollama_chat/`` provider is accepted — the legacy
``ollama/`` (``/api/generate``) lacks tool calling per LiteLLM's own
``supports_function_calling`` check, and Decepticon agents always emit tool calls.

No secret values are read or logged here; generated routes reference environment
variables using LiteLLM's ``os.environ/NAME`` syntax.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

import yaml

_CURRENT_MODEL_CATALOG = Path(__file__).with_name("litellm_prod_catalog.json")


def _configured_credential(value: str | None) -> bool:
    candidate = (value or "").strip()
    return bool(candidate and not candidate.startswith(("your-", "YOUR-")))


def current_model_routes(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    source = env if env is not None else os.environ
    catalog = json.loads(_CURRENT_MODEL_CATALOG.read_text(encoding="utf-8"))
    enabled: list[dict[str, Any]] = []
    for route in catalog["routes"]:
        params = route["litellm_params"]
        required = [
            value.removeprefix("os.environ/")
            for field in ("api_key", "api_base")
            if isinstance(value := params.get(field), str) and value.startswith("os.environ/")
        ]
        if all(_configured_credential(source.get(name)) for name in required):
            enabled.append(copy.deepcopy(route))
    return enabled


# Common LiteLLM provider prefix -> environment variable containing the API key.
# Unknown providers fall back to ``<PROVIDER>_API_KEY`` after normalization, which
# covers most LiteLLM providers without requiring a code change.
PROVIDER_API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_API_KEY",
    # NOTE: bedrock (SigV4) and vertex_ai (Google ADC) are key-less — they live
    # in _NO_API_KEY_PROVIDERS, so build_model_entry never calls _resolve_key_env
    # for them. They are still admitted to ALLOWED_DYNAMIC_PROVIDERS via the
    # _NO_API_KEY_PROVIDERS union below. A bearer-key entry here would be dead and
    # misleading (no AWS_ACCESS_KEY_ID / GOOGLE_APPLICATION_CREDENTIALS bearer
    # path exists), so they are intentionally omitted from this map.
    "gemini": "GOOGLE_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "cohere_chat": "COHERE_API_KEY",
    "together": "TOGETHER_API_KEY",
    "together_ai": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "fireworks_ai": "FIREWORKS_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "nvidia_nim": "NVIDIA_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "minimax": "MINIMAX_API_KEY",
    # New providers from the OpenClaude migration. LiteLLM ships native
    # support for each — only the env-var mapping needs to be wired here.
    "moonshot": "MOONSHOT_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    # LiteLLM's GitHub Models provider reads GITHUB_API_KEY (not the
    # GitHub-conventional GITHUB_TOKEN). Onboard writes both to the
    # .env so docs that reference GITHUB_TOKEN keep working.
    "github": "GITHUB_API_KEY",
    "lm_studio": "LMSTUDIO_API_KEY",  # LM Studio accepts any string; keep
    # symbolic so validate_model_name() lets the route through.
    "zai": "ZAI_API_KEY",
    # Cerebras Inference — native LiteLLM ``cerebras/`` provider,
    # OpenAI-compatible at api.cerebras.ai/v1.
    "cerebras": "CEREBRAS_API_KEY",
    # kimi/ is an OpenAI-compat gateway now (OPENAI_COMPAT_GATEWAYS).
    # Alias kept so older configs still resolve a key env.
    "kimi": "KIMI_API_KEY",
    # Xiaomi MiMo Open Platform — OpenAI-compatible (/v1/chat/completions).
    # No native LiteLLM provider yet, so routes are registered under the
    # ``openai/`` provider with an api_base override; this entry lets
    # operators set ``DECEPTICON_LITELLM_MODELS=xiaomi_mimo/<id>`` and
    # have validate_model_name() accept it — the actual route is built
    # by build_model_entry() below.
    "xiaomi_mimo": "XIAOMI_MIMO_API_KEY",
    # ── Full LiteLLM provider catalog (v1.89.0) ──────────────────────────
    # Every native provider that authenticates via a single API-key env var,
    # using the source-verified env name (litellm validate_environment /
    # get_secret_str), not the derived ``<PROVIDER>_API_KEY`` guess. Keys are
    # the ``_provider_prefix``-normalized form (hyphens → underscores), e.g.
    # ``nano-gpt`` → ``nano_gpt``, ``text-completion-openai`` →
    # ``text_completion_openai``. Providers with multiple accepted key aliases
    # live in PROVIDER_KEY_ENV_ALIASES; key-less providers (SigV4 / ADC / OCI
    # signing / OAuth exchange / local no-auth) live in _NO_API_KEY_PROVIDERS.
    "text_completion_openai": "OPENAI_API_KEY",
    "codestral": "CODESTRAL_API_KEY",
    "text_completion_codestral": "CODESTRAL_API_KEY",
    "azure_ai": "AZURE_AI_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "databricks": "DATABRICKS_API_KEY",
    "watsonx": "WATSONX_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "anyscale": "ANYSCALE_API_KEY",
    "ai21": "AI21_API_KEY",
    "ai21_chat": "AI21_API_KEY",
    "nlp_cloud": "NLP_CLOUD_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
    "baseten": "BASETEN_API_KEY",
    "snowflake": "SNOWFLAKE_JWT",
    "novita": "NOVITA_API_KEY",
    "hyperbolic": "HYPERBOLIC_API_KEY",
    "lambda_ai": "LAMBDA_API_KEY",
    "nebius": "NEBIUS_API_KEY",
    "galadriel": "GALADRIEL_API_KEY",
    "gradient_ai": "GRADIENT_AI_API_KEY",
    "predibase": "PREDIBASE_API_KEY",
    "clarifai": "CLARIFAI_API_KEY",
    "aleph_alpha": "ALEPH_ALPHA_API_KEY",
    "maritalk": "MARITALK_API_KEY",
    "empower": "EMPOWER_API_KEY",
    "meta_llama": "LLAMA_API_KEY",
    "nscale": "NSCALE_API_KEY",
    "v0": "V0_API_KEY",
    "morph": "MORPH_API_KEY",
    "inception": "INCEPTION_API_KEY",
    "litellm_proxy": "LITELLM_PROXY_API_KEY",
    "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
    "wandb": "WANDB_API_KEY",
    "aiml": "AIML_API_KEY",
    "heroku": "HEROKU_API_KEY",
    "ovhcloud": "OVHCLOUD_API_KEY",
    "scaleway": "SCW_SECRET_KEY",
    "compactifai": "COMPACTIFAI_API_KEY",
    "publicai": "PUBLICAI_API_KEY",
    "apertis": "STIMA_API_KEY",
    "nano_gpt": "NANOGPT_API_KEY",
    "poe": "POE_API_KEY",
    "chutes": "CHUTES_API_KEY",
    "parasail": "PARASAIL_API_KEY",
    "tensormesh": "TENSORMESH_INFERENCE_API_KEY",
    "infinity": "INFINITY_API_KEY",
    "datarobot": "DATAROBOT_API_TOKEN",
    "manus": "MANUS_API_KEY",
    # Audio / image / video providers (single bearer key).
    "deepgram": "DEEPGRAM_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "assemblyai": "ASSEMBLYAI_API_KEY",
    "recraft": "RECRAFT_API_KEY",
    "runwayml": "RUNWAYML_API_KEY",
    "stability": "STABILITY_API_KEY",
    "fal_ai": "FAL_AI_API_KEY",
    "topaz": "TOPAZ_API_KEY",
    "bedrock_mantle": "BEDROCK_MANTLE_API_KEY",
    "amazon_nova": "AMAZON_NOVA_API_KEY",
    "soniox": "SONIOX_API_KEY",
    # Local / self-hosted OpenAI-compatible servers: key (optional, any
    # string for local) + a required api_base from PROVIDER_EXTRA_PARAMS.
    "hosted_vllm": "HOSTED_VLLM_API_KEY",
    "llamafile": "LLAMAFILE_API_KEY",
    "xinference": "XINFERENCE_API_KEY",
    "lemonade": "LEMONADE_API_KEY",
    "docker_model_runner": "DOCKER_MODEL_RUNNER_API_KEY",
    "ragflow": "RAGFLOW_API_KEY",
    "openai_like": "OPENAI_LIKE_API_KEY",
}

# OpenAI-compatible gateways / aggregators with no native LiteLLM provider
# (oh-my-pi parity). Each is reached through LiteLLM's ``openai/`` provider
# with an explicit api_base override — identical to the xiaomi_mimo / custom
# path but table-driven so a batch of gateways shares one code path. The
# model alias keeps the gateway prefix (``opencode/claude-opus-4-6``) so two
# gateways exposing the same upstream slug never collide in the model_list;
# ``build_model_entry`` rewrites the prefix to ``openai/`` and pins the base.
#
# Mapping: provider_prefix -> (api_base, api_key_env). ``api_base`` is a
# literal URL for fixed-endpoint gateways, or an ``os.environ/<NAME>`` ref
# for per-account gateways (Cloudflare) whose URL the operator supplies.
# ``api_key_env`` is the env var holding the bearer token.
OPENAI_COMPAT_GATEWAYS: dict[str, tuple[str, str]] = {
    "opencode": ("https://opencode.ai/zen/v1", "OPENCODE_API_KEY"),
    "vercel": ("https://ai-gateway.vercel.sh/v1", "VERCEL_AI_GATEWAY_API_KEY"),
    "hf": ("https://router.huggingface.co/v1", "HF_TOKEN"),
    "venice": ("https://api.venice.ai/api/v1", "VENICE_API_KEY"),
    "nanogpt": ("https://nano-gpt.com/api/v1", "NANOGPT_API_KEY"),
    "synthetic": ("https://api.synthetic.new/openai/v1", "SYNTHETIC_API_KEY"),
    "zenmux": ("https://zenmux.ai/api/v1", "ZENMUX_API_KEY"),
    "qianfan": ("https://qianfan.baidubce.com/v2", "QIANFAN_API_KEY"),
    # Kimi for Coding (api.kimi.com/coding). Separate platform from the
    # legacy Moonshot API: own endpoint, keys and model ids. All models
    # reject temperature != 1, handled in llm/factory.py.
    "kimi": ("https://api.kimi.com/coding/v1", "KIMI_API_KEY"),
    # Per-account base URL: the operator sets it to their Cloudflare AI
    # Gateway OpenAI-compat endpoint (``…/compat``). Resolved by LiteLLM at
    # request time, so an unset base only fails the call (with a clear 404),
    # never proxy startup.
    "cfgateway": ("os.environ/CLOUDFLARE_AI_GATEWAY_API_BASE", "CLOUDFLARE_AI_GATEWAY_API_KEY"),
    # Cheaper Inference (api.cheaperinference.com). One key gives access to
    # models from several labs; fixed OpenAI-compatible endpoint.
    "cheaperinference": ("https://api.cheaperinference.com/v1", "CHEAPER_INFERENCE_API_KEY"),
    # Generic opt-in OpenAI-compatible gateway. BOTH api_base and api_key
    # come from the environment — no vendor URL is hardcoded. Unset env
    # vars leave this gateway disabled (DEFAULT OFF). Operators point
    # OPENAI_COMPAT_GATEWAY_API_BASE at any OpenAI-compatible /v1 endpoint
    # (self-hosted proxy, third-party aggregator, etc.). See #989.
    "compat": (
        "os.environ/OPENAI_COMPAT_GATEWAY_API_BASE",
        "OPENAI_COMPAT_GATEWAY_API_KEY",
    ),
}

# Providers that accept SEVERAL key env-var names. Resolved at config-write
# time, preferring the first var actually set in the environment (mirrors the
# ollama_cloud OLLAMA_CLOUD_API_KEY → OLLAMA_API_KEY fallback). The first name
# is the canonical fallback emitted when none are set, so the generated route
# references the documented var and fails with a clear 401 at request time
# rather than crashing proxy startup. Source-verified against litellm's
# get_secret_str() alias chains (v1.89.0).
PROVIDER_KEY_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "together_ai": (
        "TOGETHERAI_API_KEY",
        "TOGETHER_API_KEY",
        "TOGETHER_AI_API_KEY",
        "TOGETHER_AI_TOKEN",
    ),
    "fireworks_ai": (
        "FIREWORKS_AI_API_KEY",
        "FIREWORKS_API_KEY",
        "FIREWORKSAI_API_KEY",
        "FIREWORKS_AI_TOKEN",
    ),
    "perplexity": ("PERPLEXITYAI_API_KEY", "PERPLEXITY_API_KEY"),
    "cohere": ("COHERE_API_KEY", "CO_API_KEY"),
    "cohere_chat": ("COHERE_API_KEY", "CO_API_KEY"),
    "friendliai": ("FRIENDLIAI_API_KEY", "FRIENDLI_TOKEN"),
    "huggingface": ("HUGGINGFACE_API_KEY", "HF_TOKEN"),
    "openrouter": ("OPENROUTER_API_KEY", "OR_API_KEY"),
    "voyage": ("VOYAGE_API_KEY", "VOYAGE_AI_API_KEY", "VOYAGE_AI_TOKEN"),
    "jina_ai": ("JINA_AI_API_KEY", "JINA_AI_TOKEN"),
    "volcengine": ("VOLCENGINE_API_KEY", "ARK_API_KEY"),
    "featherless_ai": ("FEATHERLESS_AI_API_KEY", "FEATHERLESS_API_KEY"),
    "black_forest_labs": ("BFL_API_KEY", "BLACK_FOREST_LABS_API_KEY"),
    "cometapi": ("COMETAPI_API_KEY", "COMETAPI_KEY"),
    "nvidia_riva": ("NVIDIA_RIVA_API_KEY", "NVIDIA_NIM_API_KEY"),
}

# Providers needing more than an api_key: extra litellm_params merged into the
# route (api_base / api_version / project / tenant / account). Values are
# ``os.environ/<NAME>`` refs resolved by LiteLLM at request time, so an unset
# var only fails the call with a clear message — never proxy startup. Modeled
# on the existing vertex_ai (project+location) and azure (base+version)
# branches, now table-driven instead of an elif chain.
PROVIDER_EXTRA_PARAMS: dict[str, dict[str, str]] = {
    "azure": {
        "api_base": "os.environ/AZURE_API_BASE",
        "api_version": "os.environ/AZURE_API_VERSION",
    },
    "azure_ai": {"api_base": "os.environ/AZURE_AI_API_BASE"},
    "vertex_ai": {
        "vertex_project": "os.environ/VERTEXAI_PROJECT",
        "vertex_location": "os.environ/VERTEXAI_LOCATION",
    },
    "databricks": {"api_base": "os.environ/DATABRICKS_API_BASE"},
    "watsonx": {
        "api_base": "os.environ/WATSONX_URL",
        "project_id": "os.environ/WATSONX_PROJECT_ID",
    },
    "predibase": {"tenant_id": "os.environ/PREDIBASE_TENANT_ID"},
    "snowflake": {"account_id": "os.environ/SNOWFLAKE_ACCOUNT_ID"},
    "litellm_proxy": {"api_base": "os.environ/LITELLM_PROXY_API_BASE"},
    "heroku": {"api_base": "os.environ/HEROKU_API_BASE"},
    # Self-hosted OpenAI-compatible servers reached by a required base URL.
    "hosted_vllm": {"api_base": "os.environ/HOSTED_VLLM_API_BASE"},
    "llamafile": {"api_base": "os.environ/LLAMAFILE_API_BASE"},
    "xinference": {"api_base": "os.environ/XINFERENCE_API_BASE"},
    "lemonade": {"api_base": "os.environ/LEMONADE_API_BASE"},
    "docker_model_runner": {"api_base": "os.environ/DOCKER_MODEL_RUNNER_API_BASE"},
    "ragflow": {"api_base": "os.environ/RAGFLOW_API_BASE"},
    "openai_like": {"api_base": "os.environ/OPENAI_LIKE_API_BASE"},
    "vllm": {"api_base": "os.environ/VLLM_API_BASE"},
}

# Providers that DON'T use an Authorization-bearer api_key: AWS SigV4
# (bedrock/sagemaker), Google ADC (vertex_ai), OCI request signing, SAP /
# GigaChat OAuth token exchange, and local no-auth servers (petals / triton /
# oobabooga / vllm). LiteLLM resolves their credentials from their own env
# vars; emitting ``api_key=os.environ/<X>_API_KEY`` here would inject a bogus
# empty bearer. ``_provider_prefix``-normalized form.
_NO_API_KEY_PROVIDERS = frozenset(
    {
        "bedrock",
        "sagemaker",
        "sagemaker_chat",
        "vertex_ai",
        "oci",
        "sap",
        "gigachat",
        "petals",
        "triton",
        "oobabooga",
        "vllm",
    }
)

# OAuth device-flow providers that cannot be registered as API-key routes.
_OAUTH_REJECTED_PROVIDERS = frozenset({"github_copilot"})

ALLOWED_DYNAMIC_PROVIDERS = frozenset(
    {
        *PROVIDER_API_KEY_ENV,
        # ``ollama_chat`` (LiteLLM /api/chat) is the only Ollama provider
        # accepted — the legacy ``ollama`` (/api/generate) lacks tool
        # calling and is rejected by validate_model_name() with a
        # remediation hint, before reaching this set.
        "ollama_chat",
        # ``ollama_cloud`` — same ``/api/chat`` tool-calling endpoint but
        # routed through OLLAMA_CLOUD_API_BASE with OLLAMA_CLOUD_API_KEY.
        "ollama_cloud",
        # ``auth/`` is listed but rejected by validate() — kept here so
        # the unrecognized-provider error doesn't fire first and
        # confuse the user with a misleading "use custom/<model>" hint.
        "auth",
        "gemini_sub",
        "copilot",
        "grok_sub",
        "pplx_sub",
        "custom",
        "llamacpp",
    }
)
# ``_provider_prefix`` normalizes ``vertex-ai`` → ``vertex_ai`` already,
# but the bare model id ``vertex_ai/<m>`` carries the underscore form
# directly. Keep a defensive alias entry — the spread above only covers
# what's in PROVIDER_API_KEY_ENV with the same casing.
ALLOWED_DYNAMIC_PROVIDERS = frozenset(ALLOWED_DYNAMIC_PROVIDERS | {"vertex_ai"})
# OpenAI-compatible gateway prefixes (opencode, vercel, hf, …) are not in
# PROVIDER_API_KEY_ENV — their api_base + key come from OPENAI_COMPAT_GATEWAYS
# and build_model_entry rewrites them to ``openai/`` — so register them
# explicitly or validate_model_name would reject the alias as an unknown
# provider before the gateway branch runs.
ALLOWED_DYNAMIC_PROVIDERS = frozenset(ALLOWED_DYNAMIC_PROVIDERS | set(OPENAI_COMPAT_GATEWAYS))
# Alias-key, extra-param, and no-key providers may not all appear in
# PROVIDER_API_KEY_ENV — union them so every catalog prefix validates and the
# dynamic API-key path stays the single source of truth for "is this a known
# provider".
ALLOWED_DYNAMIC_PROVIDERS = frozenset(
    ALLOWED_DYNAMIC_PROVIDERS
    | set(PROVIDER_KEY_ENV_ALIASES)
    | set(PROVIDER_EXTRA_PARAMS)
    | _NO_API_KEY_PROVIDERS
)

# Environment variables that are model-selection controls, not model names.
_MODEL_CONTROL_SUFFIXES = (
    "PROFILE",
    "PROVIDER",
    "TEMPERATURE",
    "MAX_TOKENS",
)


def _clean_model(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.lower() in {"", "none", "null", "-"}:
        return None
    return cleaned


def _looks_like_model_env_var(name: str) -> bool:
    if name in {"DECEPTICON_MODEL", "DECEPTICON_MODEL_FALLBACK"}:
        return True
    if not name.startswith("DECEPTICON_MODEL_"):
        return False
    suffix = name.removeprefix("DECEPTICON_MODEL_")
    return not suffix.endswith(_MODEL_CONTROL_SUFFIXES)


def _extra_models_from_env(value: str | None) -> set[str]:
    """Parse optional comma-separated or JSON-list extra model IDs."""
    cleaned = _clean_model(value)
    if cleaned is None:
        return set()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return {model for item in parsed if (model := _clean_model(str(item)))}

    return {model for part in cleaned.split(",") if (model := _clean_model(part))}


def _ollama_model_from_env(source: Mapping[str, str]) -> str | None:
    """Derive ``ollama_chat/<model>`` from OLLAMA_API_BASE / OLLAMA_MODEL.

    Uses ``ollama_chat`` (not legacy ``ollama``) so /api/chat with
    tool calling is hit. Defaults to ``llama3.2`` when only the base
    URL is set, matching the agent factory.

    A user value is treated as already-qualified only when it starts
    with an Ollama provider prefix; a bare slash is not enough,
    because Ollama tags can contain slashes (HF-hosted GGUFs like
    ``hf.co/<author>/<model>:<quant>``).
    """
    base = _clean_model(source.get("OLLAMA_API_BASE"))
    model = _clean_model(source.get("OLLAMA_MODEL"))
    if base is None and model is None:
        return None
    if model is None:
        model = "llama3.2"
    lower = model.lower()
    if lower.startswith("ollama_chat/") or lower.startswith("ollama/"):
        # Pass legacy ``ollama/`` through verbatim — validate_model_name()
        # rejects it with a remediation hint pointing at ``ollama_chat/``.
        # Auto-rewriting would hide the user's mistake and leave a stale
        # ``OLLAMA_MODEL`` line in their .env disagreeing with the proxy.
        return model
    return f"ollama_chat/{model}"


def _vllm_model_from_env(source: Mapping[str, str]) -> str | None:
    """Derive ``hosted_vllm/<model>`` from VLLM_API_BASE / VLLM_MODEL.

    The ``hosted_vllm`` provider already works through the generic
    ``DECEPTICON_MODEL_<ROLE>=hosted_vllm/<model>`` path, but that requires
    knowing the prefix exists. This mirrors the ``OLLAMA_MODEL`` shortcut so
    self-hosting a model is symmetric with running one under Ollama: point at
    the server, name the model, done.

    The model must match the server's ``--served-model-name``, not the
    HuggingFace repo — vLLM answers on the served name only, and a mismatch
    surfaces as a 404 that reads like a routing bug rather than a typo.

    ``vllm/`` is passed through untouched so validate_model_name() can reject
    it with a hint. It is LiteLLM's deprecated local-SDK provider, not the
    hosted server, and silently rewriting it would leave a ``VLLM_MODEL`` line
    that disagrees with the route the proxy actually registered.
    """
    base = _clean_model(source.get("VLLM_API_BASE")) or _clean_model(
        source.get("HOSTED_VLLM_API_BASE")
    )
    model = _clean_model(source.get("VLLM_MODEL")) or _clean_model(source.get("HOSTED_VLLM_MODEL"))
    if base is None or model is None:
        # Unlike Ollama there is no sensible default model: a vLLM server
        # serves exactly what it was started with. Registering a guess would
        # produce a route that 404s on every call.
        return None
    lower = model.lower()
    if lower.startswith("hosted_vllm/") or lower.startswith("vllm/"):
        return model
    return f"hosted_vllm/{model}"


def _compat_gateway_models_from_env(source: Mapping[str, str]) -> set[str]:
    """Derive ``compat/<model>`` aliases when the generic gateway is wired.

    Requires ``OPENAI_COMPAT_GATEWAY_API_BASE`` + ``OPENAI_COMPAT_GATEWAY_API_KEY``
    (real key) plus at least one of ``OPENAI_COMPAT_GATEWAY_MODEL`` /
    ``OPENAI_COMPAT_GATEWAY_MODEL_{HIGH,MID,LOW}``. DEFAULT OFF — unset
    env registers nothing. Mirrors the OLLAMA_MODEL / VLLM_MODEL shortcuts
    so a key-only+base deployment gets proxy routes without DECEPTICON_MODEL*.
    """
    base = _clean_model(source.get("OPENAI_COMPAT_GATEWAY_API_BASE"))
    key = (source.get("OPENAI_COMPAT_GATEWAY_API_KEY") or "").strip()
    if not base or not key:
        return set()
    # Reject obvious launcher placeholders so a stock .env does not opt in.
    lower = key.lower()
    if lower.startswith("your-") or lower.endswith("-key-here") or len(key) < 24:
        return set()
    models: set[str] = set()
    single = _clean_model(source.get("OPENAI_COMPAT_GATEWAY_MODEL"))
    for tier_env in (
        "OPENAI_COMPAT_GATEWAY_MODEL_HIGH",
        "OPENAI_COMPAT_GATEWAY_MODEL_MID",
        "OPENAI_COMPAT_GATEWAY_MODEL_LOW",
    ):
        m = _clean_model(source.get(tier_env)) or single
        if m is None:
            continue
        if m.lower().startswith("compat/"):
            models.add(m)
        else:
            models.add(f"compat/{m}")
    return models


def collect_requested_models(env: Mapping[str, str] | None = None) -> set[str]:
    """Collect model IDs requested through DECEPTICON_MODEL* env vars.

    Also picks up the OSS-friendly ``OLLAMA_MODEL`` shortcut so a user
    can pull any local model and just point the launcher at it without
    learning the LiteLLM model-id syntax.
    """
    source = env if env is not None else os.environ
    models: set[str] = set()

    for name, value in source.items():
        if not _looks_like_model_env_var(name):
            continue
        model = _clean_model(value)
        if model is not None:
            models.add(model)

    models.update(_extra_models_from_env(source.get("DECEPTICON_LITELLM_MODELS")))

    ollama_model = _ollama_model_from_env(source)
    if ollama_model is not None:
        models.add(ollama_model)

    vllm_model = _vllm_model_from_env(source)
    if vllm_model is not None:
        models.add(vllm_model)

    models.update(_compat_gateway_models_from_env(source))

    return models


def _provider_prefix(model_name: str) -> str:
    return model_name.split("/", 1)[0].lower().replace("-", "_")


_SUBSCRIPTION_PROVIDER_PREFIXES = frozenset(
    {
        "auth",  # auth/claude-*, auth/gpt-* — Claude Code OAuth + Codex ChatGPT
        "gemini_sub",  # gemini-sub/* — Gemini Advanced subscription
        "copilot",  # copilot/* — Copilot Pro subscription
        "grok_sub",  # grok-sub/* — SuperGrok subscription
        "pplx_sub",  # pplx-sub/* — Perplexity Pro subscription
    }
)


def validate_model_name(model_name: str) -> None:
    """Validate user-supplied dynamic model IDs before registering routes.

    Rejects subscription / OAuth provider prefixes (``auth/*``, ``gemini-sub/*``,
    ``copilot/*``, ``grok-sub/*``, ``pplx-sub/*``) for the API-key registration
    path — those routes are added by ``_inject_subscription_routes`` when the
    matching ``DECEPTICON_AUTH_*`` flag is set, with custom-provider dispatch
    in ``litellm_startup.py``. Trying to register them as API-key routes here
    would produce a phantom ``<PROVIDER>_API_KEY`` env-var lookup that never
    resolves.

    ``merge_dynamic_models`` skips this validation when the model is already
    present in the model_list, so a user setting
    ``DECEPTICON_MODEL=auth/gpt-6-luna`` plus ``DECEPTICON_AUTH_CHATGPT=true``
    is fine — the subscription path injected the route first and the requested
    model is satisfied.
    """
    if "/" not in model_name:
        raise ValueError(f"model {model_name!r} must use LiteLLM provider/model format")
    provider = _provider_prefix(model_name)
    if provider in _SUBSCRIPTION_PROVIDER_PREFIXES:
        raise ValueError(
            f"{provider}/* routes are not allowed as dynamic API-key model "
            f"routes. Enable the matching subscription via "
            f"DECEPTICON_AUTH_<provider>=true so the route is registered "
            f"through litellm_startup.py's custom_provider_map instead."
        )
    if provider in _OAUTH_REJECTED_PROVIDERS:
        raise ValueError(
            f"{provider}/* uses interactive OAuth device-flow auth, not an "
            "API-key env var, so it cannot be registered as a dynamic model "
            "route. It is not supported through DECEPTICON_LITELLM_MODELS."
        )
    if provider == "ollama":
        # Legacy ``ollama/`` (/api/generate) lacks tool calling — fail
        # closed since Decepticon agents always emit tool calls.
        slug = model_name.split("/", 1)[1]
        raise ValueError(
            f"model {model_name!r} uses the legacy ollama/ provider, which "
            "routes to /api/generate and does not support tool/function "
            "calling. Decepticon agents always emit tool calls — use "
            f"ollama_chat/{slug} (routes to /api/chat) instead."
        )
    if provider not in ALLOWED_DYNAMIC_PROVIDERS:
        raise ValueError(
            f"unsupported model provider {provider!r} for {model_name!r}; "
            f"{len(ALLOWED_DYNAMIC_PROVIDERS)} providers are supported "
            "(see the DECEPTICON_LITELLM_MODELS docs for the full list). "
            "Use custom/<model> with CUSTOM_OPENAI_API_BASE for an "
            "OpenAI-compatible gateway that isn't listed."
        )


def _derived_api_key_env(provider: str) -> str:
    return f"{provider.upper()}_API_KEY"


def _resolve_key_env(provider: str) -> str:
    """Pick the api_key env var name for ``provider``.

    Multi-alias providers resolve at write time to the first candidate var
    actually set, falling back to the canonical (first-listed) name when none
    are set. Otherwise use the explicit PROVIDER_API_KEY_ENV mapping, then the
    derived ``<PROVIDER>_API_KEY`` guess for any provider not in either table.
    """
    aliases = PROVIDER_KEY_ENV_ALIASES.get(provider)
    if aliases:
        for name in aliases:
            if os.environ.get(name, "").strip():
                return name
        return aliases[0]
    return PROVIDER_API_KEY_ENV.get(provider, _derived_api_key_env(provider))


def build_model_entry(model_name: str) -> dict[str, Any]:
    """Build a LiteLLM ``model_list`` entry for a requested model ID.

    The generated route keeps ``model_name`` identical to the string used by the
    agent.  That makes per-role overrides transparent: if an agent asks for
    ``groq/llama-3.3-70b-versatile``, LiteLLM receives exactly that alias.
    """
    validate_model_name(model_name)
    provider = _provider_prefix(model_name)

    if provider == "custom":
        # OpenAI-compatible endpoint with arbitrary model name.  Example:
        #   DECEPTICON_MODEL=custom/qwen3-coder
        #   CUSTOM_OPENAI_API_BASE=https://gateway.example/v1
        actual_model = model_name.split("/", 1)[1]
        params: dict[str, Any] = {
            "model": f"openai/{actual_model}",
            "api_key": "os.environ/CUSTOM_OPENAI_API_KEY",
            "api_base": "os.environ/CUSTOM_OPENAI_API_BASE",
        }
    elif provider == "zai":
        # LiteLLM 1.55+ ships a native ``zai/`` provider — pass the model
        # id through verbatim and let LiteLLM resolve the base URL +
        # ZAI_API_KEY internally.
        params = {
            "model": model_name,
            "api_key": "os.environ/ZAI_API_KEY",
        }
    elif provider == "lm_studio":
        # LM Studio runs locally as an OpenAI-compatible server. The
        # base URL comes from LMSTUDIO_API_BASE (default localhost:1234).
        # No real key is required; LM Studio accepts any string.
        params = {
            "model": model_name,
            "api_base": "os.environ/LMSTUDIO_API_BASE",
            "api_key": "os.environ/LMSTUDIO_API_KEY",
        }
    elif provider == "llamacpp":
        # llama.cpp's llama-server is OpenAI-compatible but is not a
        # native LiteLLM provider, so we remap the route to ``openai/<m>``
        # and point LiteLLM at LLAMACPP_API_BASE. Symmetric to the
        # ``custom/`` branch above; kept as its own provider so a user
        # can have a generic custom OpenAI gateway AND llama.cpp wired
        # at the same time. Issue #151.
        actual_model = model_name.split("/", 1)[1]
        params = {
            "model": f"openai/{actual_model}",
            "api_key": "os.environ/LLAMACPP_API_KEY",
            "api_base": "os.environ/LLAMACPP_API_BASE",
        }
    elif provider == "xiaomi_mimo":
        # Xiaomi MiMo Open Platform — OpenAI-compatible
        # (``/v1/chat/completions``, Bearer auth). No native LiteLLM
        # provider yet; remap to ``openai/<id>`` and override api_base
        # to XIAOMI_MIMO_API_BASE (default points at
        # ``https://platform.xiaomimimo.com/v1``).
        actual_model = model_name.split("/", 1)[1]
        params = {
            "model": f"openai/{actual_model}",
            "api_key": "os.environ/XIAOMI_MIMO_API_KEY",
            "api_base": "os.environ/XIAOMI_MIMO_API_BASE",
        }
    elif provider in OPENAI_COMPAT_GATEWAYS:
        # OpenAI-compatible gateway / aggregator (OpenCode Zen, Vercel AI
        # Gateway, Hugging Face Router, Venice, NanoGPT, Synthetic, ZenMux,
        # Kimi-for-Coding, Qianfan, Cloudflare AI Gateway). Strip the gateway
        # prefix from the alias, remap to ``openai/<slug>``, and pin the
        # gateway's base URL + bearer key. The slug may itself contain
        # slashes (``vercel/anthropic/claude-opus-4.6`` →
        # ``openai/anthropic/claude-opus-4.6``) — LiteLLM forwards everything
        # after ``openai/`` to the endpoint verbatim, which is exactly what
        # the gateway's ``creator/model`` ids expect.
        api_base, api_key_env = OPENAI_COMPAT_GATEWAYS[provider]
        actual_model = model_name.split("/", 1)[1]
        params = {
            "model": f"openai/{actual_model}",
            "api_key": f"os.environ/{api_key_env}",
            "api_base": api_base,
        }
        if provider == "kimi":
            # Kimi for Coding rejects temperature != 1. Same proxy-level
            # drop as the opus entries in config/litellm.yaml.
            params["additional_drop_params"] = ["temperature"]
    else:
        params = {"model": model_name}
        if provider == "ollama_chat":
            # Ollama runs locally and has no API key. The legacy ``ollama``
            # provider is rejected upstream by validate_model_name so only
            # ``ollama_chat/`` (which routes to /api/chat with tool support)
            # reaches this branch.
            #
            # When OLLAMA_API_BASE is unset, LiteLLM's ``os.environ/<NAME>``
            # syntax resolves to an empty string and the route silently
            # 404s. Pin to ``http://host.docker.internal:11434`` as the
            # write-time default — it works on macOS, Linux, and WSL2
            # (Docker Desktop installs an /etc/hosts alias to the host)
            # and is exactly what the launcher onboard wizard writes.
            # Operators who run docker-without-Desktop on a pure Linux
            # host can override by exporting the env var; the explicit
            # ``os.environ/OLLAMA_API_BASE`` resolution takes effect when
            # the env var IS set, because LiteLLM resolves env-refs at
            # request time.
            if os.environ.get("OLLAMA_API_BASE", "").strip():
                params["api_base"] = "os.environ/OLLAMA_API_BASE"
            else:
                params["api_base"] = "http://host.docker.internal:11434"
        elif provider == "ollama_cloud":
            # Ollama Cloud (https://docs.ollama.com/cloud) — OpenAI-compatible
            # at ``https://ollama.com/v1`` with Bearer auth via the cloud key.
            # No native LiteLLM ``ollama_cloud/`` provider yet, so remap the
            # route to ``openai/<model>`` with explicit api_base override.
            # ``OLLAMA_CLOUD_API_BASE`` defaults to ``https://ollama.com/v1``
            # but can point at a self-hosted Ollama endpoint that mirrors
            # the cloud OpenAI shape. Same env-empty-fallback pattern as
            # ``ollama_chat`` above — write a literal default URL when the
            # operator hasn't pinned the base, so the route doesn't
            # silently 404 when LiteLLM resolves an empty env-ref.
            actual_model = model_name.split("/", 1)[1]
            cloud_base = (
                "os.environ/OLLAMA_CLOUD_API_BASE"
                if os.environ.get("OLLAMA_CLOUD_API_BASE", "").strip()
                else "https://ollama.com/v1"
            )
            # The onboarding wizard (onboard.go) and setup docs write the
            # key as OLLAMA_CLOUD_API_KEY; the official Ollama convention is
            # OLLAMA_API_KEY. Accept either, preferring the namespaced
            # _CLOUD_ form, so a user who followed `decepticon onboard`
            # authenticates instead of sending an empty Bearer token and
            # 401-ing on every turn — the "stuck in the Soundwave interview"
            # loop reported on Ollama Cloud.
            cloud_key_env = (
                "OLLAMA_CLOUD_API_KEY"
                if os.environ.get("OLLAMA_CLOUD_API_KEY", "").strip()
                else "OLLAMA_API_KEY"
            )
            params = {
                "model": f"openai/{actual_model}",
                "api_key": f"os.environ/{cloud_key_env}",
                "api_base": cloud_base,
            }
        else:
            # Every other native LiteLLM provider, table-driven. Extra
            # litellm_params (api_base / api_version / vertex_project /
            # tenant_id / account_id ...) come from PROVIDER_EXTRA_PARAMS; the
            # api_key env var from _resolve_key_env (alias-aware), unless the
            # provider authenticates without a bearer key (SigV4 / ADC / OCI
            # signing / OAuth exchange / local no-auth) — those are listed in
            # _NO_API_KEY_PROVIDERS. Replaces the former bedrock / vertex_ai /
            # azure / derived-key elif chain.
            params.update(PROVIDER_EXTRA_PARAMS.get(provider, {}))
            if provider == "hosted_vllm":
                # The table above points at HOSTED_VLLM_API_BASE, but the
                # VLLM_MODEL shortcut lets an operator name the server with the
                # shorter VLLM_API_BASE. Emitting the unset one would resolve to
                # an empty string at request time and 404 with no clue as to
                # why, so pin whichever the operator actually set. Same failure
                # mode the ollama_chat branch guards against.
                if not os.environ.get("HOSTED_VLLM_API_BASE", "").strip():
                    if os.environ.get("VLLM_API_BASE", "").strip():
                        params["api_base"] = "os.environ/VLLM_API_BASE"
                # Same split for the key: the catalog name is
                # HOSTED_VLLM_API_KEY, but an operator who used the VLLM_MODEL
                # shortcut will reach for VLLM_API_KEY. Prefer whichever is
                # actually set; the catalog name stays the default so the
                # every-provider-uses-a-verified-key-env invariant holds.
                if not os.environ.get("HOSTED_VLLM_API_KEY", "").strip():
                    if os.environ.get("VLLM_API_KEY", "").strip():
                        params["api_key"] = "os.environ/VLLM_API_KEY"
                    else:
                        params["api_key"] = f"os.environ/{_resolve_key_env(provider)}"
                else:
                    params["api_key"] = f"os.environ/{_resolve_key_env(provider)}"
            elif provider not in _NO_API_KEY_PROVIDERS:
                params["api_key"] = f"os.environ/{_resolve_key_env(provider)}"

    return {"model_name": model_name, "litellm_params": params}


# ── Subscription OAuth routes ───────────────────────────────────────────
# These were previously static in litellm.yaml. LiteLLM's native providers
# (chatgpt, gemini-sub, copilot, grok-sub, pplx-sub) attempt OAuth
# handshakes at startup when they see their routes. If the user hasn't
# enabled the auth method, the handshake blocks → times out → container
# becomes unhealthy. Gating on DECEPTICON_AUTH_* prevents that.

# Shadow pricing for subscription OAuth routes (USD per token, as of
# 2026-05-14). These routes are paid via flat monthly subscriptions
# (ChatGPT Pro/Plus/Team, Gemini Advanced, Copilot Pro, SuperGrok,
# Perplexity Pro), so per-token cost is NOT what the user actually pays.
# We stamp the equivalent paid-API price into ``model_info`` so
# /spend/logs reports an "API-equivalent" USD number — useful for
# comparing benchmark cost across paid and subscription routes
# apples-to-apples. The OSS user's real cash spend stays the
# subscription fee; this number is opportunity cost.
#
# Perplexity Sonar numbers are best-effort against the published rate
# card (Sonar $1/$1, Sonar Pro $3/$15) — search-call surcharge not
# modeled.
_SUBSCRIPTION_SHADOW_PRICING: dict[str, tuple[float, float]] = {
    # Codex routes omit shadow prices until model-specific rates are verified.
    "gemini-sub/gemini-2.5-pro": (0.00000125, 0.00001),
    "gemini-sub/gemini-2.5-flash": (0.0000003, 0.0000025),
    "copilot/gpt-5.5": (0.000005, 0.000030),
    "copilot/claude-sonnet-4-6": (0.000003, 0.000015),
    "copilot/gpt-5.4-mini": (0.00000075, 0.0000045),
    "copilot/gpt-5.3-codex": (0.00000175, 0.000014),
    "grok-sub/grok-4.3": (0.00000125, 0.0000025),
    "grok-sub/grok-4-1-fast-reasoning": (0.0000002, 0.0000005),
    "pplx-sub/sonar-pro": (0.000003, 0.000015),
    "pplx-sub/sonar": (0.000001, 0.000001),
}


def _with_shadow_pricing(route: dict[str, Any]) -> dict[str, Any]:
    """Attach ``model_info`` shadow pricing to a subscription route, if any.

    Models without verified shadow prices retain no explicit cost metadata.
    """
    pricing = _SUBSCRIPTION_SHADOW_PRICING.get(route["model_name"])
    if pricing is None:
        return route
    enriched = dict(route)
    enriched["model_info"] = {
        "input_cost_per_token": pricing[0],
        "output_cost_per_token": pricing[1],
    }
    return enriched


_SUBSCRIPTION_ROUTES: dict[str, list[dict[str, Any]]] = {
    # env flag → model_list entries
    "DECEPTICON_AUTH_CHATGPT": [
        # User-facing model_name stays ``auth/gpt-*`` for consistency with
        # ``auth/claude-*``. The internal LiteLLM route uses the dedicated
        # ``codex-oauth`` custom provider plus the ``oauth-gpt-`` slug
        # sentinel — without the sentinel LiteLLM's main.py:2561 falls
        # into the OpenAI branch (``model in open_ai_chat_completion_models``
        # short-circuits before the custom_llm_provider check) and forwards
        # to api.openai.com regardless of provider. The codex_chatgpt
        # handler strips the ``oauth-`` prefix before sending the model
        # name upstream. Current models and retained GPT-5.6 overrides:
        # https://learn.chatgpt.com/docs/models (2026-10-14).
        {
            "model_name": "auth/gpt-6-astra",
            "litellm_params": {"model": "codex-oauth/oauth-gpt-6-astra"},
        },
        {
            "model_name": "auth/gpt-6.1-sol",
            "litellm_params": {"model": "codex-oauth/oauth-gpt-6.1-sol"},
        },
        {
            "model_name": "auth/gpt-6-luna",
            "litellm_params": {"model": "codex-oauth/oauth-gpt-6-luna"},
        },
        {
            "model_name": "auth/gpt-5.6-sol",
            "litellm_params": {"model": "codex-oauth/oauth-gpt-5.6-sol"},
        },
        {
            "model_name": "auth/gpt-5.6-terra",
            "litellm_params": {"model": "codex-oauth/oauth-gpt-5.6-terra"},
        },
        {
            "model_name": "auth/gpt-5.6-luna",
            "litellm_params": {"model": "codex-oauth/oauth-gpt-5.6-luna"},
        },
    ],
    "DECEPTICON_AUTH_GEMINI": [
        {
            "model_name": "gemini-sub/gemini-2.5-pro",
            "litellm_params": {"model": "gemini-sub/gemini-2.5-pro"},
        },
        {
            "model_name": "gemini-sub/gemini-2.5-flash",
            "litellm_params": {"model": "gemini-sub/gemini-2.5-flash"},
        },
    ],
    "DECEPTICON_AUTH_COPILOT": [
        # GitHub Copilot retired gpt-4o / o1 / o3-mini on 2025-10-23.
        # Current Copilot model picker exposes (as of 2026-05-14):
        #   OpenAI:   gpt-5 mini, gpt-5.2, gpt-5.2-Codex, gpt-5.3-Codex,
        #             gpt-5.4, gpt-5.4 mini, gpt-5.4 nano, gpt-5.5
        #   Anthropic: Haiku 4.5, Opus 4.5/4.6/4.7, Sonnet 4.5/4.6
        #   Google:    Gemini 2.5 Pro, Gemini 3 Flash (preview)
        #   xAI:       Grok Code Fast 1
        # Default tier picks below avoid the LiteLLM main.py:2561
        # short-circuit by choosing slugs NOT in
        # ``open_ai_chat_completion_models`` — no sentinel needed.
        # ``claude-sonnet-4-6`` is in ``anthropic_models`` but main.py
        # does not early-check that list (only openai's), so it routes
        # cleanly through the ``copilot`` custom provider.
        {
            "model_name": "copilot/gpt-5.5",
            "litellm_params": {"model": "copilot/gpt-5.5"},
        },
        {
            "model_name": "copilot/claude-sonnet-4-6",
            "litellm_params": {"model": "copilot/claude-sonnet-4-6"},
        },
        {
            "model_name": "copilot/gpt-5.4-mini",
            "litellm_params": {"model": "copilot/gpt-5.4-mini"},
        },
        # Code-heavy override (option α). gpt-5.3-codex is in
        # ``open_ai_chat_completion_models``, so the ``oauth-`` slug
        # sentinel is required to dodge the main.py:2561 short-circuit.
        # copilot_handler._upstream_model_slug strips ``oauth-`` before
        # posting to api.githubcopilot.com. Pick via
        # ``DECEPTICON_MODEL_<ROLE>=copilot/gpt-5.3-codex`` for
        # patcher / exploiter / contract_auditor.
        {
            "model_name": "copilot/gpt-5.3-codex",
            "litellm_params": {"model": "copilot/oauth-gpt-5.3-codex"},
        },
    ],
    "DECEPTICON_AUTH_GROK": [
        # grok-3 / grok-3-mini retired by xAI on 2026-05-15. Replaced
        # with the current production lineup: grok-4.3 (flagship) and
        # grok-4-1-fast-reasoning (cost-efficient MID). Both slugs are
        # NOT in ``open_ai_chat_completion_models`` so no sentinel needed.
        {
            "model_name": "grok-sub/grok-4.3",
            "litellm_params": {"model": "grok-sub/grok-4.3"},
        },
        {
            "model_name": "grok-sub/grok-4-1-fast-reasoning",
            "litellm_params": {"model": "grok-sub/grok-4-1-fast-reasoning"},
        },
    ],
    "DECEPTICON_AUTH_PERPLEXITY": [
        {"model_name": "pplx-sub/sonar-pro", "litellm_params": {"model": "pplx-sub/sonar-pro"}},
        {"model_name": "pplx-sub/sonar", "litellm_params": {"model": "pplx-sub/sonar"}},
    ],
}

# Fallback entries for subscription routes — appended to litellm_settings.fallbacks
_SUBSCRIPTION_FALLBACKS: dict[str, list[dict[str, list[str]]]] = {
    "DECEPTICON_AUTH_CHATGPT": [
        {"auth/gpt-6-astra": ["auth/gpt-6.1-sol"]},
        {"auth/gpt-6.1-sol": ["auth/gpt-6-luna"]},
    ],
    "DECEPTICON_AUTH_GEMINI": [
        {"gemini-sub/gemini-2.5-pro": ["gemini-sub/gemini-2.5-flash"]},
    ],
    "DECEPTICON_AUTH_COPILOT": [
        {"copilot/gpt-5.5": ["copilot/claude-sonnet-4-6"]},
        {"copilot/claude-sonnet-4-6": ["copilot/gpt-5.4-mini"]},
    ],
    "DECEPTICON_AUTH_GROK": [
        {"grok-sub/grok-4.3": ["grok-sub/grok-4-1-fast-reasoning"]},
    ],
    "DECEPTICON_AUTH_PERPLEXITY": [
        {"pplx-sub/sonar-pro": ["pplx-sub/sonar"]},
    ],
}


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


def _inject_subscription_routes(
    config: MutableMapping[str, Any], env: Mapping[str, str] | None = None
) -> None:
    """Conditionally add subscription OAuth model routes and fallbacks.

    Only registers routes for providers whose ``DECEPTICON_AUTH_*`` flag is
    truthy.  This prevents LiteLLM's native OAuth providers from attempting
    device-code or session-token handshakes at startup when the user hasn't
    enabled the auth method.
    """
    source = env if env is not None else os.environ
    # A YAML config may carry ``model_list:`` / ``litellm_settings:`` /
    # ``fallbacks:`` keys that parse to ``None`` (key present, no value).
    # ``setdefault`` keeps that ``None`` (the key exists), so coerce each to
    # the right empty container before iterating — otherwise startup dies with
    # a raw ``'NoneType' is not iterable`` / ``has no attribute 'setdefault'``.
    if not isinstance(config.get("model_list"), list):
        config["model_list"] = []
    model_list = config["model_list"]
    existing = {e.get("model_name") for e in model_list if isinstance(e, dict)}

    if not isinstance(config.get("litellm_settings"), MutableMapping):
        config["litellm_settings"] = {}
    settings = config["litellm_settings"]
    if not isinstance(settings.get("fallbacks"), list):
        settings["fallbacks"] = []
    fallbacks = settings["fallbacks"]

    for flag, routes in _SUBSCRIPTION_ROUTES.items():
        if not _is_truthy(source.get(flag, "")):
            continue
        for route in routes:
            if route["model_name"] not in existing:
                model_list.append(_with_shadow_pricing(route))
                existing.add(route["model_name"])
        # Add corresponding fallbacks
        for fb in _SUBSCRIPTION_FALLBACKS.get(flag, []):
            if fb not in fallbacks:
                fallbacks.append(fb)


def has_subscription_routes(env: Mapping[str, str] | None = None) -> bool:
    """Return True if any DECEPTICON_AUTH_* flag enables a subscription route.

    Used by ``litellm_startup.py`` to decide whether to regenerate the
    LiteLLM config even when no ``DECEPTICON_MODEL*`` overrides are set —
    a user who only enables ``DECEPTICON_AUTH_CHATGPT=true`` still needs
    the corresponding ``auth/gpt-*`` model_list entries.
    """
    source = env if env is not None else os.environ
    return any(_is_truthy(source.get(flag, "")) for flag in _SUBSCRIPTION_ROUTES)


def merge_dynamic_models(
    config: MutableMapping[str, Any], env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Append requested models not already present in a LiteLLM config."""
    merged = copy.deepcopy(dict(config))

    # Conditionally inject subscription OAuth routes
    _inject_subscription_routes(merged, env)

    model_list = list(merged.get("model_list") or [])
    existing = {entry.get("model_name") for entry in model_list if isinstance(entry, dict)}

    for route in current_model_routes(env):
        if route["model_name"] not in existing:
            model_list.append(route)
            existing.add(route["model_name"])

    for model_name in sorted(collect_requested_models(env)):
        if model_name in existing:
            # Already registered by ``_inject_subscription_routes`` (or a
            # static entry in litellm.yaml). Skip the API-key validator —
            # ``auth/*`` slugs are deliberately rejected by
            # ``validate_model_name`` for the API-key path even though
            # they are valid subscription targets.
            continue
        validate_model_name(model_name)
        model_list.append(build_model_entry(model_name))
        existing.add(model_name)

    merged["model_list"] = model_list
    return merged


def write_dynamic_config(config_path: str | Path, output_path: str | Path) -> Path:
    """Read a LiteLLM YAML config, append requested models, and write a copy."""
    source_path = Path(config_path)
    target_path = Path(output_path)

    try:
        with source_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"LiteLLM base config not found at {source_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"LiteLLM base config at {source_path} is not valid YAML: {exc}") from exc
    if config is None:
        config = {}
    if not isinstance(config, MutableMapping):
        raise ValueError(
            f"LiteLLM base config at {source_path} must be a YAML mapping "
            f"(got {type(config).__name__}); expected top-level keys like "
            "'model_list:' / 'litellm_settings:'."
        )

    merged = merge_dynamic_models(config, os.environ)

    target_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(target_path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target_path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False)
    os.chmod(target_path, 0o600)

    return target_path


__all__ = [
    "build_model_entry",
    "collect_requested_models",
    "has_subscription_routes",
    "merge_dynamic_models",
    "validate_model_name",
    "write_dynamic_config",
]
