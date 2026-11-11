"""Composed per-role model override — ``PluginBundle.models`` + the
``DECEPTICON_MODEL_<ROLE>`` operator env, applied by ``LLMFactory``.

This is the model analogue of the middleware/tool/prompt composition a
plugin already gets: a SaaS bundle can re-tier a single agent onto a
different model without editing the OSS tier map or a global env var.
"""

from decepticon.agents.build import resolve_role_model
from decepticon.llm.factory import LLMFactory
from decepticon_core.plugin_loader import PluginBundle
from decepticon_core.types.llm import AuthMethod, Credentials, ModelAssignment, ProxyConfig

_ITER = "decepticon.agents.build._iter_override_bundles"


class TestResolveRoleModel:
    def test_desktop_selection_reaches_role_factory(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_DESKTOP_MODEL", "openai/gpt-5.5")
        monkeypatch.setenv("DECEPTICON_MODEL_EXPLOIT", "anthropic/claude-opus-4-8")
        assert resolve_role_model("exploit") == "openai/gpt-5.5"
        assignment = LLMFactory._compose_assignment(
            "exploit", ModelAssignment(primary="anthropic/claude-opus-4-8", fallbacks=[])
        )
        assert assignment.primary == "openai/gpt-5.5"
        assert assignment.fallbacks == []

    def test_forced_model_env_reaches_role_factory(self, monkeypatch):
        # Host-neutral name — the replacement for DECEPTICON_DESKTOP_MODEL.
        monkeypatch.delenv("DECEPTICON_DESKTOP_MODEL", raising=False)
        monkeypatch.setenv("DECEPTICON_FORCED_MODEL", "openai/gpt-5.5")
        assert resolve_role_model("exploit") == "openai/gpt-5.5"

    def test_forced_model_wins_over_legacy_desktop_alias(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_FORCED_MODEL", "openai/gpt-5.5")
        monkeypatch.setenv("DECEPTICON_DESKTOP_MODEL", "ollama/qwen3")
        assert resolve_role_model("exploit") == "openai/gpt-5.5"

    def test_desktop_selection_works_with_single_credential_and_no_cloud_fallback(
        self, monkeypatch
    ):
        monkeypatch.setenv("DECEPTICON_DESKTOP_MODEL", "ollama/qwen3")
        factory = LLMFactory(
            proxy=ProxyConfig(),
            credentials=Credentials(methods=[AuthMethod.MINIMAX_API]),
            profile="eco",
        )
        assignment = factory.router.get_assignment("scanner")
        assert assignment.primary == "ollama/qwen3"
        assert assignment.fallbacks == []
        assert factory.get_fallback_models("scanner") == []

    def test_none_when_neither_env_nor_bundle(self, monkeypatch):
        monkeypatch.delenv("DECEPTICON_MODEL_EXPLOIT", raising=False)
        monkeypatch.setattr(_ITER, lambda role: iter(()))
        assert resolve_role_model("exploit") is None

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_MODEL_EXPLOIT", "auth/claude-sonnet-5")
        assert resolve_role_model("exploit") == "auth/claude-sonnet-5"

    def test_bundle_override(self, monkeypatch):
        monkeypatch.delenv("DECEPTICON_MODEL_EXPLOIT", raising=False)
        bundle = PluginBundle(bundle="rt", models={"exploit": "auth/claude-sonnet-5"})
        monkeypatch.setattr(_ITER, lambda role: iter((bundle,)))
        assert resolve_role_model("exploit") == "auth/claude-sonnet-5"

    def test_env_beats_bundle(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_MODEL_EXPLOIT", "auth/claude-opus-4-8")
        bundle = PluginBundle(bundle="rt", models={"exploit": "auth/claude-sonnet-5"})
        monkeypatch.setattr(_ITER, lambda role: iter((bundle,)))
        assert resolve_role_model("exploit") == "auth/claude-opus-4-8"

    def test_conflicting_bundles_keep_first(self, monkeypatch):
        monkeypatch.delenv("DECEPTICON_MODEL_EXPLOIT", raising=False)
        a = PluginBundle(bundle="a", models={"exploit": "auth/claude-sonnet-5"})
        b = PluginBundle(bundle="b", models={"exploit": "openai/gpt-5.5"})
        monkeypatch.setattr(_ITER, lambda role: iter((a, b)))
        assert resolve_role_model("exploit") == "auth/claude-sonnet-5"


class TestComposeAssignment:
    """``LLMFactory._compose_assignment`` re-tiers a role at get time —
    so it also covers custom plugin roles resolved via a fallback role,
    not just those hardcoded in ``AGENT_TIERS``."""

    def _base(self):
        return ModelAssignment(
            primary="anthropic/claude-opus-4-8",
            fallbacks=["openai/gpt-5.5"],
            temperature=0.3,
        )

    def test_override_promoted_to_primary(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_MODEL_EXPLOIT", "auth/claude-sonnet-5")
        a = LLMFactory._compose_assignment("exploit", self._base())
        assert a.primary == "auth/claude-sonnet-5"
        assert a.fallbacks[0] == "anthropic/claude-opus-4-8"  # default demoted, not dropped
        assert a.temperature == 0.3

    def test_no_override_returns_input_unchanged(self, monkeypatch):
        monkeypatch.delenv("DECEPTICON_MODEL_EXPLOIT", raising=False)
        monkeypatch.setattr(_ITER, lambda role: iter(()))
        base = self._base()
        assert LLMFactory._compose_assignment("exploit", base) is base

    def test_override_equal_to_primary_is_noop(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_MODEL_EXPLOIT", "anthropic/claude-opus-4-8")
        base = self._base()
        assert LLMFactory._compose_assignment("exploit", base) is base

    def test_override_deduped_from_fallbacks(self, monkeypatch):
        # Promoting a model already in the fallback chain must not double it.
        monkeypatch.setenv("DECEPTICON_MODEL_EXPLOIT", "openai/gpt-5.5")
        a = LLMFactory._compose_assignment("exploit", self._base())
        assert a.primary == "openai/gpt-5.5"
        assert a.fallbacks == ["anthropic/claude-opus-4-8"]
