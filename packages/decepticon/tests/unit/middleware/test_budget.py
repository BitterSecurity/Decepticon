"""Tests for decepticon.middleware.budget."""

from __future__ import annotations

import logging

import pytest

from decepticon.middleware.budget import (
    BudgetCredentialCheckUnavailable,
    BudgetEnforcementMiddleware,
    BudgetExceeded,
    BudgetSpendUntracked,
    _SpendCache,
)


def test_spend_cache_set_get():
    cache = _SpendCache(ttl_seconds=60.0)
    cache.set("k1", 1.23)
    assert cache.get("k1") == 1.23


def test_spend_cache_miss():
    cache = _SpendCache(ttl_seconds=60.0)
    assert cache.get("nope") is None


def test_spend_cache_eviction_at_capacity():
    cache = _SpendCache(ttl_seconds=60.0)
    for i in range(cache._MAX_ENTRIES + 10):
        cache.set(f"k{i}", float(i))
    assert len(cache._entries) <= cache._MAX_ENTRIES


def test_disabled_when_no_caps_set():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=0.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda _k: 1000.0,
    )
    assert mw._enabled() is False


def test_under_cap_does_not_raise():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        soft_warn_at_pct=0.7,
        spend_provider=lambda _k: 5.0,
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())


def test_over_cap_raises_budget_exceeded():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda _k: 15.0,
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    with pytest.raises(BudgetExceeded) as exc_info:
        mw._enforce(_Req())
    assert exc_info.value.scope == "engagement"
    assert exc_info.value.cap_usd == 10.0


def test_budget_blocks_when_credential_inventory_fails(monkeypatch):
    from decepticon.llm import factory

    def broken_inventory():
        raise RuntimeError("credential inventory unavailable")

    monkeypatch.setattr(factory, "auth_inventory", broken_inventory)
    queries = []
    calls = []
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0, spend_provider=lambda key: queries.append(key) or 0.0
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    with pytest.raises(BudgetCredentialCheckUnavailable, match="model call blocked"):
        mw.wrap_model_call(_Req(), lambda request: calls.append(request))
    assert calls == []
    assert queries == []


@pytest.mark.asyncio
async def test_async_budget_blocks_when_credential_inventory_fails(monkeypatch):
    from decepticon.llm import factory

    def broken_inventory():
        raise RuntimeError("credential inventory unavailable")

    monkeypatch.setattr(factory, "auth_inventory", broken_inventory)
    queries = []
    calls = []
    mw = BudgetEnforcementMiddleware(
        per_agent_cap_usd=10.0, spend_provider=lambda key: queries.append(key) or 0.0
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    async def handler(request):
        calls.append(request)

    with pytest.raises(BudgetCredentialCheckUnavailable, match="model call blocked"):
        await mw.awrap_model_call(_Req(), handler)
    assert calls == []
    assert queries == []


def test_disabled_budget_does_not_require_credential_inventory(monkeypatch):
    from decepticon.llm import factory

    def broken_inventory():
        raise RuntimeError("credential inventory unavailable")

    monkeypatch.setattr(factory, "auth_inventory", broken_inventory)
    mw = BudgetEnforcementMiddleware(engagement_cap_usd=0.0, per_agent_cap_usd=0.0)

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())


def test_budget_rejects_unpriced_active_credential_before_spend_lookup(monkeypatch, caplog):
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "cheaper_inference_api")
    monkeypatch.setenv("CHEAPER_INFERENCE_API_KEY", "ci_live_" + "a" * 24)
    queries = []
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda key: queries.append(key) or 0.0,
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    monkeypatch.setattr(logging.getLogger("decepticon"), "propagate", True)
    with caplog.at_level(logging.ERROR, logger="decepticon.middleware.budget"):
        with pytest.raises(BudgetSpendUntracked, match="cheaper_inference_api"):
            mw._enforce(_Req())
        with pytest.raises(BudgetSpendUntracked):
            mw._enforce(_Req())

    assert queries == []
    assert len([r for r in caplog.records if "Cannot enforce" in r.getMessage()]) == 1


def test_budget_rejects_unpriced_fallback_even_with_priced_primary(monkeypatch):
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "anthropic_api,cheaper_inference_api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "a" * 40)
    monkeypatch.setenv("CHEAPER_INFERENCE_API_KEY", "ci_live_" + "a" * 24)
    mw = BudgetEnforcementMiddleware(engagement_cap_usd=10.0, spend_provider=lambda _k: 0.0)

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    with pytest.raises(BudgetSpendUntracked):
        mw._enforce(_Req())


def test_budget_rejects_unpriced_kimi_route(monkeypatch):
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "kimi_api")
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi-" + "a" * 32)
    mw = BudgetEnforcementMiddleware(per_agent_cap_usd=10.0, spend_provider=lambda _k: 0.0)

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    with pytest.raises(BudgetSpendUntracked, match="kimi_api"):
        mw._enforce(_Req())


def test_model_handler_not_called_when_unpriced_budget_is_blocked(monkeypatch):
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "cheaper_inference_api")
    monkeypatch.setenv("CHEAPER_INFERENCE_API_KEY", "ci_live_" + "a" * 24)
    mw = BudgetEnforcementMiddleware(engagement_cap_usd=10.0, spend_provider=lambda _k: 0.0)
    calls = []

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    with pytest.raises(BudgetSpendUntracked):
        mw.wrap_model_call(_Req(), lambda request: calls.append(request))
    assert calls == []


def test_budget_allows_normally_billed_credential(monkeypatch):
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "anthropic_api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "a" * 40)
    mw = BudgetEnforcementMiddleware(engagement_cap_usd=10.0, spend_provider=lambda _k: 0.0)

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())


def test_budget_allows_priced_abliteratedllm_credential(monkeypatch):
    # AbliteratedLLM routes carry published per-token prices in
    # config/litellm.yaml, so the method must NOT be treated as
    # spend-untracked: a configured cap stays enforceable.
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "abliteratedllm_api")
    monkeypatch.setenv("ABLITERATEDLLM_API_KEY", "abliterated-" + "a" * 32)
    queries = []
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda key: queries.append(key) or 0.0,
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())
    assert queries == ["engagement:test"]


def test_budget_disabled_allows_unpriced_credential(monkeypatch):
    monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "cheaper_inference_api")
    monkeypatch.setenv("CHEAPER_INFERENCE_API_KEY", "ci_live_" + "a" * 24)
    mw = BudgetEnforcementMiddleware(engagement_cap_usd=0.0)

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())


def test_per_agent_cap_is_checked_separately():
    spends = {"engagement:test": 1.0, "agent:test:recon": 50.0}
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=100.0,
        per_agent_cap_usd=10.0,
        spend_provider=spends.get,
    )

    class _RT:
        agent_name = "recon"

    class _Req:
        state = {"engagement_name": "test"}
        runtime = _RT()

    with pytest.raises(BudgetExceeded) as exc_info:
        mw._enforce(_Req())
    assert exc_info.value.scope == "agent"


def test_provider_exception_is_swallowed():
    def _bad_provider(_k):
        raise RuntimeError("postgres down")

    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=_bad_provider,
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())


def test_soft_warn_fires_once_per_scope():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        soft_warn_at_pct=0.5,
        spend_provider=lambda _k: 7.0,
    )

    class _Req:
        state = {"engagement_name": "test"}
        runtime = None

    mw._enforce(_Req())
    assert "engagement:engagement:test" in mw._warned_scopes
    mw._enforce(_Req())
    assert len(mw._warned_scopes) == 1


# ------------------------------------------------------------- _tag_request


class _OverridableReq:
    """Minimal ModelRequest stand-in supporting ``override``."""

    def __init__(self, model_settings: dict | None = None):
        self.state = {"engagement_name": "test"}

        class _RT:
            agent_name = "recon"

        self.runtime = _RT()
        self.model_settings = model_settings or {}

    def override(self, **kwargs):
        new = _OverridableReq()
        new.__dict__.update(self.__dict__)
        new.__dict__.update(kwargs)
        return new


def test_tag_request_injects_scope_tags():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda _k: 0.0,
    )
    tagged = mw._tag_request(_OverridableReq())
    tags = tagged.model_settings["extra_body"]["metadata"]["tags"]
    assert tags == ["engagement:test", "agent:test:recon"]


def test_tag_request_preserves_existing_extra_body_and_tags():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda _k: 0.0,
    )
    req = _OverridableReq(
        model_settings={
            "extra_body": {
                "thinking": {"type": "enabled"},
                "metadata": {"tags": ["custom-tag"], "trace_id": "t-1"},
            },
            "max_tokens": 64,
        }
    )
    tagged = mw._tag_request(req)
    extra = tagged.model_settings["extra_body"]
    assert extra["thinking"] == {"type": "enabled"}
    assert tagged.model_settings["max_tokens"] == 64
    assert extra["metadata"]["trace_id"] == "t-1"
    assert extra["metadata"]["tags"] == [
        "custom-tag",
        "engagement:test",
        "agent:test:recon",
    ]
    # the original request's settings must not be mutated in place
    assert req.model_settings["extra_body"]["metadata"]["tags"] == ["custom-tag"]


def test_tag_request_is_idempotent():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=10.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda _k: 0.0,
    )
    once = mw._tag_request(_OverridableReq())
    twice = mw._tag_request(once)
    tags = twice.model_settings["extra_body"]["metadata"]["tags"]
    assert tags == ["engagement:test", "agent:test:recon"]


def test_tag_request_noop_when_disabled():
    mw = BudgetEnforcementMiddleware(
        engagement_cap_usd=0.0,
        per_agent_cap_usd=0.0,
        spend_provider=lambda _k: 0.0,
    )
    req = _OverridableReq()
    assert mw._tag_request(req) is req
