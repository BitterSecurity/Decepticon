"""Tests for headless auth introspection: factory.auth_inventory + the CLI."""

from __future__ import annotations

import json

import pytest

from decepticon.cli.auth import main as auth_main
from decepticon.llm import factory
from decepticon.llm.factory import AuthMethod

# Every env var that could enable a method, so a test starts from a known
# blank slate regardless of the host/CI environment.
_PROVIDER_ENV_VARS = (
    *factory._API_METHOD_ENV.values(),
    *factory._OAUTH_METHOD_ENV.values(),
    *factory._LOCAL_METHOD_ENV.values(),
    "DECEPTICON_AUTH_PRIORITY",
    "DECEPTICON_HOME",
    "OLLAMA_MODEL",
    "OLLAMA_CLOUD_MODEL",
    "LMSTUDIO_MODEL",
    "LLAMACPP_MODEL",
    "CUSTOM_OPENAI_API_KEY",
    "CLAUDE_CODE_CREDENTIALS_PATH",
    "CODEX_AUTH_PATH",
    "GEMINI_TOKENS_PATH",
    "COPILOT_TOKENS_PATH",
    "GROK_TOKENS_PATH",
    "PERPLEXITY_TOKENS_PATH",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Redirect HOME so OAuth credential-file lookups (~/.claude, ~/.config/…)
    # resolve into an empty dir instead of the developer's real tokens.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return monkeypatch


def test_inventory_nothing_configured(clean_env):
    inv = factory.auth_inventory()
    assert not inv.any_active
    assert inv.resolved_chain == ()
    assert all(not s.configured for s in inv.statuses)
    # Every enum member is reported, classified into a known bucket.
    assert {s.method for s in inv.statuses} == set(AuthMethod)
    assert all(s.kind in ("api", "subscription", "local") for s in inv.statuses)


def test_inventory_api_key_active(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 40)
    inv = factory.auth_inventory()
    anthropic = next(s for s in inv.statuses if s.method == AuthMethod.ANTHROPIC_API)
    assert anthropic.configured
    assert anthropic.active
    assert anthropic.kind == "api"
    assert AuthMethod.ANTHROPIC_API in inv.resolved_chain
    assert inv.any_active


def test_gateway_key_activates_method(clean_env):
    # An OpenAI-compatible gateway key (oh-my-pi parity) is detected and
    # routed exactly like any first-party API key — it is in the default
    # priority, so a lone OpenCode key produces an active chain.
    clean_env.setenv("OPENCODE_API_KEY", "sk-" + "o" * 40)
    inv = factory.auth_inventory()
    opencode = next(s for s in inv.statuses if s.method == AuthMethod.OPENCODE_API)
    assert opencode.configured
    assert opencode.active
    assert opencode.kind == "api"
    assert AuthMethod.OPENCODE_API in inv.resolved_chain


def test_abliteratedllm_key_activates_method(clean_env):
    # ABLITERATEDLLM_API_KEY is detected like any other gateway key: no
    # key-prefix hint is pinned for this provider, so the generic
    # non-placeholder / minimum-length checks apply.
    clean_env.setenv("ABLITERATEDLLM_API_KEY", "abliterated-" + "a" * 32)
    inv = factory.auth_inventory()
    abliteratedllm = next(s for s in inv.statuses if s.method == AuthMethod.ABLITERATEDLLM_API)
    assert abliteratedllm.configured
    assert abliteratedllm.active
    assert abliteratedllm.kind == "api"
    assert AuthMethod.ABLITERATEDLLM_API in inv.resolved_chain


def test_placeholder_key_is_not_configured(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "your-anthropic-key-here")
    inv = factory.auth_inventory()
    anthropic = next(s for s in inv.statuses if s.method == AuthMethod.ANTHROPIC_API)
    assert not anthropic.configured
    assert not inv.any_active


def test_subscription_auto_routes_by_default(clean_env, tmp_path):
    # A fully-wired Gemini Advanced subscription is now in the DEFAULT priority
    # chain, so it is routed without the user authoring DECEPTICON_AUTH_PRIORITY
    # (the footgun the earlier release left for google/copilot/grok/perplexity).
    cred = tmp_path / "gemini.json"
    cred.write_text(json.dumps({"access_token": "x"}))
    clean_env.setenv("DECEPTICON_AUTH_GEMINI", "true")
    clean_env.setenv("GEMINI_TOKENS_PATH", str(cred))
    inv = factory.auth_inventory()
    gem = next(s for s in inv.statuses if s.method == AuthMethod.GOOGLE_OAUTH)
    assert gem.configured
    assert gem.kind == "subscription"
    assert gem.in_priority
    assert gem.active
    assert AuthMethod.GOOGLE_OAUTH in inv.resolved_chain
    assert AuthMethod.GOOGLE_OAUTH not in {s.method for s in inv.configured_but_idle}


def test_configured_but_idle_when_priority_omits(clean_env):
    # configured_but_idle still flags a credential the user excluded via an
    # explicit priority list — wired but deliberately not routed.
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 40)
    clean_env.setenv("OPENAI_API_KEY", "sk-" + "y" * 48)
    clean_env.setenv("DECEPTICON_AUTH_PRIORITY", "openai_api")
    inv = factory.auth_inventory()
    anthropic = next(s for s in inv.statuses if s.method == AuthMethod.ANTHROPIC_API)
    assert anthropic.configured
    assert not anthropic.in_priority
    assert not anthropic.active
    assert AuthMethod.ANTHROPIC_API in {s.method for s in inv.configured_but_idle}


def test_explicit_priority_routes_subscription(clean_env, tmp_path):
    cred = tmp_path / "gemini.json"
    cred.write_text(json.dumps({"access_token": "x"}))
    clean_env.setenv("DECEPTICON_AUTH_GEMINI", "true")
    clean_env.setenv("GEMINI_TOKENS_PATH", str(cred))
    clean_env.setenv("DECEPTICON_AUTH_PRIORITY", "google_oauth")
    inv = factory.auth_inventory()
    gem = next(s for s in inv.statuses if s.method == AuthMethod.GOOGLE_OAUTH)
    assert gem.active
    assert inv.priority_explicit
    assert inv.resolved_chain == (AuthMethod.GOOGLE_OAUTH,)
    assert not inv.configured_but_idle


def test_cli_status_json(clean_env, capsys):
    clean_env.setenv("OPENAI_API_KEY", "sk-" + "y" * 48)
    rc = auth_main(["status", "--json", "--no-env-file"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["any_active"] is True
    assert "openai_api" in payload["resolved_chain"]
    assert any(m["method"] == "openai_api" and m["active"] for m in payload["methods"])


def test_cli_doctor_exit_codes(clean_env, capsys):
    # No credentials → doctor fails for CI preflight.
    assert auth_main(["doctor", "--no-env-file"]) == 2
    capsys.readouterr()
    # With a real key → doctor passes.
    clean_env.setenv("GROQ_API_KEY", "gsk_" + "z" * 40)
    assert auth_main(["doctor", "--no-env-file"]) == 0
    assert "active" in capsys.readouterr().out.lower()
