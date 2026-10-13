"""Regression tests for ModelOverrideMiddleware.

The middleware re-binds the agent's LLM to a user-supplied LiteLLM model
id at call time. Issue #186 traced an ``APIConnectionError`` for every
``/model <id>`` override to ``_build_proxied_llm`` constructing
``ProxyConfig()`` with bare pydantic defaults (``url="http://localhost:4000"``)
instead of resolving from agent config the way :class:`LLMFactory`
does — so the override request was sent to the langgraph container
itself, where nothing was listening.

These tests pin the resolved-config behaviour so the regression cannot
recur, plus the temperature handling for the Opus 4.x family which the
factory drops at the ``temperature``-rejecting upstream API.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from decepticon.middleware.model_override import (
    ModelOverrideMiddleware,
    ModelOverridesState,
    _build_proxied_llm,
    _read_override,
)

# ── Test helpers ────────────────────────────────────────────────────────


@pytest.fixture
def proxy_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set the proxy env vars the agent runs under in the langgraph container.

    The container's hostname for the LiteLLM proxy is ``litellm`` on the
    decepticon-net Docker network; the bare ``ProxyConfig()`` default of
    ``localhost:4000`` does not work inside a container — issue #186.
    """
    env = {
        "DECEPTICON_LLM__PROXY_URL": "http://litellm:4000",
        "DECEPTICON_LLM__PROXY_API_KEY": "sk-decepticon-master",
        "DECEPTICON_LLM__TIMEOUT": "120",
        "DECEPTICON_LLM__MAX_RETRIES": "2",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


def _original_chat_model(temperature: float | None = 0.4) -> ChatOpenAI:
    """A stand-in for the LLM the agent was constructed with.

    Only ``temperature`` is read by the override path; the other fields
    must be present so pydantic accepts the construction.
    """
    return ChatOpenAI(
        model="openai/gpt-5.5",
        temperature=temperature,
        api_key="sk-test-original",
        base_url="http://localhost:4000",
    )


# ── Issue #186 regressions ──────────────────────────────────────────────


class TestBuildProxiedLLMResolvesConfig:
    """``_build_proxied_llm`` must resolve proxy config the same way
    ``LLMFactory`` does — not fall back to bare ``ProxyConfig()`` defaults.
    """

    def test_uses_resolved_proxy_url_from_env(self, proxy_env: dict[str, str]) -> None:
        """Override must hit the resolved ``DECEPTICON_LLM__PROXY_URL``,
        not the pydantic default ``http://localhost:4000``. Inside the
        langgraph container, ``localhost:4000`` is the container itself
        — no listener — and was the root cause of the original report.
        """
        bound = _build_proxied_llm("openai/gpt-5.5", _original_chat_model())

        assert bound.openai_api_base == proxy_env["DECEPTICON_LLM__PROXY_URL"], (
            "_build_proxied_llm must resolve proxy URL through agent config, "
            "not fall back to ProxyConfig() defaults — see issue #186."
        )
        assert bound.openai_api_base != "http://localhost:4000"

    def test_uses_resolved_api_key_from_env(self, proxy_env: dict[str, str]) -> None:
        """Same path: api_key is sourced from config, not pydantic defaults.

        Bare default would be ``sk-decepticon-master`` which happens to
        match a stock proxy install — the bug here is that the *whole*
        config object is unresolved, so a non-default key would also be
        ignored. Use the explicit env value to confirm the resolution
        happened, not a coincidence with the default literal.
        """
        bound = _build_proxied_llm("openai/gpt-5.5", _original_chat_model())

        # ChatOpenAI wraps the api_key in SecretStr; unwrap to compare.
        api_key = bound.openai_api_key.get_secret_value()
        assert api_key == proxy_env["DECEPTICON_LLM__PROXY_API_KEY"]

    def test_routes_for_every_override_target_not_just_anthropic(
        self, proxy_env: dict[str, str]
    ) -> None:
        """The bug reproduces with any override target, not just the
        ``auth/claude-opus-4-7`` the original reporter hit. Walk through
        a range of LiteLLM ids to make sure the resolution path is
        target-agnostic.
        """
        targets = [
            "auth/claude-opus-4-7",
            "anthropic/claude-sonnet-4-6",
            "openai/gpt-5.5",
            "groq/llama-3.3-70b-versatile",
            "openrouter/anthropic/claude-haiku-4-5",
        ]
        for target in targets:
            bound = _build_proxied_llm(target, _original_chat_model())
            assert bound.openai_api_base == proxy_env["DECEPTICON_LLM__PROXY_URL"], (
                f"override target {target!r} did not resolve proxy URL"
            )
            assert bound.model_name == target

    def test_proxy_url_default_falls_through_when_env_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without env overrides we still get a sane default — the
        config layer's ``http://localhost:4000`` baseline. The bug was
        not "default value chosen" but "config layer never consulted".
        Belt-and-braces: confirm we do go through the config, even when
        the resolved value happens to equal the bare default.
        """
        # Strip any test-host env so we observe the config-layer default,
        # not a leak from the developer's shell.
        for var in (
            "DECEPTICON_LLM__PROXY_URL",
            "DECEPTICON_LLM__PROXY_API_KEY",
            "DECEPTICON_LLM__TIMEOUT",
            "DECEPTICON_LLM__MAX_RETRIES",
        ):
            monkeypatch.delenv(var, raising=False)

        bound = _build_proxied_llm("openai/gpt-5.5", _original_chat_model())

        # The default config baseline matches localhost; the assertion
        # is that we got a non-empty, sensible URL — not the *pathway*.
        # The earlier test_routes_for_every_override_target with env set
        # is the positive proof that resolution actually happened.
        assert bound.openai_api_base
        assert bound.openai_api_base.endswith(":4000")


class TestBuildProxiedLLMTemperatureHandling:
    """Opus 4.x family rejects ``temperature`` at the upstream Anthropic
    API regardless of the proxy path. ``LLMFactory._create_chat_model``
    drops it via ``_model_drops_temperature``; the override path must
    do the same for parity (issue #186 secondary finding).
    """

    @pytest.mark.parametrize(
        "model_id",
        [
            "auth/claude-opus-4-7",
            "anthropic/claude-opus-4-7",
            "openrouter/anthropic/claude-opus-4-7",
        ],
    )
    def test_drops_temperature_for_opus_4x_family(
        self, proxy_env: dict[str, str], model_id: str
    ) -> None:
        bound = _build_proxied_llm(model_id, _original_chat_model(temperature=0.4))
        assert bound.temperature is None, (
            f"Opus 4.x must drop temperature, got {bound.temperature!r} for {model_id}"
        )

    @pytest.mark.parametrize(
        "model_id",
        [
            "auth/claude-sonnet-4-6",
            "openai/gpt-5.5",
            "groq/llama-3.3-70b-versatile",
        ],
    )
    def test_preserves_temperature_for_other_models(
        self, proxy_env: dict[str, str], model_id: str
    ) -> None:
        bound = _build_proxied_llm(model_id, _original_chat_model(temperature=0.4))
        assert bound.temperature == 0.4

    def test_missing_temperature_on_original_does_not_crash(
        self, proxy_env: dict[str, str]
    ) -> None:
        """Some BaseChatModel subclasses do not expose ``temperature``.
        ``getattr(original, "temperature", None)`` is the protective
        access; this pins it so a future refactor does not regress to
        ``original.temperature`` and break for those models.
        """

        class _NoTemp:
            """BaseChatModel-shaped object without a ``temperature`` attr."""

            pass

        bound = _build_proxied_llm("openai/gpt-5.5", _NoTemp())  # type: ignore[arg-type]
        # No temperature passed through → ChatOpenAI's own default applies.
        # Whatever that default is, we just need to confirm we did not
        # crash and we did construct a model bound to the proxy URL.
        assert bound.openai_api_base == proxy_env["DECEPTICON_LLM__PROXY_URL"]


# ── Read-override resolution (lightweight smoke for the read path) ──────


class TestReadOverride:
    """``_read_override`` already had implicit coverage through the
    runtime tests; pin the contract here so the override key spelling
    is locked.
    """

    def test_runtime_context_takes_priority(self) -> None:
        class _Runtime:
            context = {"model_override": "openai/gpt-5.5"}

        class _Req:
            runtime = _Runtime()
            state = {"model_override": "openai/gpt-5.4"}

        assert _read_override(_Req()) == "openai/gpt-5.5"

    def test_state_used_when_runtime_absent(self) -> None:
        class _Req:
            runtime = None
            state = {"model_override": "openai/gpt-5.4"}

        assert _read_override(_Req()) == "openai/gpt-5.4"

    def test_per_role_map_targets_the_matching_role(self) -> None:
        class _Runtime:
            context = {"model_overrides": {"soundwave": "auth/claude-sonnet-5"}}

        class _Req:
            runtime = _Runtime()
            state: dict[str, Any] = {}

        assert _read_override(_Req(), "soundwave") == "auth/claude-sonnet-5"
        # a role NOT in the map (and no global) → no override
        assert _read_override(_Req(), "recon") == ""
        # no role passed → the per-role map is ignored
        assert _read_override(_Req()) == ""

    def test_per_role_map_beats_the_global_override(self) -> None:
        class _Runtime:
            context = {
                "model_overrides": {"exploit": "auth/claude-sonnet-5"},
                "model_override": "openai/gpt-5.5",
            }

        class _Req:
            runtime = _Runtime()
            state: dict[str, Any] = {}

        # exploit → its per-role model; other roles → the global override
        assert _read_override(_Req(), "exploit") == "auth/claude-sonnet-5"
        assert _read_override(_Req(), "recon") == "openai/gpt-5.5"

    def test_per_role_map_from_state_channel(self) -> None:
        class _Req:
            runtime = None
            state = {"model_overrides": {"soundwave": "auth/claude-sonnet-5"}}

        assert _read_override(_Req(), "soundwave") == "auth/claude-sonnet-5"

    def test_empty_string_means_no_override(self) -> None:
        class _Runtime:
            context = {"model_override": ""}

        class _Req:
            runtime = _Runtime()
            state: dict[str, Any] = {}

        assert _read_override(_Req()) == ""

    def test_whitespace_only_means_no_override(self) -> None:
        class _Runtime:
            context = {"model_override": "   "}

        class _Req:
            runtime = _Runtime()
            state: dict[str, Any] = {}

        assert _read_override(_Req()) == ""


# ── Middleware wiring (smoke — no LLM calls) ────────────────────────────


class TestModelOverrideMiddlewareWiring:
    """Make sure the middleware short-circuits to the original handler
    when no override is set so a user with no ``/model`` command gets
    the baked-in primary unchanged.
    """

    def test_no_override_passes_through(self) -> None:
        mw = ModelOverrideMiddleware()
        seen: list[Any] = []

        def handler(request):
            seen.append(request)
            return "passthrough"

        class _Req:
            runtime = None
            state: dict[str, Any] = {}

        assert mw.wrap_model_call(_Req(), handler) == "passthrough"
        assert seen, "handler must be invoked when no override is set"


class TestWrapModelCallActiveOverride:
    def test_active_override_rebinds_and_calls_handler(self, proxy_env: dict[str, str]) -> None:
        mw = ModelOverrideMiddleware()
        original = _original_chat_model()

        class _OverrideReq:
            class runtime:
                context = {"model_override": "openai/gpt-5.5"}

            state: dict[str, Any] = {}
            model = original
            override_called = False
            last_override_model: Any = None

            def override(self, *, model: Any) -> "_OverrideReq":
                self.override_called = True
                self.last_override_model = model
                return self

        req = _OverrideReq()
        handler_calls: list[Any] = []

        def handler(request: Any) -> str:
            handler_calls.append(request)
            return "RESULT"

        result = mw.wrap_model_call(req, handler)

        assert result == "RESULT"
        assert len(handler_calls) == 1
        assert req.override_called
        bound = req.last_override_model
        assert isinstance(bound, ChatOpenAI)
        assert bound.openai_api_base == proxy_env["DECEPTICON_LLM__PROXY_URL"]
        assert bound.model_name == "openai/gpt-5.5"

    def test_bind_failure_falls_back_to_original_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mw = ModelOverrideMiddleware()
        original = _original_chat_model()

        def _raise(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr("decepticon.middleware.model_override._build_proxied_llm", _raise)

        class _OverrideReq:
            class runtime:
                context = {"model_override": "openai/gpt-5.5"}

            state: dict[str, Any] = {}
            model = original
            override_called = False

            def override(self, *, model: Any) -> "_OverrideReq":
                self.override_called = True
                return self

        req = _OverrideReq()
        handler_calls: list[Any] = []

        def handler(request: Any) -> str:
            handler_calls.append(request)
            return "FALLBACK"

        result = mw.wrap_model_call(req, handler)

        assert result == "FALLBACK"
        assert len(handler_calls) == 1
        assert handler_calls[0] is req
        assert not req.override_called


class TestAwrapModelCallNoOverride:
    async def test_async_no_override_passes_through(self) -> None:
        mw = ModelOverrideMiddleware()

        class _Req:
            runtime = None
            state: dict[str, Any] = {}

        seen: list[Any] = []

        async def handler(request: Any) -> str:
            seen.append(request)
            return "passthrough"

        result = await mw.awrap_model_call(_Req(), handler)
        assert result == "passthrough"
        assert seen


class TestAwrapModelCallActiveOverride:
    async def test_async_active_override_rebinds(self, proxy_env: dict[str, str]) -> None:
        mw = ModelOverrideMiddleware()
        original = _original_chat_model()

        class _OverrideReq:
            class runtime:
                context = {"model_override": "groq/llama-3.3-70b-versatile"}

            state: dict[str, Any] = {}
            model = original
            override_called = False
            last_override_model: Any = None

            def override(self, *, model: Any) -> "_OverrideReq":
                self.override_called = True
                self.last_override_model = model
                return self

        req = _OverrideReq()
        handler_calls: list[Any] = []

        async def handler(request: Any) -> str:
            handler_calls.append(request)
            return "ARESULT"

        result = await mw.awrap_model_call(req, handler)

        assert result == "ARESULT"
        assert len(handler_calls) == 1
        assert req.override_called
        bound = req.last_override_model
        assert isinstance(bound, ChatOpenAI)
        assert bound.openai_api_base == proxy_env["DECEPTICON_LLM__PROXY_URL"]
        assert bound.model_name == "groq/llama-3.3-70b-versatile"

    async def test_async_bind_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mw = ModelOverrideMiddleware()
        original = _original_chat_model()

        def _raise(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr("decepticon.middleware.model_override._build_proxied_llm", _raise)

        class _OverrideReq:
            class runtime:
                context = {"model_override": "groq/llama-3.3-70b-versatile"}

            state: dict[str, Any] = {}
            model = original
            override_called = False

            def override(self, *, model: Any) -> "_OverrideReq":
                self.override_called = True
                return self

        req = _OverrideReq()
        handler_calls: list[Any] = []

        async def handler(request: Any) -> str:
            handler_calls.append(request)
            return "AFALLBACK"

        result = await mw.awrap_model_call(req, handler)

        assert result == "AFALLBACK"
        assert len(handler_calls) == 1
        assert handler_calls[0] is req
        assert not req.override_called


def test_global_override_survives_graph_input_change_and_clear() -> None:
    graph = (
        StateGraph(ModelOverridesState)
        .add_node("read", lambda state: {})
        .add_edge(START, "read")
        .add_edge("read", END)
        .compile(checkpointer=InMemorySaver())
    )
    config = {"configurable": {"thread_id": "override-lifecycle"}}
    for update, expected in [
        ({"model_override": "auth/gpt-6-luna"}, "auth/gpt-6-luna"),
        ({}, "auth/gpt-6-luna"),
        ({"model_override": "auth/gpt-6.1-sol"}, "auth/gpt-6.1-sol"),
        ({"model_override": ""}, ""),
        ({}, ""),
    ]:
        state = graph.invoke({"messages": [], **update}, config)
        assert state.get("model_override") == expected


def test_resume_updates_global_override_without_displacing_role_override() -> None:
    observed: list[tuple[str, str]] = []

    def resume_node(state: ModelOverridesState) -> dict[str, Any]:
        interrupt("Continue?")
        request = SimpleNamespace(runtime=None, state=state)
        observed.append((_read_override(request, "recon"), _read_override(request, "exploit")))
        return {}

    graph = (
        StateGraph(ModelOverridesState)
        .add_node("resume", resume_node)
        .add_edge(START, "resume")
        .add_edge("resume", END)
        .compile(checkpointer=InMemorySaver())
    )
    config = {"configurable": {"thread_id": "override-resume"}}
    for selected in ("auth/gpt-6.1-sol", ""):
        graph.invoke(
            {
                "messages": [],
                "model_override": "auth/gpt-6-luna",
                "model_overrides": {"exploit": "auth/claude-sonnet-5"},
            },
            config,
        )
        graph.invoke(Command(resume="yes", update={"model_override": selected}), config)
    assert observed == [
        ("auth/gpt-6.1-sol", "auth/claude-sonnet-5"),
        ("", "auth/claude-sonnet-5"),
    ]


@pytest.mark.parametrize(
    ("model_id", "attribute", "expected"),
    [
        ("openai/gpt-6.1-sol", "use_responses_api", True),
        ("anthropic/claude-sonnet-5-5", "disable_streaming", "tool_calling"),
        ("ollama_chat/qwen3-coder:30b", "disable_streaming", "tool_calling"),
    ],
)
def test_override_preserves_factory_protocol(
    proxy_env: dict[str, str], model_id: str, attribute: str, expected: object
) -> None:
    bound = _build_proxied_llm(model_id, _original_chat_model())
    assert getattr(bound, attribute) == expected
