"""Unit tests for dynamic LiteLLM model config generation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_MODULE_PATH = Path(__file__).resolve().parents[5] / "config" / "litellm_dynamic_config.py"
_spec = importlib.util.spec_from_file_location("decepticon_litellm_dynamic_config", _MODULE_PATH)
assert _spec is not None
assert _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

collect_requested_models = _module.collect_requested_models
build_model_entry = _module.build_model_entry
merge_dynamic_models = _module.merge_dynamic_models
validate_model_name = _module.validate_model_name
OPENAI_COMPAT_GATEWAYS = _module.OPENAI_COMPAT_GATEWAYS
ALLOWED_DYNAMIC_PROVIDERS = _module.ALLOWED_DYNAMIC_PROVIDERS
write_dynamic_config = _module.write_dynamic_config
_provider_prefix = _module._provider_prefix
_NO_API_KEY_PROVIDERS = _module._NO_API_KEY_PROVIDERS
PROVIDER_KEY_ENV_ALIASES = _module.PROVIDER_KEY_ENV_ALIASES
PROVIDER_EXTRA_PARAMS = _module.PROVIDER_EXTRA_PARAMS


@pytest.mark.parametrize(
    ("model", "key_env", "response_mode"),
    [
        ("openai/gpt-6-astra", "OPENAI_API_KEY", True),
        ("openai/gpt-6.1-sol", "OPENAI_API_KEY", True),
        ("openai/gpt-6-luna", "OPENAI_API_KEY", True),
        ("anthropic/claude-fable-5-1", "ANTHROPIC_API_KEY", False),
        ("anthropic/claude-opus-5-5", "ANTHROPIC_API_KEY", False),
        ("anthropic/claude-sonnet-5-5", "ANTHROPIC_API_KEY", False),
        ("gemini/gemini-3.8-flash", "GEMINI_API_KEY", False),
        ("xai/grok-4.7", "XAI_API_KEY", False),
    ],
)
def test_current_api_models_have_proxy_routes(
    model: str, key_env: str, response_mode: bool
) -> None:
    config = yaml.safe_load((_MODULE_PATH.parent / "litellm.yaml").read_text())
    routes = [entry for entry in config["model_list"] if entry["model_name"] == model]
    assert len(routes) == 1
    route = routes[0]
    assert route["litellm_params"]["model"] == model
    assert route["litellm_params"]["api_key"] == f"os.environ/{key_env}"
    assert (route.get("model_info", {}).get("mode") == "responses") is response_mode
    if model == "gemini/gemini-3.8-flash":
        assert route["litellm_params"]["additional_drop_params"] == [
            "temperature",
            "top_p",
            "top_k",
            "thinking_budget",
            "candidate_count",
        ]


def test_collect_requested_models_includes_global_and_role_overrides() -> None:
    env = {
        "DECEPTICON_MODEL": "openrouter/anthropic/claude-3.7-sonnet",
        "DECEPTICON_MODEL_FALLBACK": "groq/llama-3.3-70b-versatile",
        "DECEPTICON_MODEL_RECON": "ollama_chat/qwen2.5-coder:32b",
        "DECEPTICON_MODEL_RECON_FALLBACK": "openai/gpt-4.1-mini",
    }

    assert collect_requested_models(env) == {
        "openrouter/anthropic/claude-3.7-sonnet",
        "groq/llama-3.3-70b-versatile",
        "ollama_chat/qwen2.5-coder:32b",
        "openai/gpt-4.1-mini",
    }


def test_build_model_entry_uses_provider_specific_api_key_env() -> None:
    entry = build_model_entry("openrouter/anthropic/claude-3.7-sonnet")

    assert entry["model_name"] == "openrouter/anthropic/claude-3.7-sonnet"
    assert entry["litellm_params"] == {
        "model": "openrouter/anthropic/claude-3.7-sonnet",
        "api_key": "os.environ/OPENROUTER_API_KEY",
    }


def test_build_model_entry_supports_custom_openai_compatible_endpoint() -> None:
    entry = build_model_entry("custom/qwen3-coder")

    assert entry["litellm_params"] == {
        "model": "openai/qwen3-coder",
        "api_key": "os.environ/CUSTOM_OPENAI_API_KEY",
        "api_base": "os.environ/CUSTOM_OPENAI_API_BASE",
    }


def test_build_model_entry_routes_ollama_chat_to_api_base(monkeypatch) -> None:
    """When OLLAMA_API_BASE is set, the route references it via os.environ."""
    monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
    entry = build_model_entry("ollama_chat/qwen3-coder:30b")

    assert entry["litellm_params"] == {
        "model": "ollama_chat/qwen3-coder:30b",
        "api_base": "os.environ/OLLAMA_API_BASE",
    }


def test_build_model_entry_ollama_chat_default_when_env_unset(monkeypatch) -> None:
    """When OLLAMA_API_BASE is unset, fall back to ``host.docker.internal:11434``.

    LiteLLM's ``os.environ/<NAME>`` syntax resolves an unset env var to
    an empty string, which would silently 404 every Ollama request. The
    dynamic config writer pins a sensible default at write time so
    operators who run ``DECEPTICON_LITELLM_MODELS=ollama_chat/<m>``
    without going through the launcher onboard wizard still reach the
    host Ollama instance on macOS, Linux, and WSL2.
    """
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
    entry = build_model_entry("ollama_chat/qwen3-coder:30b")

    assert entry["litellm_params"] == {
        "model": "ollama_chat/qwen3-coder:30b",
        "api_base": "http://host.docker.internal:11434",
    }


def test_build_model_entry_ollama_cloud_prefers_cloud_api_key(monkeypatch) -> None:
    """OLLAMA_CLOUD_API_KEY (what the onboard wizard writes) wins over
    OLLAMA_API_KEY so a user who followed `decepticon onboard` authenticates."""
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "sk-cloud")
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-legacy")
    monkeypatch.delenv("OLLAMA_CLOUD_API_BASE", raising=False)
    entry = build_model_entry("ollama_cloud/qwen3-coder:480b")

    assert entry["litellm_params"] == {
        "model": "openai/qwen3-coder:480b",
        "api_key": "os.environ/OLLAMA_CLOUD_API_KEY",
        "api_base": "https://ollama.com/v1",
    }


def test_build_model_entry_ollama_cloud_falls_back_to_ollama_api_key(monkeypatch) -> None:
    """When only OLLAMA_API_KEY (the official Ollama var) is set, use it."""
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-legacy")
    monkeypatch.setenv("OLLAMA_CLOUD_API_BASE", "https://ollama.com/v1")
    entry = build_model_entry("ollama_cloud/gpt-oss:120b")

    assert entry["litellm_params"] == {
        "model": "openai/gpt-oss:120b",
        "api_key": "os.environ/OLLAMA_API_KEY",
        "api_base": "os.environ/OLLAMA_CLOUD_API_BASE",
    }


def test_validate_model_name_rejects_bare_or_internal_routes() -> None:
    with pytest.raises(ValueError, match="provider/model"):
        validate_model_name("gpt-4.1")
    with pytest.raises(ValueError, match=r"auth/\*"):
        validate_model_name("auth/claude-sonnet-4-6")
    with pytest.raises(ValueError, match="unsupported model provider"):
        validate_model_name("unknown/model")


def test_validate_model_name_rejects_other_subscription_prefixes() -> None:
    """Subscription providers register through ``custom_provider_map`` in
    ``litellm_startup.py`` — admitting them on the API-key dynamic path
    would synthesize a ``<PROVIDER>_API_KEY`` env lookup that never works.
    """
    for slug in (
        "gemini-sub/gemini-2.5-pro",
        "copilot/gpt-5.5",
        "grok-sub/grok-4.3",
        "pplx-sub/sonar",
    ):
        with pytest.raises(ValueError, match="not allowed as dynamic API-key model routes"):
            validate_model_name(slug)


def test_merge_dynamic_models_allows_subscription_model_override() -> None:
    """A user setting ``DECEPTICON_MODEL=auth/gpt-5.4-mini`` together with
    ``DECEPTICON_AUTH_CHATGPT=true`` must succeed — the subscription path
    injects the route first, and the API-key validator is skipped because
    the model is already present in the model_list.
    """
    merged = merge_dynamic_models(
        {"model_list": [], "litellm_settings": {"fallbacks": []}},
        {
            "DECEPTICON_AUTH_CHATGPT": "true",
            "DECEPTICON_MODEL": "auth/gpt-5.4-mini",
        },
    )
    names = {entry["model_name"] for entry in merged["model_list"]}
    assert "auth/gpt-5.4-mini" in names


def test_validate_model_name_rejects_legacy_ollama_with_remediation() -> None:
    """``ollama/`` (legacy /api/generate) does not support tool calling per
    LiteLLM's own ``supports_function_calling`` assertion. Decepticon agents
    always emit tool calls, so accepting it would silently break the first
    request — fail closed at config-merge time and point at ``ollama_chat/``.
    """
    with pytest.raises(ValueError, match="ollama_chat/llama3.2"):
        validate_model_name("ollama/llama3.2")
    with pytest.raises(ValueError, match="tool/function"):
        validate_model_name("ollama/qwen2.5-coder:32b")


def test_merge_dynamic_models_rejects_invalid_env_model() -> None:
    with pytest.raises(ValueError, match="provider/model"):
        merge_dynamic_models({"model_list": []}, {"DECEPTICON_MODEL": "gpt-4.1"})


def test_merge_dynamic_models_rejects_legacy_ollama_env() -> None:
    with pytest.raises(ValueError, match="ollama_chat/"):
        merge_dynamic_models(
            {"model_list": []},
            {"DECEPTICON_MODEL_RECON": "ollama/qwen2.5-coder:32b"},
        )


def test_collect_requested_models_wraps_hf_hosted_gguf_with_ollama_chat() -> None:
    """HuggingFace-hosted Ollama models embed slashes in the tag itself
    (``hf.co/<author>/<model>:<quant>``). The resolver must wrap them
    with ``ollama_chat/`` rather than treating the bare slash as a
    provider/model split — otherwise validate_model_name would reject
    ``hf.co`` as an unknown provider.
    """
    env = {
        "OLLAMA_API_BASE": "http://host.docker.internal:11434",
        "OLLAMA_MODEL": "hf.co/lmstudio-community/Qwen3-Coder-30B-GGUF:Q4_K_M",
    }
    models = collect_requested_models(env)
    assert "ollama_chat/hf.co/lmstudio-community/Qwen3-Coder-30B-GGUF:Q4_K_M" in models


def test_merge_dynamic_models_keeps_existing_entries_and_appends_missing() -> None:
    config = {
        "model_list": [
            {
                "model_name": "openai/gpt-4.1",
                "litellm_params": {
                    "model": "openai/gpt-4.1",
                    "api_key": "os.environ/OPENAI_API_KEY",
                },
            }
        ]
    }
    env = {
        "DECEPTICON_MODEL": "openai/gpt-4.1",
        "DECEPTICON_MODEL_RECON": "mistral/mistral-large-latest",
    }

    merged = merge_dynamic_models(config, env)

    assert [entry["model_name"] for entry in merged["model_list"]] == [
        "openai/gpt-4.1",
        "mistral/mistral-large-latest",
    ]


def test_merge_dynamic_models_registers_only_supported_chatgpt_oauth_routes() -> None:
    merged = merge_dynamic_models(
        {"model_list": [], "litellm_settings": {"fallbacks": []}},
        {"DECEPTICON_AUTH_CHATGPT": "true"},
    )

    # User-facing model_name stays ``auth/gpt-*`` for consistency with
    # ``auth/claude-*``, but the internal litellm_params.model uses the
    # dedicated ``codex-oauth`` custom provider — bare ``auth/gpt-*``
    # makes LiteLLM strip the prefix and route to the native OpenAI
    # provider because the ``gpt-*`` slug collides with OpenAI's aliases.
    entries = {entry["model_name"]: entry["litellm_params"] for entry in merged["model_list"]}
    assert entries == {
        # GPT-5.6-family frontier checkpoints served on the Codex subscription
        # (all verified 2026-07-13; plain gpt-5.6 400s on the Codex account).
        "auth/gpt-5.6-sol": {"model": "codex-oauth/oauth-gpt-5.6-sol"},
        "auth/gpt-5.6-terra": {"model": "codex-oauth/oauth-gpt-5.6-terra"},
        "auth/gpt-5.6-luna": {"model": "codex-oauth/oauth-gpt-5.6-luna"},
        "auth/gpt-5.5": {"model": "codex-oauth/oauth-gpt-5.5"},
        "auth/gpt-5.4": {"model": "codex-oauth/oauth-gpt-5.4"},
        "auth/gpt-5.4-mini": {"model": "codex-oauth/oauth-gpt-5.4-mini"},
        # Code-heavy override route. Registered alongside the tier
        # defaults so per-agent env overrides like
        # ``DECEPTICON_MODEL_PATCHER=auth/gpt-5.3-codex`` work without a
        # yaml edit. The ``oauth-`` slug sentinel is required because
        # ``gpt-5.3-codex`` is in ``open_ai_chat_completion_models``.
        "auth/gpt-5.3-codex": {"model": "codex-oauth/oauth-gpt-5.3-codex"},
        "auth/gpt-5.3-codex-spark": {
            "model": "codex-oauth/oauth-gpt-5.3-codex-spark",
        },
    }
    assert "auth/gpt-5-nano" not in entries
    assert merged["litellm_settings"]["fallbacks"] == [
        {"auth/gpt-5.5": ["auth/gpt-5.4"]},
        {"auth/gpt-5.4": ["auth/gpt-5.4-mini"]},
    ]


# ── llama.cpp OpenAI-compatible backend (issue #151) ────────────────────


def test_build_model_entry_routes_llamacpp_to_openai_with_custom_base() -> None:
    """``llamacpp/<model>`` routes through LiteLLM's openai-compatible
    path with ``LLAMACPP_API_BASE`` and ``LLAMACPP_API_KEY``. Symmetric
    to the ``custom/`` branch but kept distinct so users can have BOTH
    a generic custom OpenAI gateway AND llama.cpp configured.
    """
    entry = build_model_entry("llamacpp/qwen2.5-coder-7b-instruct-q4_k_m")

    assert entry["model_name"] == "llamacpp/qwen2.5-coder-7b-instruct-q4_k_m", (
        "model_name must be the agent-facing alias unchanged — per-role "
        "DECEPTICON_MODEL_<ROLE> overrides depend on this passthrough"
    )
    assert entry["litellm_params"] == {
        "model": "openai/qwen2.5-coder-7b-instruct-q4_k_m",
        "api_key": "os.environ/LLAMACPP_API_KEY",
        "api_base": "os.environ/LLAMACPP_API_BASE",
    }


def test_validate_model_name_accepts_llamacpp_prefix() -> None:
    """``llamacpp/`` is in ``ALLOWED_DYNAMIC_PROVIDERS`` so the validator
    must let it through. Pre-fix this would raise the
    ``unsupported model provider`` error.
    """
    # Should not raise.
    validate_model_name("llamacpp/qwen2.5-coder-7b-instruct-q4_k_m")


def test_validate_model_name_rejects_llamacpp_without_model_slug() -> None:
    """Bare ``llamacpp`` (no slash, no model) is still invalid — the
    validator's first check is the provider/model format gate.
    """
    with pytest.raises(ValueError, match="provider/model"):
        validate_model_name("llamacpp")


# ── OpenAI-compatible gateways / aggregators (oh-my-pi parity) ──────────


def test_every_gateway_prefix_is_an_allowed_dynamic_provider() -> None:
    """Each OPENAI_COMPAT_GATEWAYS prefix must be in ALLOWED_DYNAMIC_PROVIDERS
    or validate_model_name would reject the alias before build_model_entry's
    gateway branch runs.
    """
    for prefix in OPENAI_COMPAT_GATEWAYS:
        assert prefix in ALLOWED_DYNAMIC_PROVIDERS, prefix


def test_build_model_entry_routes_gateway_to_openai_with_api_base() -> None:
    """A gateway alias is rewritten to ``openai/<slug>`` + the gateway's
    fixed base URL and bearer key, mirroring the xiaomi_mimo / custom path.
    """
    entry = build_model_entry("opencode/claude-opus-4-6")

    assert entry["model_name"] == "opencode/claude-opus-4-6", (
        "model_name must stay the agent-facing alias unchanged — per-role "
        "DECEPTICON_MODEL_<ROLE> overrides depend on this passthrough"
    )
    assert entry["litellm_params"] == {
        "model": "openai/claude-opus-4-6",
        "api_key": "os.environ/OPENCODE_API_KEY",
        "api_base": "https://opencode.ai/zen/v1",
    }


def test_build_model_entry_gateway_preserves_multi_slash_slug() -> None:
    """Gateways whose ids embed slashes (``creator/model``) keep the full
    slug after ``openai/`` so the gateway receives the id it expects.
    """
    entry = build_model_entry("vercel/anthropic/claude-opus-4.6")

    assert entry["litellm_params"] == {
        "model": "openai/anthropic/claude-opus-4.6",
        "api_key": "os.environ/VERCEL_AI_GATEWAY_API_KEY",
        "api_base": "https://ai-gateway.vercel.sh/v1",
    }


def test_build_model_entry_gateway_preserves_hf_colon_slug() -> None:
    """Synthetic's ``hf:`` slug prefix survives the openai/ rewrite."""
    entry = build_model_entry("synthetic/hf:openai/gpt-oss-120b")

    assert entry["litellm_params"]["model"] == "openai/hf:openai/gpt-oss-120b"
    assert entry["litellm_params"]["api_base"] == "https://api.synthetic.new/openai/v1"
    assert entry["litellm_params"]["api_key"] == "os.environ/SYNTHETIC_API_KEY"


def test_build_model_entry_cloudflare_uses_env_base_url() -> None:
    """Cloudflare AI Gateway is per-account, so its api_base is an
    ``os.environ`` ref the operator supplies — not a literal URL.
    """
    entry = build_model_entry("cfgateway/anthropic/claude-opus-4-6")

    assert entry["litellm_params"] == {
        "model": "openai/anthropic/claude-opus-4-6",
        "api_key": "os.environ/CLOUDFLARE_AI_GATEWAY_API_KEY",
        "api_base": "os.environ/CLOUDFLARE_AI_GATEWAY_API_BASE",
    }


def test_validate_model_name_accepts_every_gateway_prefix() -> None:
    """Every gateway prefix must validate with a model slug attached."""
    for prefix in OPENAI_COMPAT_GATEWAYS:
        validate_model_name(f"{prefix}/some-model")  # must not raise


def test_merge_dynamic_models_registers_gateway_override() -> None:
    """A per-role gateway override flows through merge → build_model_entry."""
    merged = merge_dynamic_models(
        {"model_list": [], "litellm_settings": {"fallbacks": []}},
        {"DECEPTICON_MODEL_PATCHER": "zenmux/anthropic/claude-opus-4.6"},
    )
    entries = {e["model_name"]: e["litellm_params"] for e in merged["model_list"]}
    assert entries["zenmux/anthropic/claude-opus-4.6"] == {
        "model": "openai/anthropic/claude-opus-4.6",
        "api_key": "os.environ/ZENMUX_API_KEY",
        "api_base": "https://zenmux.ai/api/v1",
    }


# ── error-proofing: null / malformed YAML config blocks ─────────────────


def test_merge_dynamic_models_tolerates_null_model_list() -> None:
    """A YAML config with a bare ``model_list:`` key parses to ``None``.

    ``_inject_subscription_routes`` must coerce it to ``[]`` instead of
    iterating ``None`` (regression: raw ``'NoneType' is not iterable``).
    """
    merged = merge_dynamic_models({"model_list": None}, {})
    assert merged["model_list"] == []


def test_merge_dynamic_models_tolerates_null_litellm_settings() -> None:
    """A bare ``litellm_settings:`` key parses to ``None``; injecting a
    subscription route must not call ``.setdefault`` on ``None``."""
    merged = merge_dynamic_models({"litellm_settings": None}, {"DECEPTICON_AUTH_CHATGPT": "true"})
    assert isinstance(merged["litellm_settings"], dict)
    names = [e.get("model_name", "") for e in merged["model_list"]]
    assert any(n.startswith("auth/gpt") for n in names)


def test_write_dynamic_config_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    """A top-level YAML list is not a valid LiteLLM config — surface an
    actionable ValueError rather than a cryptic ``dict()`` TypeError."""
    src = tmp_path / "bad.yaml"
    src.write_text("- one\n- two\n")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        write_dynamic_config(src, tmp_path / "out.yaml")


def test_write_dynamic_config_rejects_malformed_yaml(tmp_path: Path) -> None:
    """Malformed YAML raises a clear ValueError naming the config path."""
    src = tmp_path / "broken.yaml"
    src.write_text("model_list: [unclosed\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        write_dynamic_config(src, tmp_path / "out.yaml")


def test_write_dynamic_config_tolerates_null_blocks(tmp_path: Path) -> None:
    """A config whose ``model_list:`` / ``litellm_settings:`` keys are null
    must write a usable generated config instead of crashing at boot."""
    import yaml

    src = tmp_path / "cfg.yaml"
    src.write_text("model_list:\nlitellm_settings:\n")
    out = write_dynamic_config(src, tmp_path / "out.yaml")
    data = yaml.safe_load(out.read_text())
    assert isinstance(data["model_list"], list)


# ── full LiteLLM provider catalog coverage (v1.89.0) ────────────────────
# Source of truth: the 114 providers researched from litellm 1.89.0 source.
# Pinned here (not read from the catalog JSON, which isn't shipped to the
# container) so this test fails loudly if a provider regresses out of the
# tables. ``(prefix, expected_key_env)``: expected_key_env is the api_key env
# var the route must reference, or None for key-less / local-no-key routes.
_REJECTED_CATALOG_PREFIXES = {"ollama", "github_copilot"}

_CATALOG: list[tuple[str, str | None]] = [
    ("openai", "OPENAI_API_KEY"),
    ("text-completion-openai", "OPENAI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("gemini", "GOOGLE_API_KEY"),
    ("vertex_ai", None),
    ("azure", "AZURE_API_KEY"),
    ("azure_ai", "AZURE_AI_API_KEY"),
    ("bedrock", None),
    ("sagemaker", None),
    ("sagemaker_chat", None),
    ("mistral", "MISTRAL_API_KEY"),
    ("codestral", "CODESTRAL_API_KEY"),
    ("text-completion-codestral", "CODESTRAL_API_KEY"),
    ("groq", "GROQ_API_KEY"),
    ("together_ai", "TOGETHERAI_API_KEY"),
    ("fireworks_ai", "FIREWORKS_AI_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("cohere", "COHERE_API_KEY"),
    ("cohere_chat", "COHERE_API_KEY"),
    ("perplexity", "PERPLEXITYAI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("cerebras", "CEREBRAS_API_KEY"),
    ("sambanova", "SAMBANOVA_API_KEY"),
    ("nvidia_nim", "NVIDIA_API_KEY"),
    ("databricks", "DATABRICKS_API_KEY"),
    ("watsonx", "WATSONX_API_KEY"),
    ("ollama", None),
    ("ollama_chat", None),
    ("vllm", None),
    ("hosted_vllm", "HOSTED_VLLM_API_KEY"),
    ("deepinfra", "DEEPINFRA_API_KEY"),
    ("anyscale", "ANYSCALE_API_KEY"),
    ("replicate", "REPLICATE_API_TOKEN"),
    ("huggingface", "HUGGINGFACE_API_KEY"),
    ("ai21", "AI21_API_KEY"),
    ("ai21_chat", "AI21_API_KEY"),
    ("nlp_cloud", "NLP_CLOUD_API_KEY"),
    ("voyage", "VOYAGE_API_KEY"),
    ("jina_ai", "JINA_AI_API_KEY"),
    ("cloudflare", "CLOUDFLARE_API_KEY"),
    ("baseten", "BASETEN_API_KEY"),
    ("snowflake", "SNOWFLAKE_JWT"),
    ("github", "GITHUB_API_KEY"),
    ("github_copilot", None),
    ("lm_studio", "LMSTUDIO_API_KEY"),
    ("llamafile", "LLAMAFILE_API_KEY"),
    ("novita", "NOVITA_API_KEY"),
    ("hyperbolic", "HYPERBOLIC_API_KEY"),
    ("lambda_ai", "LAMBDA_API_KEY"),
    ("nebius", "NEBIUS_API_KEY"),
    ("dashscope", "DASHSCOPE_API_KEY"),
    ("moonshot", "MOONSHOT_API_KEY"),
    ("zai", "ZAI_API_KEY"),
    ("minimax", "MINIMAX_API_KEY"),
    ("volcengine", "VOLCENGINE_API_KEY"),
    ("friendliai", "FRIENDLIAI_API_KEY"),
    ("featherless_ai", "FEATHERLESS_AI_API_KEY"),
    ("galadriel", "GALADRIEL_API_KEY"),
    ("gradient_ai", "GRADIENT_AI_API_KEY"),
    ("predibase", "PREDIBASE_API_KEY"),
    ("clarifai", "CLARIFAI_API_KEY"),
    ("aleph_alpha", "ALEPH_ALPHA_API_KEY"),
    ("petals", None),
    ("maritalk", "MARITALK_API_KEY"),
    ("xinference", "XINFERENCE_API_KEY"),
    ("empower", "EMPOWER_API_KEY"),
    ("meta_llama", "LLAMA_API_KEY"),
    ("nscale", "NSCALE_API_KEY"),
    ("v0", "V0_API_KEY"),
    ("morph", "MORPH_API_KEY"),
    ("inception", "INCEPTION_API_KEY"),
    ("litellm_proxy", "LITELLM_PROXY_API_KEY"),
    ("vercel_ai_gateway", "VERCEL_AI_GATEWAY_API_KEY"),
    ("wandb", "WANDB_API_KEY"),
    ("cometapi", "COMETAPI_API_KEY"),
    ("aiml", "AIML_API_KEY"),
    ("heroku", "HEROKU_API_KEY"),
    ("oci", None),
    ("gigachat", None),
    ("datarobot", "DATAROBOT_API_TOKEN"),
    ("ovhcloud", "OVHCLOUD_API_KEY"),
    ("scaleway", "SCW_SECRET_KEY"),
    ("lemonade", "LEMONADE_API_KEY"),
    ("docker_model_runner", "DOCKER_MODEL_RUNNER_API_KEY"),
    ("ragflow", "RAGFLOW_API_KEY"),
    ("compactifai", "COMPACTIFAI_API_KEY"),
    ("publicai", "PUBLICAI_API_KEY"),
    ("synthetic", "SYNTHETIC_API_KEY"),
    ("apertis", "STIMA_API_KEY"),
    ("nano-gpt", "NANOGPT_API_KEY"),
    ("poe", "POE_API_KEY"),
    ("chutes", "CHUTES_API_KEY"),
    ("parasail", "PARASAIL_API_KEY"),
    ("tensormesh", "TENSORMESH_INFERENCE_API_KEY"),
    ("infinity", "INFINITY_API_KEY"),
    ("triton", None),
    ("oobabooga", None),
    ("deepgram", "DEEPGRAM_API_KEY"),
    ("elevenlabs", "ELEVENLABS_API_KEY"),
    ("assemblyai", "ASSEMBLYAI_API_KEY"),
    ("recraft", "RECRAFT_API_KEY"),
    ("runwayml", "RUNWAYML_API_KEY"),
    ("stability", "STABILITY_API_KEY"),
    ("fal_ai", "FAL_AI_API_KEY"),
    ("black_forest_labs", "BFL_API_KEY"),
    ("topaz", "TOPAZ_API_KEY"),
    ("bedrock_mantle", "BEDROCK_MANTLE_API_KEY"),
    ("amazon_nova", "AMAZON_NOVA_API_KEY"),
    ("soniox", "SONIOX_API_KEY"),
    ("nvidia_riva", "NVIDIA_RIVA_API_KEY"),
    ("sap", None),
    ("openai_like", "OPENAI_LIKE_API_KEY"),
    ("manus", "MANUS_API_KEY"),
]


def test_catalog_has_full_provider_count() -> None:
    """Guard against silent shrinkage of the pinned catalog list."""
    assert len(_CATALOG) == 114


@pytest.mark.parametrize("prefix", [p for p, _ in _CATALOG if p not in _REJECTED_CATALOG_PREFIXES])
def test_every_catalog_provider_validates_and_builds(prefix: str) -> None:
    """Every supported catalog provider prefix passes validation and builds a
    route whose ``model_name`` is preserved verbatim."""
    model = f"{prefix}/some-model"
    validate_model_name(model)  # must not raise
    entry = build_model_entry(model)
    assert entry["model_name"] == model
    assert entry["litellm_params"]["model"]  # a model is always set
    provider = _provider_prefix(model)
    # Key-bearing providers must reference an api_key env; key-less ones
    # (SigV4 / ADC / OCI / OAuth / local-no-auth, plus local ollama_chat)
    # legitimately omit it.
    if provider not in _NO_API_KEY_PROVIDERS and provider != "ollama_chat":
        assert "api_key" in entry["litellm_params"], provider


@pytest.mark.parametrize(
    "prefix,expected_key", [(p, k) for p, k in _CATALOG if p not in _REJECTED_CATALOG_PREFIXES]
)
def test_every_catalog_provider_uses_source_verified_key_env(
    prefix: str, expected_key: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The api_key env ref must be the source-verified name, not the derived
    ``<PROVIDER>_API_KEY`` guess. Run with a clean env so alias providers fall
    back to their canonical (first-listed) var."""
    for _, candidates in PROVIDER_KEY_ENV_ALIASES.items():
        for name in candidates:
            monkeypatch.delenv(name, raising=False)
    params = build_model_entry(f"{prefix}/some-model")["litellm_params"]
    if expected_key is None:
        assert "api_key" not in params
    else:
        assert params["api_key"] == f"os.environ/{expected_key}"


@pytest.mark.parametrize("prefix", sorted(_REJECTED_CATALOG_PREFIXES))
def test_rejected_catalog_providers_raise(prefix: str) -> None:
    with pytest.raises(ValueError):
        validate_model_name(f"{prefix}/some-model")


def test_github_copilot_rejected_as_oauth() -> None:
    with pytest.raises(ValueError, match="OAuth"):
        validate_model_name("github_copilot/gpt-4o")


def test_unsupported_provider_error_mentions_count_and_docs() -> None:
    with pytest.raises(ValueError, match="providers are supported") as exc:
        validate_model_name("totally-made-up/model")
    assert "DECEPTICON_LITELLM_MODELS" in str(exc.value)
    assert str(len(ALLOWED_DYNAMIC_PROVIDERS)) in str(exc.value)


def test_bedrock_route_has_no_api_key() -> None:
    params = build_model_entry("bedrock/anthropic.claude-3-5-sonnet")["litellm_params"]
    assert "api_key" not in params
    assert params == {"model": "bedrock/anthropic.claude-3-5-sonnet"}


def test_vertex_ai_route_passes_project_and_location_without_key() -> None:
    params = build_model_entry("vertex_ai/gemini-2.0-flash")["litellm_params"]
    assert "api_key" not in params
    assert params["vertex_project"] == "os.environ/VERTEXAI_PROJECT"
    assert params["vertex_location"] == "os.environ/VERTEXAI_LOCATION"


def test_watsonx_route_carries_url_and_project_params() -> None:
    params = build_model_entry("watsonx/ibm/granite-13b-chat-v2")["litellm_params"]
    assert params["api_key"] == "os.environ/WATSONX_API_KEY"
    assert params["api_base"] == "os.environ/WATSONX_URL"
    assert params["project_id"] == "os.environ/WATSONX_PROJECT_ID"


def test_predibase_route_carries_tenant_id() -> None:
    params = build_model_entry("predibase/llama-3-8b-instruct")["litellm_params"]
    assert params["api_key"] == "os.environ/PREDIBASE_API_KEY"
    assert params["tenant_id"] == "os.environ/PREDIBASE_TENANT_ID"


def test_alias_key_prefers_first_env_var_actually_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """together_ai accepts 4 key aliases; the route must reference the first
    one actually set, not always the canonical TOGETHERAI_API_KEY."""
    for name in PROVIDER_KEY_ENV_ALIASES["together_ai"]:
        monkeypatch.delenv(name, raising=False)
    # None set → canonical fallback.
    assert (
        build_model_entry("together_ai/x")["litellm_params"]["api_key"]
        == "os.environ/TOGETHERAI_API_KEY"
    )
    # Only a non-canonical alias set → that one wins.
    monkeypatch.setenv("TOGETHER_AI_TOKEN", "secret")
    assert (
        build_model_entry("together_ai/x")["litellm_params"]["api_key"]
        == "os.environ/TOGETHER_AI_TOKEN"
    )


def test_cohere_falls_back_to_co_api_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROVIDER_KEY_ENV_ALIASES["cohere"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CO_API_KEY", "secret")
    assert (
        build_model_entry("cohere/command-r")["litellm_params"]["api_key"]
        == "os.environ/CO_API_KEY"
    )


def test_local_no_key_providers_accepted_with_api_base() -> None:
    """vllm/hosted_vllm/lemonade/llamafile are accepted and pinned to their
    api_base env ref."""
    assert build_model_entry("vllm/m")["litellm_params"]["api_base"] == "os.environ/VLLM_API_BASE"
    assert (
        build_model_entry("lemonade/m")["litellm_params"]["api_base"]
        == "os.environ/LEMONADE_API_BASE"
    )


def test_every_catalog_prefix_is_allowed_or_rejected() -> None:
    """No catalog provider falls through the cracks: each normalized prefix is
    either in ALLOWED_DYNAMIC_PROVIDERS or explicitly rejected."""
    for prefix, _ in _CATALOG:
        provider = _provider_prefix(f"{prefix}/x")
        if prefix in _REJECTED_CATALOG_PREFIXES:
            continue
        assert provider in ALLOWED_DYNAMIC_PROVIDERS, prefix


def test_collect_requested_models_picks_up_vllm_shortcut(monkeypatch) -> None:
    """VLLM_MODEL + VLLM_API_BASE registers a route without knowing the prefix.

    Parity with the OLLAMA_MODEL shortcut: self-hosting a model should not
    require learning that ``hosted_vllm/`` is the LiteLLM provider name.
    """
    env = {"VLLM_API_BASE": "http://gpu:8000/v1", "VLLM_MODEL": "qwen3-coder-30b"}
    assert "hosted_vllm/qwen3-coder-30b" in collect_requested_models(env)


def test_collect_requested_models_vllm_needs_both_vars() -> None:
    """A base URL alone must not register a route.

    Unlike Ollama there is no defensible default model — a vLLM server serves
    exactly what it was started with, so a guessed slug would 404 on every call.
    """
    assert collect_requested_models({"VLLM_API_BASE": "http://gpu:8000/v1"}) == set()
    assert collect_requested_models({"VLLM_MODEL": "qwen3-coder-30b"}) == set()


def test_collect_requested_models_vllm_passes_through_qualified_id() -> None:
    """An already-prefixed value is left alone, including the deprecated one.

    ``vllm/`` is LiteLLM's local-SDK provider, not the hosted server. It is
    passed through so validate_model_name can reject it with a hint; rewriting
    it silently would leave the operator's env disagreeing with the route.
    """
    env = {"VLLM_API_BASE": "http://gpu:8000/v1", "VLLM_MODEL": "hosted_vllm/m"}
    assert "hosted_vllm/m" in collect_requested_models(env)
    env["VLLM_MODEL"] = "vllm/m"
    assert "vllm/m" in collect_requested_models(env)


def test_build_model_entry_vllm_uses_the_base_url_that_is_set(monkeypatch) -> None:
    """The emitted api_base must name whichever env var the operator set.

    Emitting the unset one resolves to an empty string at request time and
    404s with nothing pointing at the cause.
    """
    monkeypatch.delenv("HOSTED_VLLM_API_BASE", raising=False)
    monkeypatch.setenv("VLLM_API_BASE", "http://gpu:8000/v1")
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    monkeypatch.delenv("HOSTED_VLLM_API_KEY", raising=False)
    entry = build_model_entry("hosted_vllm/qwen3-coder-30b")
    assert entry["litellm_params"]["api_base"] == "os.environ/VLLM_API_BASE"


def test_build_model_entry_vllm_uses_the_key_env_that_is_set(monkeypatch) -> None:
    """The key follows the same shortcut split as the base URL.

    An operator who set VLLM_API_BASE will reach for VLLM_API_KEY, not the
    catalog name. The catalog name stays the default so the
    every-provider-uses-a-verified-key-env invariant still holds.
    """
    monkeypatch.setenv("VLLM_API_BASE", "http://gpu:8000/v1")
    monkeypatch.delenv("HOSTED_VLLM_API_KEY", raising=False)
    monkeypatch.setenv("VLLM_API_KEY", "sk-local")
    entry = build_model_entry("hosted_vllm/qwen3-coder-30b")
    assert entry["litellm_params"]["api_key"] == "os.environ/VLLM_API_KEY"

    monkeypatch.setenv("HOSTED_VLLM_API_KEY", "sk-catalog")
    entry = build_model_entry("hosted_vllm/qwen3-coder-30b")
    assert entry["litellm_params"]["api_key"] == "os.environ/HOSTED_VLLM_API_KEY"


def test_build_model_entry_routes_kimi_coding_gateway() -> None:
    entry = build_model_entry("kimi/k3")

    assert entry["litellm_params"] == {
        "model": "openai/k3",
        "api_key": "os.environ/KIMI_API_KEY",
        "api_base": "https://api.kimi.com/coding/v1",
        # kimi models reject temperature != 1, so the proxy drops it
        "additional_drop_params": ["temperature"],
    }


def test_validate_model_name_accepts_kimi_coding_models() -> None:
    for model in (
        "kimi/k3",
        "kimi/k3-256k",
        "kimi/kimi-for-coding",
        "kimi/kimi-for-coding-highspeed",
    ):
        validate_model_name(model)  # must not raise


def test_kimi_gateway_registered_in_openai_compat_gateways() -> None:
    base, key_env = OPENAI_COMPAT_GATEWAYS["kimi"]
    assert base == "https://api.kimi.com/coding/v1"
    assert key_env == "KIMI_API_KEY"
