"""Unit tests for decepticon_core.types.llm — tier-based, credentials-aware mapping."""

from pathlib import Path

import pytest
import yaml

from decepticon_core.types.llm import (
    AGENT_TEMPERATURES,
    AGENT_TIERS,
    METHOD_MODELS,
    AuthMethod,
    Credentials,
    LLMModelMapping,
    ModelAssignment,
    ModelProfile,
    ProxyConfig,
    Tier,
    resolve_chain,
)

_LITELLM_CONFIG = Path(__file__).resolve().parents[5] / "config" / "litellm.yaml"

# ── Enum sanity ─────────────────────────────────────────────────────────


class TestTier:
    def test_values(self):
        assert Tier.HIGH == "high"
        assert Tier.MID == "mid"
        assert Tier.LOW == "low"

    def test_iteration_complete(self):
        assert {t for t in Tier} == {Tier.HIGH, Tier.MID, Tier.LOW}


class TestAuthMethod:
    def test_values(self):
        assert AuthMethod.ANTHROPIC_API == "anthropic_api"
        assert AuthMethod.ANTHROPIC_OAUTH == "anthropic_oauth"
        assert AuthMethod.OPENAI_API == "openai_api"
        assert AuthMethod.GOOGLE_API == "google_api"
        assert AuthMethod.MINIMAX_API == "minimax_api"

    def test_anthropic_api_and_oauth_distinct(self):
        assert AuthMethod.ANTHROPIC_API != AuthMethod.ANTHROPIC_OAUTH


class TestModelProfile:
    def test_values(self):
        assert ModelProfile.ECO == "eco"
        assert ModelProfile.MAX == "max"
        assert ModelProfile.TEST == "test"


# ── Tier × Method matrix ────────────────────────────────────────────────


class TestMethodModels:
    def test_anthropic_api_full_tier_coverage(self):
        m = METHOD_MODELS[AuthMethod.ANTHROPIC_API]
        assert m[Tier.HIGH] == "anthropic/claude-opus-4-8"
        assert m[Tier.MID] == "anthropic/claude-sonnet-5"
        assert m[Tier.LOW] == "anthropic/claude-haiku-4-5"

    def test_anthropic_oauth_routes_to_auth_prefix(self):
        m = METHOD_MODELS[AuthMethod.ANTHROPIC_OAUTH]
        assert m[Tier.HIGH] == "auth/claude-opus-4-8"
        assert m[Tier.MID] == "auth/claude-sonnet-5"
        assert m[Tier.LOW] == "auth/claude-haiku-4-5"

    def test_openai_full_tier_coverage(self):
        m = METHOD_MODELS[AuthMethod.OPENAI_API]
        assert m[Tier.HIGH] == "openai/gpt-5.5"
        assert m[Tier.MID] == "openai/gpt-5.4"
        assert m[Tier.LOW] == "openai/gpt-5-nano"

    def test_chatgpt_oauth_uses_only_supported_subscription_models(self) -> None:
        m = METHOD_MODELS[AuthMethod.OPENAI_OAUTH]
        assert m[Tier.HIGH] == "auth/gpt-6-astra"
        assert m[Tier.MID] == "auth/gpt-6.1-sol"
        assert m[Tier.LOW] == "auth/gpt-6-luna"
        assert "auth/gpt-5-nano" not in m.values()

    def test_google_full_tier_coverage(self):
        m = METHOD_MODELS[AuthMethod.GOOGLE_API]
        assert m[Tier.HIGH] == "gemini/gemini-2.5-pro"
        assert m[Tier.MID] == "gemini/gemini-2.5-flash"
        assert m[Tier.LOW] == "gemini/gemini-2.5-flash-lite"

    def test_minimax_no_low_tier(self):
        m = METHOD_MODELS[AuthMethod.MINIMAX_API]
        assert m[Tier.HIGH] == "minimax/MiniMax-M3"
        assert m[Tier.MID] == "minimax/MiniMax-M2.7-highspeed"
        assert Tier.LOW not in m

    def test_cheaper_inference_tiers(self):
        m = METHOD_MODELS[AuthMethod.CHEAPER_INFERENCE_API]
        assert m[Tier.HIGH] == "cheaperinference/claude-sonnet-5"
        assert m[Tier.MID] == "cheaperinference/gpt-5.4"
        assert m[Tier.LOW] == "cheaperinference/gpt-5.4-mini"

    def test_default_bedrock_models_are_registered(self):
        config = yaml.safe_load(_LITELLM_CONFIG.read_text(encoding="utf-8"))
        routes = {entry["model_name"]: entry["litellm_params"] for entry in config["model_list"]}

        for model in METHOD_MODELS[AuthMethod.BEDROCK_API].values():
            assert routes[model]["model"] == model
            assert "api_key" not in routes[model]


# ── Per-agent tier table ────────────────────────────────────────────────


class TestAgentTiers:
    def test_high_tier_agents(self):
        for role in (
            "decepticon",
            "exploit",
            "exploiter",
            "patcher",
            "contract_auditor",
            "analyst",
            "vulnresearch",
        ):
            assert AGENT_TIERS[role] == Tier.HIGH

    def test_mid_tier_agents(self):
        for role in (
            "detector",
            "verifier",
            "postexploit",
            "ad_operator",
            "cloud_hunter",
            "reverser",
        ):
            assert AGENT_TIERS[role] == Tier.MID

    def test_low_tier_agents(self):
        for role in ("soundwave", "recon", "scanner"):
            assert AGENT_TIERS[role] == Tier.LOW

    def test_all_agents_have_temperature(self):
        for role in AGENT_TIERS:
            assert role in AGENT_TEMPERATURES

    def test_temperatures_in_valid_range(self):
        for role, t in AGENT_TEMPERATURES.items():
            assert 0.0 <= t <= 2.0, f"{role}: {t} out of range"


# ── Credentials ─────────────────────────────────────────────────────────


class TestCredentials:
    def test_default_empty(self):
        c = Credentials()
        assert c.methods == []

    def test_explicit_methods(self):
        c = Credentials(methods=[AuthMethod.ANTHROPIC_API, AuthMethod.OPENAI_API])
        assert c.methods == [AuthMethod.ANTHROPIC_API, AuthMethod.OPENAI_API]

    def test_all_api_methods_helper(self):
        c = Credentials.all_api_methods()
        assert c.methods == [
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


# ── resolve_chain ───────────────────────────────────────────────────────


class TestResolveChain:
    def test_anthropic_api_only_high(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_API])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["anthropic/claude-opus-4-8"]

    def test_oauth_only_high(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_OAUTH])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["auth/claude-opus-4-8"]

    def test_oauth_then_api_high(self):
        # Subscription primary, paid API fallback when quota hits.
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_OAUTH, AuthMethod.ANTHROPIC_API])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["auth/claude-opus-4-8", "anthropic/claude-opus-4-8"]

    def test_oauth_then_openai_low(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_OAUTH, AuthMethod.OPENAI_API])
        chain = resolve_chain(Tier.LOW, creds)
        assert chain == ["auth/claude-haiku-4-5", "openai/gpt-5-nano"]

    def test_minimax_low_falls_through(self):
        # MiniMax has no LOW tier; chain should skip and continue with the
        # next method in priority order.
        creds = Credentials(methods=[AuthMethod.MINIMAX_API, AuthMethod.OPENAI_API])
        chain = resolve_chain(Tier.LOW, creds)
        assert chain == ["openai/gpt-5-nano"]

    def test_minimax_only_low_returns_empty(self):
        creds = Credentials(methods=[AuthMethod.MINIMAX_API])
        chain = resolve_chain(Tier.LOW, creds)
        assert chain == []

    def test_chatgpt_oauth_low_uses_gpt_6_luna(self) -> None:
        creds = Credentials(methods=[AuthMethod.OPENAI_OAUTH])
        chain = resolve_chain(Tier.LOW, creds)
        assert chain == ["auth/gpt-6-luna"]

    def test_empty_credentials_returns_empty(self):
        assert resolve_chain(Tier.HIGH, Credentials()) == []

    def test_priority_order_preserved(self):
        creds = Credentials(
            methods=[
                AuthMethod.OPENAI_API,
                AuthMethod.ANTHROPIC_API,
                AuthMethod.GOOGLE_API,
            ]
        )
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == [
            "openai/gpt-5.5",
            "anthropic/claude-opus-4-8",
            "gemini/gemini-2.5-pro",
        ]


# ── OpenAI-compatible gateways / aggregators (oh-my-pi parity) ──────────


class TestGatewayChains:
    """The gateway AuthMethods have fixed catalogs (no env-driven model),
    so resolve_chain just reads METHOD_MODELS. Every tier must resolve to
    a gateway-prefixed alias so routes never collide across gateways."""

    GATEWAYS = (
        AuthMethod.OPENCODE_API,
        AuthMethod.VERCEL_GATEWAY_API,
        AuthMethod.HUGGINGFACE_API,
        AuthMethod.VENICE_API,
        AuthMethod.NANOGPT_API,
        AuthMethod.SYNTHETIC_API,
        AuthMethod.ZENMUX_API,
        AuthMethod.QIANFAN_API,
        AuthMethod.CLOUDFLARE_GATEWAY_API,
        AuthMethod.CHEAPER_INFERENCE_API,
    )

    def test_opencode_high_resolves_to_prefixed_alias(self):
        creds = Credentials(methods=[AuthMethod.OPENCODE_API])
        assert resolve_chain(Tier.HIGH, creds) == ["opencode/claude-opus-4-6"]

    def test_every_gateway_has_all_three_tiers(self):
        # Unlike MiniMax/Mistral (no LOW), the gateways expose HIGH/MID/LOW,
        # so a single-gateway inventory yields a non-empty chain at every tier.
        for method in self.GATEWAYS:
            creds = Credentials(methods=[method])
            for tier in (Tier.HIGH, Tier.MID, Tier.LOW):
                chain = resolve_chain(tier, creds)
                assert len(chain) == 1, (method, tier)
                assert "/" in chain[0]

    def test_gateway_aliases_keep_gateway_prefix(self):
        # The leading prefix of each alias must match the gateway's own
        # routing prefix (opencode/, vercel/, hf/, …) — the property that
        # keeps two gateways sharing an upstream slug from colliding.
        expected_prefix = {
            AuthMethod.OPENCODE_API: "opencode/",
            AuthMethod.VERCEL_GATEWAY_API: "vercel/",
            AuthMethod.HUGGINGFACE_API: "hf/",
            AuthMethod.VENICE_API: "venice/",
            AuthMethod.NANOGPT_API: "nanogpt/",
            AuthMethod.SYNTHETIC_API: "synthetic/",
            AuthMethod.ZENMUX_API: "zenmux/",
            AuthMethod.QIANFAN_API: "qianfan/",
            AuthMethod.CLOUDFLARE_GATEWAY_API: "cfgateway/",
            AuthMethod.CHEAPER_INFERENCE_API: "cheaperinference/",
        }
        for method, prefix in expected_prefix.items():
            creds = Credentials(methods=[method])
            chain = resolve_chain(Tier.HIGH, creds)
            assert chain[0].startswith(prefix), (method, chain)

    def test_gateway_falls_back_to_next_method(self):
        # A gateway primary with a vendor-API fallback resolves both, in order.
        creds = Credentials(methods=[AuthMethod.OPENCODE_API, AuthMethod.OPENAI_API])
        assert resolve_chain(Tier.HIGH, creds) == [
            "opencode/claude-opus-4-6",
            "openai/gpt-5.5",
        ]


# ── OLLAMA_LOCAL dynamic resolution (issue #106) ────────────────────────


class TestOllamaLocalChain:
    """OLLAMA_LOCAL is special — its model id comes from OLLAMA_MODEL env
    and collapses to the same model across all tiers (local GPU usually
    runs one model). The chain must pull the live env value, not a static
    placeholder."""

    def test_ollama_resolves_user_chosen_model_at_high(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3-coder:30b")
        creds = Credentials(methods=[AuthMethod.OLLAMA_LOCAL])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["ollama_chat/qwen3-coder:30b"]

    def test_ollama_collapses_across_tiers(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
        creds = Credentials(methods=[AuthMethod.OLLAMA_LOCAL])
        for tier in (Tier.HIGH, Tier.MID, Tier.LOW):
            assert resolve_chain(tier, creds) == ["ollama_chat/llama3.2"]

    def test_ollama_skipped_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_API_BASE", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        # OLLAMA_LOCAL listed but not configured — chain should drop it
        # rather than emit ``ollama_chat/__OLLAMA_MODEL__`` placeholder.
        creds = Credentials(methods=[AuthMethod.OLLAMA_LOCAL, AuthMethod.OPENAI_API])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["openai/gpt-5.5"]

    def test_ollama_default_when_only_base_set(self, monkeypatch):
        # User wired up the URL but didn't pick a model — fall back to
        # llama3.2 rather than failing, since they explicitly opted in.
        monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        creds = Credentials(methods=[AuthMethod.OLLAMA_LOCAL])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["ollama_chat/llama3.2"]

    def test_ollama_mixed_with_api_chain_priority_wins(self, monkeypatch):
        # User has OpenAI + local Ollama and prefers local primary →
        # chain leads with Ollama and falls back to the cloud API.
        monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3-coder:30b")
        creds = Credentials(methods=[AuthMethod.OLLAMA_LOCAL, AuthMethod.OPENAI_API])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["ollama_chat/qwen3-coder:30b", "openai/gpt-5.5"]


# ── LLAMACPP_LOCAL dynamic resolution (issue #151) ──────────────────────


class TestLlamacppLocalChain:
    """LLAMACPP_LOCAL is OpenAI-compatible (llama-server) and collapses
    across tiers like OLLAMA_LOCAL — llama-server runs one GGUF at a
    time. Chain must read the live env value, not a static placeholder.
    """

    def test_llamacpp_resolves_user_chosen_model_at_high(self, monkeypatch):
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://host.docker.internal:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODEL", "qwen2.5-coder-7b-instruct-q4_k_m")
        creds = Credentials(methods=[AuthMethod.LLAMACPP_LOCAL])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["llamacpp/qwen2.5-coder-7b-instruct-q4_k_m"]

    def test_llamacpp_collapses_across_tiers(self, monkeypatch):
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://host.docker.internal:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODEL", "llama-3.3-70b-instruct-q5_k_m")
        creds = Credentials(methods=[AuthMethod.LLAMACPP_LOCAL])
        for tier in (Tier.HIGH, Tier.MID, Tier.LOW):
            assert resolve_chain(tier, creds) == ["llamacpp/llama-3.3-70b-instruct-q5_k_m"]

    def test_llamacpp_skipped_when_env_unset(self, monkeypatch):
        """LLAMACPP_LOCAL listed but not configured — chain must drop it
        rather than emit ``llamacpp/__LLAMACPP_MODEL__`` placeholder."""
        monkeypatch.delenv("LLAMACPP_API_BASE", raising=False)
        monkeypatch.delenv("LLAMACPP_MODEL", raising=False)
        creds = Credentials(methods=[AuthMethod.LLAMACPP_LOCAL, AuthMethod.OPENAI_API])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == ["openai/gpt-5.5"]

    def test_llamacpp_default_when_only_base_set(self, monkeypatch):
        """User opted in via base URL but didn't pick a model — fall
        back to the documented default rather than failing."""
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://host.docker.internal:8080/v1")
        monkeypatch.delenv("LLAMACPP_MODEL", raising=False)
        creds = Credentials(methods=[AuthMethod.LLAMACPP_LOCAL])
        chain = resolve_chain(Tier.HIGH, creds)
        # The default lives in models._LLAMACPP_DEFAULT_MODEL — assert
        # on the prefix + the format rather than the exact tag so a
        # future bump doesn't churn this test.
        assert len(chain) == 1
        assert chain[0].startswith("llamacpp/")
        assert chain[0] != "llamacpp/__LLAMACPP_MODEL__"

    def test_llamacpp_mixed_with_api_chain_priority_wins(self, monkeypatch):
        """User has llama.cpp primary + OpenAI fallback — chain leads
        with llama.cpp."""
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://host.docker.internal:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODEL", "qwen2.5-coder-7b-instruct-q4_k_m")
        creds = Credentials(methods=[AuthMethod.LLAMACPP_LOCAL, AuthMethod.OPENAI_API])
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == [
            "llamacpp/qwen2.5-coder-7b-instruct-q4_k_m",
            "openai/gpt-5.5",
        ]

    def test_llamacpp_coexists_with_lmstudio_and_custom_openai(self, monkeypatch):
        """All three OpenAI-compatible local-ish methods configured at
        once — each picks up its own env namespace and the chain holds
        all three in priority order. Pins that the new ``llamacpp/``
        prefix does not collide with ``custom/`` or ``lm_studio/``.
        """
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://host.docker.internal:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODEL", "model-llamacpp")
        monkeypatch.setenv("LMSTUDIO_API_BASE", "http://host.docker.internal:1234/v1")
        monkeypatch.setenv("LMSTUDIO_MODEL", "model-lmstudio")
        monkeypatch.setenv("CUSTOM_OPENAI_API_BASE", "https://gateway.example/v1")
        monkeypatch.setenv("CUSTOM_OPENAI_MODEL", "model-custom")
        creds = Credentials(
            methods=[
                AuthMethod.LLAMACPP_LOCAL,
                AuthMethod.LMSTUDIO_LOCAL,
                AuthMethod.CUSTOM_OPENAI_API,
            ]
        )
        chain = resolve_chain(Tier.HIGH, creds)
        assert chain == [
            "llamacpp/model-llamacpp",
            "lm_studio/model-lmstudio",
            "custom/model-custom",
        ]


# ── ModelAssignment ─────────────────────────────────────────────────────


class TestModelAssignment:
    def test_defaults(self):
        a = ModelAssignment(primary="x")
        assert a.primary == "x"
        assert a.fallbacks == []
        assert a.fallback is None  # backwards-compat property
        assert a.temperature == 0.7

    def test_with_single_fallback(self):
        a = ModelAssignment(primary="a", fallbacks=["b"], temperature=0.3)
        assert a.fallbacks == ["b"]
        assert a.fallback == "b"
        assert a.temperature == 0.3

    def test_with_multi_fallback_chain(self):
        a = ModelAssignment(primary="a", fallbacks=["b", "c", "d"])
        assert a.fallbacks == ["b", "c", "d"]
        # Backwards-compat property returns only the first fallback.
        assert a.fallback == "b"

    def test_temperature_bounds(self):
        with pytest.raises(Exception):
            ModelAssignment(primary="x", temperature=3.0)
        with pytest.raises(Exception):
            ModelAssignment(primary="x", temperature=-0.1)


# ── LLMModelMapping ─────────────────────────────────────────────────────


class TestLLMModelMapping:
    def test_get_assignment_unknown_raises(self):
        m = LLMModelMapping()
        with pytest.raises(KeyError):
            m.get_assignment("nonexistent")

    def test_from_credentials_anthropic_only_eco(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_API])
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        a = m.get_assignment("decepticon")
        assert a.primary == "anthropic/claude-opus-4-8"
        assert a.fallbacks == []

    def test_from_credentials_oauth_plus_api(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_OAUTH, AuthMethod.ANTHROPIC_API])
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        a = m.get_assignment("decepticon")
        assert a.primary == "auth/claude-opus-4-8"
        assert a.fallbacks == ["anthropic/claude-opus-4-8"]

    def test_from_credentials_full_chain_high_tier(self):
        # Every method configured → every method appears in the HIGH-tier chain
        # in priority order. ModelFallbackMiddleware walks the full list.
        creds = Credentials(
            methods=[
                AuthMethod.ANTHROPIC_OAUTH,
                AuthMethod.ANTHROPIC_API,
                AuthMethod.OPENAI_API,
                AuthMethod.GOOGLE_API,
                AuthMethod.MINIMAX_API,
            ]
        )
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        a = m.get_assignment("decepticon")
        assert a.primary == "auth/claude-opus-4-8"
        assert a.fallbacks == [
            "anthropic/claude-opus-4-8",
            "openai/gpt-5.5",
            "gemini/gemini-2.5-pro",
            "minimax/MiniMax-M3",
        ]

    def test_from_credentials_full_chain_low_tier_skips_minimax(self):
        # MiniMax has no LOW model → drops out of the chain at LOW tier.
        creds = Credentials(
            methods=[
                AuthMethod.ANTHROPIC_API,
                AuthMethod.OPENAI_API,
                AuthMethod.GOOGLE_API,
                AuthMethod.MINIMAX_API,
            ]
        )
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        recon = m.get_assignment("recon")
        assert recon.primary == "anthropic/claude-haiku-4-5"
        assert recon.fallbacks == [
            "openai/gpt-5-nano",
            "gemini/gemini-2.5-flash-lite",
        ]

    def test_from_credentials_low_tier_minimax_skipped(self):
        creds = Credentials(methods=[AuthMethod.MINIMAX_API, AuthMethod.OPENAI_API])
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        recon = m.get_assignment("recon")
        assert recon.primary == "openai/gpt-5-nano"
        assert recon.fallbacks == []

    def test_from_credentials_minimax_only_low_role_dropped(self):
        # No method supplies a LOW model → recon (LOW tier) is omitted from
        # the mapping. HIGH/MID roles still resolve.
        creds = Credentials(methods=[AuthMethod.MINIMAX_API])
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        with pytest.raises(KeyError):
            m.get_assignment("recon")
        assert m.get_assignment("decepticon").primary == "minimax/MiniMax-M3"

    def test_max_profile_promotes_recon_to_high(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_API])
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.MAX)
        assert m.get_assignment("recon").primary == "anthropic/claude-opus-4-8"

    def test_test_profile_demotes_decepticon_to_low(self):
        creds = Credentials(methods=[AuthMethod.ANTHROPIC_API])
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.TEST)
        assert m.get_assignment("decepticon").primary == "anthropic/claude-haiku-4-5"

    def test_from_profile_uses_all_api_methods(self):
        m = LLMModelMapping.from_profile(ModelProfile.ECO)
        a = m.get_assignment("decepticon")
        assert a.primary == "anthropic/claude-opus-4-8"
        assert a.fallbacks == [
            "openai/gpt-5.5",
            "gemini/gemini-2.5-pro",
            "minimax/MiniMax-M3",
            "deepseek/deepseek-v4-pro",
            "xai/grok-4.3",
            "mistral/mistral-large-latest",
            "openrouter/anthropic/claude-opus-4-8",
            "nvidia_nim/meta/llama-3.3-70b-instruct",
        ]

    def test_from_profile_string_input(self):
        for name in ("eco", "max", "test"):
            assert LLMModelMapping.from_profile(name) is not None

    def test_invalid_profile_raises(self):
        with pytest.raises(ValueError):
            LLMModelMapping.from_profile("nonexistent")

    def test_temperatures_carried_through(self):
        creds = Credentials.all_api_methods()
        m = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        assert m.get_assignment("decepticon").temperature == 0.4
        assert m.get_assignment("exploit").temperature == 0.3


# ── ProxyConfig ─────────────────────────────────────────────────────────


class TestProxyConfig:
    def test_defaults(self):
        c = ProxyConfig()
        assert c.url == "http://localhost:4000"
        assert c.timeout == 120
        assert c.max_retries == 2
