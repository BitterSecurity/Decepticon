"""Tests for EngagementContextMiddleware — engagement and benchmark inject paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import AgentState
from langchain.agents.factory import _resolve_schemas
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph

from decepticon.middleware.engagement import (
    EngagementContextMiddleware,
    EngagementContextState,
    _benchmark_mode_active,
    _hydrate_engagement_state,
    _resolve_workspace_path,
)
from decepticon.middleware.opplan import OPPLANState


class _FakeRequest:
    """Minimal duck-typed stand-in for the AgentMiddleware request object."""

    def __init__(
        self,
        state: dict[str, Any] | None = None,
        system_message: SystemMessage | None = None,
    ) -> None:
        self.state = state or {}
        self.system_message = system_message

    def override(self, system_message: SystemMessage) -> "_FakeRequest":
        return _FakeRequest(state=self.state, system_message=system_message)


def _flatten(message: SystemMessage | None) -> str:
    """Return the concatenated text of a SystemMessage regardless of content shape."""
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text", "")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


@pytest.fixture
def middleware() -> EngagementContextMiddleware:
    return EngagementContextMiddleware()


@pytest.fixture(autouse=True)
def _clear_benchmark_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default each test to BENCHMARK_MODE unset; tests opt-in via monkeypatch.setenv."""
    monkeypatch.delenv("BENCHMARK_MODE", raising=False)


# ── env-var helper ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "anything"])
def test_benchmark_mode_active_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BENCHMARK_MODE", value)
    assert _benchmark_mode_active() is True


@pytest.mark.parametrize("value", ["", "0", "false", "FALSE", "no", "off", "  "])
def test_benchmark_mode_active_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BENCHMARK_MODE", value)
    assert _benchmark_mode_active() is False


def test_benchmark_mode_active_unset() -> None:
    # autouse fixture deletes the env; must be False.
    assert _benchmark_mode_active() is False


@pytest.mark.parametrize(
    "schema",
    [
        OPPLANState,
        EngagementContextState,
        _resolve_schemas({AgentState, OPPLANState, EngagementContextState})[0],
    ],
)
def test_engagement_name_reducer_handles_concurrent_updates(schema) -> None:
    def set_name(_state):
        return {"engagement_name": "demo-engagement"}

    def keep_name(_state):
        return {"engagement_name": None}

    graph = StateGraph(schema)
    graph.add_node("set_name", set_name)
    graph.add_node("keep_name", keep_name)
    graph.add_edge(START, "set_name")
    graph.add_edge(START, "keep_name")
    graph.add_edge("set_name", END)
    graph.add_edge("keep_name", END)

    result = graph.compile().invoke({"messages": []})

    assert result["engagement_name"] == "demo-engagement"


@pytest.mark.parametrize(
    "schema",
    [
        OPPLANState,
        EngagementContextState,
        _resolve_schemas({AgentState, OPPLANState, EngagementContextState})[0],
    ],
)
def test_workspace_path_reducer_handles_concurrent_updates(schema) -> None:
    """Regression for #153 — parallel objectives must not trigger
    INVALID_CONCURRENT_GRAPH_UPDATE on workspace_path."""

    def set_workspace(_state):
        return {"workspace_path": "/workspace"}

    def keep_workspace(_state):
        return {"workspace_path": None}

    graph = StateGraph(schema)
    graph.add_node("set_workspace", set_workspace)
    graph.add_node("keep_workspace", keep_workspace)
    graph.add_edge(START, "set_workspace")
    graph.add_edge(START, "keep_workspace")
    graph.add_edge("set_workspace", END)
    graph.add_edge("keep_workspace", END)

    result = graph.compile().invoke({"messages": []})

    assert result["workspace_path"] == "/workspace"


# Every launcher-/harness-set EngagementContextState channel must survive a
# parallel fan-out (two subagent branches writing the same inherited value in
# one superstep). Without a reducer LangGraph raises INVALID_CONCURRENT_GRAPH_
# UPDATE — this is the bug that crashed bugclaw's parallel hunter dispatch on
# `language`. Each branch carries the same value, so last-write-wins converges.
_CONVERGING_ENGAGEMENT_FIELDS = [
    ("language", "ko"),
    ("target_type", "web_url"),
    ("target_value", "https://target.example"),
    ("authorization_confirmed", True),
    ("target_url", "http://target.example"),
    ("target_extra_ports", {22: 2222}),
    ("vulnerability_tags", ["xss", "sqli"]),
    ("flag_format", "flag{...}"),
    ("mission_brief", "Demo challenge"),
]


@pytest.mark.parametrize(("field", "value"), _CONVERGING_ENGAGEMENT_FIELDS)
@pytest.mark.parametrize(
    "schema",
    [
        EngagementContextState,
        _resolve_schemas({AgentState, OPPLANState, EngagementContextState})[0],
    ],
)
def test_engagement_context_fields_survive_parallel_fanout(schema, field, value) -> None:
    def branch_a(_state):
        return {field: value}

    def branch_b(_state):
        return {field: value}

    graph = StateGraph(schema)
    graph.add_node("branch_a", branch_a)
    graph.add_node("branch_b", branch_b)
    graph.add_edge(START, "branch_a")
    graph.add_edge(START, "branch_b")
    graph.add_edge("branch_a", END)
    graph.add_edge("branch_b", END)

    # Must not raise INVALID_CONCURRENT_GRAPH_UPDATE; value converges.
    result = graph.compile().invoke({"messages": []})
    assert result[field] == value


_CONVERGING_KG_FIELDS = [
    ("kg_engagement", "eng-scope"),
    ("kg_revision", "rev-7"),
    ("kg_summary", "## graph state"),
]


@pytest.mark.parametrize(("field", "value"), _CONVERGING_KG_FIELDS)
def test_kg_state_fields_survive_parallel_fanout(field, value) -> None:
    """KG slot runs in analyst / contract_auditor / ad_operator — dispatching
    two in parallel writes these channels concurrently; the reducer must let
    them converge instead of tripping INVALID_CONCURRENT_GRAPH_UPDATE."""
    from decepticon.middleware.kg_internal.state import KGState

    def branch_a(_state):
        return {field: value}

    def branch_b(_state):
        return {field: value}

    graph = StateGraph(KGState)
    graph.add_node("branch_a", branch_a)
    graph.add_node("branch_b", branch_b)
    graph.add_edge(START, "branch_a")
    graph.add_edge(START, "branch_b")
    graph.add_edge("branch_a", END)
    graph.add_edge("branch_b", END)

    result = graph.compile().invoke({"messages": []})
    assert result[field] == value


def test_reduce_converging_value_semantics() -> None:
    from decepticon.middleware.state_reducers import reduce_converging_value

    assert reduce_converging_value("old", "new") == "new"
    assert reduce_converging_value("keep", None) == "keep"
    assert reduce_converging_value(None, "set") == "set"
    # generic over container types (dict / list channels)
    assert reduce_converging_value({22: 1}, {22: 2}) == {22: 2}
    assert reduce_converging_value(["a"], None) == ["a"]


# ── inject paths ───────────────────────────────────────────────────────


def test_no_injection_returns_request_unchanged(
    middleware: EngagementContextMiddleware,
) -> None:
    req = _FakeRequest(state={})
    result = middleware._inject(req)
    assert result is req
    assert result.system_message is None


def test_engagement_only_injection(middleware: EngagementContextMiddleware) -> None:
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": "/workspace"},
    )
    result = middleware._inject(req)

    assert result is not req  # override produced a fresh request
    text = _flatten(result.system_message)
    assert "Workspace slug: blue-falcon" in text
    assert "Workspace root: /workspace" in text
    assert "BENCHMARK MODE" not in text  # benchmark section absent


def test_autohunt_injection_uses_only_declared_target(
    middleware: EngagementContextMiddleware,
) -> None:
    req = _FakeRequest(
        state={
            "target_type": "web_url",
            "target_value": "https://target.example",
            "authorization_confirmed": True,
        },
    )

    text = _flatten(middleware._inject(req).system_message)

    assert "Declared target: https://target.example" in text
    assert "Declared target type: web_url" in text
    assert "Authorization: confirmed" in text
    assert "Do not infer related domains" in text
    assert "BENCHMARK MODE" not in text


def test_engagement_injection_honors_custom_workspace(
    middleware: EngagementContextMiddleware,
) -> None:
    """Multi-tenant launchers mount engagements under a non-default
    root; the injection must name the resolved workspace, not a hardcoded
    ``/workspace`` (regression: the path was previously hardcoded)."""
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": "/srv/engagements/bf"},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace root: /srv/engagements/bf" in text
    assert "Treat /srv/engagements/bf as the only engagement directory" in text
    assert "/srv/engagements/bf/plan/" in text
    # The stale default must not leak into a custom-root engagement.
    assert "/workspace" not in text


def test_benchmark_mode_env_off_does_not_inject_challenge_context(
    middleware: EngagementContextMiddleware,
) -> None:
    """Even with full challenge state, no inject when BENCHMARK_MODE is unset."""
    req = _FakeRequest(
        state={
            "target_url": "http://host.docker.internal:8080",
            "vulnerability_tags": ["sqli"],
            "flag_format": "FLAG{<64-char-hex>}",
            "mission_brief": "Test challenge",
        },
    )
    result = middleware._inject(req)

    # No engagement_name and benchmark off → return original request.
    assert result is req


def test_benchmark_mode_injects_per_challenge_context_only(
    monkeypatch: pytest.MonkeyPatch,
    middleware: EngagementContextMiddleware,
) -> None:
    """BENCHMARK_MODE=1 injects per-challenge context only.

    The benchmark playbook (Rule 8/9 suspension, OPPLAN structure, SHORT-CIRCUIT,
    cross-domain skill paths) lives in /skills/benchmark/SKILL.md — middleware
    must NOT inject any of it.
    """
    monkeypatch.setenv("BENCHMARK_MODE", "1")
    req = _FakeRequest(
        state={
            "target_url": "http://x",
            "vulnerability_tags": ["idor"],
            "flag_format": "FLAG{...}",
            "mission_brief": "test",
        }
    )
    result = middleware._inject(req)

    text = _flatten(result.system_message)
    # Playbook strings must NOT appear — they belong in /skills/benchmark/SKILL.md.
    assert "[BENCHMARK MODE — engaged]" not in text
    assert "Rule 8 (Startup Required)" not in text
    assert "Rule 9 (Final Report)" not in text
    assert "RECON objective" not in text
    assert "/skills/standard/exploit/web/" not in text
    assert "/skills/benchmark/SKILL.md" not in text
    # Per-challenge context IS injected.
    assert "## CTF Benchmark Challenge" in text
    assert "**Target URL:** http://x" in text
    assert "**Vulnerability tags:** idor" in text
    assert "**Flag format:** FLAG{...}" in text
    assert "**Mission brief:** test" in text


def test_benchmark_mode_full_context(
    monkeypatch: pytest.MonkeyPatch,
    middleware: EngagementContextMiddleware,
) -> None:
    monkeypatch.setenv("BENCHMARK_MODE", "1")
    req = _FakeRequest(
        state={
            "engagement_name": "benchmark-XBEN-001-24",
            "workspace_path": "/workspace/benchmark-XBEN-001-24",
            "target_url": "http://host.docker.internal:33001",
            "target_extra_ports": {},
            "vulnerability_tags": ["sqli", "auth-bypass"],
            "flag_format": "FLAG{<64-char-hex>}",
            "mission_brief": "Login Form SQLi — bypass authentication",
        },
    )
    result = middleware._inject(req)

    text = _flatten(result.system_message)
    # engagement section
    assert "Workspace slug: benchmark-XBEN-001-24" in text
    # per-challenge context
    assert "## CTF Benchmark Challenge" in text
    assert "**Target URL:** http://host.docker.internal:33001" in text
    assert "Attack ONLY this URL" in text
    assert "**Vulnerability tags:** sqli, auth-bypass" in text
    assert "**Flag format:** FLAG{<64-char-hex>}" in text
    assert "**Mission brief:** Login Form SQLi — bypass authentication" in text
    # benchmark playbook must NOT be in middleware output
    assert "[BENCHMARK MODE — engaged]" not in text
    assert "/skills/standard/exploit/web/" not in text
    # engagement section comes before benchmark per-challenge section
    assert text.index("Workspace slug:") < text.index("## CTF Benchmark Challenge")


def test_benchmark_extra_ports(
    monkeypatch: pytest.MonkeyPatch,
    middleware: EngagementContextMiddleware,
) -> None:
    monkeypatch.setenv("BENCHMARK_MODE", "1")
    req = _FakeRequest(
        state={
            "target_url": "http://host.docker.internal:33001",
            "target_extra_ports": {22: 2222, 3306: 33060},
            "vulnerability_tags": ["sqli"],
        },
    )
    result = middleware._inject(req)

    text = _flatten(result.system_message)
    assert "**Additional services:**" in text
    assert "**SSH:** host.docker.internal:2222 (internal port 22)" in text
    assert "**Port 3306:** host.docker.internal:33060" in text


def test_benchmark_extra_ports_empty_does_not_emit_section(
    monkeypatch: pytest.MonkeyPatch,
    middleware: EngagementContextMiddleware,
) -> None:
    monkeypatch.setenv("BENCHMARK_MODE", "1")
    req = _FakeRequest(
        state={
            "target_url": "http://host.docker.internal:33001",
            "target_extra_ports": {},
        },
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)
    assert "Additional services" not in text


def test_appended_to_existing_system_message(
    monkeypatch: pytest.MonkeyPatch,
    middleware: EngagementContextMiddleware,
) -> None:
    """When the request already has a system message, content_blocks are extended."""
    monkeypatch.setenv("BENCHMARK_MODE", "1")
    req = _FakeRequest(
        state={"engagement_name": "demo", "workspace_path": "/workspace"},
        system_message=SystemMessage(content="ORIGINAL_PROMPT_BODY"),
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)
    # original content is preserved; addendum is appended.
    assert "ORIGINAL_PROMPT_BODY" in text
    assert "Workspace slug: demo" in text
    assert "## CTF Benchmark Challenge" in text
    assert text.index("ORIGINAL_PROMPT_BODY") < text.index("Workspace slug")


def _write_deconfliction(workspace: Path, payload: dict[str, Any]) -> None:
    plan_dir = workspace / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "deconfliction.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def test_deconfliction_identifiers_appear_in_injection(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_deconfliction(
        tmp_path,
        {
            "engagement_name": "blue-falcon",
            "deconfliction_code": "ECHO-9",
            "identifiers": [
                {"type": "source-ip", "value": "10.4.4.4", "description": "jump host"},
                {"type": "user-agent", "value": "decepticon/1.0"},
            ],
        },
    )
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Deconfliction code: ECHO-9" in text
    assert "source-ip: 10.4.4.4" in text
    assert "user-agent: decepticon/1.0" in text
    assert text.index("Workspace slug:") < text.index("Deconfliction code: ECHO-9")


def test_deconfliction_absent_degrades_cleanly(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: blue-falcon" in text
    assert "Deconfliction" not in text


def test_deconfliction_malformed_json_degrades_cleanly(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "deconfliction.json").write_text("{not valid json", encoding="utf-8")

    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: blue-falcon" in text
    assert "Deconfliction" not in text


def test_deconfliction_empty_identifiers_emit_no_block(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_deconfliction(
        tmp_path,
        {"engagement_name": "blue-falcon", "deconfliction_code": "", "identifiers": []},
    )
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: blue-falcon" in text
    assert "Deconfliction" not in text


def test_deconfliction_skipped_without_engagement_slug(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_deconfliction(
        tmp_path,
        {"engagement_name": "blue-falcon", "deconfliction_code": "ECHO-9"},
    )
    req = _FakeRequest(state={"workspace_path": str(tmp_path)})
    result = middleware._inject(req)

    assert result is req


# ── runnable-config hydration ──────────────────────────────────────────


def test_hydrate_pulls_engagement_from_configurable_when_state_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """before_agent must copy engagement_name + workspace_path from config to state."""
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"engagement_name": "from-launcher", "workspace_path": "/workspace"},
    )

    updates = _hydrate_engagement_state({})

    assert updates == {
        "engagement_name": "from-launcher",
        "workspace_path": "/workspace",
    }


def test_hydrate_pulls_autohunt_context_from_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {
            "target_type": "web_url",
            "target_value": "https://target.example",
            "authorization_confirmed": True,
        },
    )

    assert _hydrate_engagement_state({}) == {
        "target_type": "web_url",
        "target_value": "https://target.example",
        "authorization_confirmed": True,
    }


def test_hydrate_is_idempotent_when_state_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If state already carries the field, configurable is ignored for that field."""
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"engagement_name": "from-launcher", "workspace_path": "/workspace"},
    )

    updates = _hydrate_engagement_state(
        {"engagement_name": "already-on-state", "workspace_path": "/workspace"}
    )

    assert updates is None


def test_hydrate_partial_when_only_one_field_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hydrates only the missing field; preserves the one already on state."""
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"engagement_name": "from-launcher", "workspace_path": "/workspace"},
    )

    updates = _hydrate_engagement_state({"engagement_name": "kept-on-state"})

    assert updates == {"workspace_path": "/workspace"}


def test_hydrate_returns_none_when_neither_state_nor_configurable_have_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        dict,
    )

    assert _hydrate_engagement_state({}) is None


def test_hydrate_ignores_non_string_configurable_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: configurable values that are not non-empty strings are ignored."""
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"engagement_name": 123, "workspace_path": ""},
    )

    assert _hydrate_engagement_state({}) is None


def test_resolve_workspace_path_prefers_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"workspace_path": "/workspace/from-config"},
    )

    assert _resolve_workspace_path({"workspace_path": "/workspace/from-state"}) == (
        "/workspace/from-state"
    )


def test_resolve_workspace_path_falls_back_to_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"workspace_path": "/workspace/from-config"},
    )

    assert _resolve_workspace_path({}) == "/workspace/from-config"


def test_resolve_workspace_path_defaults_when_neither_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        dict,
    )

    assert _resolve_workspace_path({}) == "/workspace"


def test_benchmark_with_missing_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
    middleware: EngagementContextMiddleware,
) -> None:
    """Empty optional fields are silently skipped — only non-empty pieces appear."""
    monkeypatch.setenv("BENCHMARK_MODE", "1")
    req = _FakeRequest(state={"target_url": "http://x"})
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "**Target URL:** http://x" in text
    # No tags / flag_format / brief sections.
    assert "**Vulnerability tags:**" not in text
    assert "**Flag format:**" not in text
    assert "**Mission brief:**" not in text


# ── Per-run language override ─────────────────────────────────────────────


def test_hydrate_pulls_language_from_configurable_when_state_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-tenant launcher path: config.configurable.language flows into state."""
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"language": "ko"},
    )

    updates = _hydrate_engagement_state({})

    assert updates == {"language": "ko"}


def test_hydrate_does_not_overwrite_existing_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State-set language wins over a different value in configurable."""
    monkeypatch.setattr(
        "decepticon.middleware.engagement._configurable_from_runnable_config",
        lambda: {"language": "en"},
    )

    updates = _hydrate_engagement_state({"language": "ko"})

    assert updates is None


def test_inject_appends_language_policy_when_state_carries_language(
    middleware: EngagementContextMiddleware,
) -> None:
    """A run with state.language=ko produces a LANGUAGE_POLICY system block."""
    req = _FakeRequest(state={"language": "ko"})
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "<LANGUAGE_POLICY>" in text
    assert "Korean" in text


def test_inject_skips_language_policy_for_english_or_empty(
    middleware: EngagementContextMiddleware,
) -> None:
    """en / empty → no override block; default prompt policy applies."""
    req_en = _FakeRequest(state={"language": "en"})
    assert middleware._inject(req_en).system_message is None

    req_empty = _FakeRequest(state={"language": ""})
    assert middleware._inject(req_empty).system_message is None


def test_inject_combines_engagement_slug_and_language_in_one_message(
    middleware: EngagementContextMiddleware,
) -> None:
    """Both injections compose into a single SystemMessage in order."""
    req = _FakeRequest(state={"engagement_name": "acme", "language": "ja"})
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: acme" in text
    assert "<LANGUAGE_POLICY>" in text
    assert "Japanese" in text


def test_build_language_policy_helper_handles_aliases_and_no_op_cases() -> None:
    """The shared helper is the single source of truth for both paths."""
    from decepticon.agents.prompts import build_language_policy

    # No-op codes
    assert build_language_policy("") is None
    assert build_language_policy("en") is None
    assert build_language_policy("EN") is None

    # ISO code → full name
    policy_ko = build_language_policy("ko")
    assert policy_ko is not None and "Korean" in policy_ko

    # Country-code alias resolved (jp → ja → Japanese)
    policy_jp = build_language_policy("jp")
    assert policy_jp is not None and "Japanese" in policy_jp

    # Wenyan special mode
    policy_wenyan = build_language_policy("wenyan")
    assert policy_wenyan is not None and "文言文" in policy_wenyan


# ── Provided-credentials injection (greybox) ────────────────────────────


def _write_credentials(workspace: Path, payload: dict[str, Any]) -> None:
    plan_dir = workspace / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "credentials.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def test_credentials_form_login_appears_in_injection(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_credentials(
        tmp_path,
        {
            "credentials": [
                {
                    "id": "admin",
                    "label": "admin account",
                    "target": "http://app.example.com",
                    "login_method": "form",
                    "login_url": "http://app.example.com/login",
                    "username": "alice",
                    "password": "s3cret",
                }
            ]
        },
    )
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "[Target credentials — provided by operator]" in text
    assert "id: admin" in text
    assert "login_url: http://app.example.com/login" in text
    assert "username: alice" in text
    assert "password: s3cret" in text
    assert text.index("Workspace slug:") < text.index("id: admin")


def test_credentials_token_only_renders_headers(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_credentials(
        tmp_path,
        {
            "credentials": [
                {
                    "id": "api",
                    "login_method": "bearer",
                    "headers": {"Authorization": "Bearer deadbeef"},
                }
            ]
        },
    )
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "id: api" in text
    assert "headers: Authorization: Bearer deadbeef" in text


def test_credentials_absent_degrades_cleanly(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: blue-falcon" in text
    assert "Target credentials" not in text


def test_credentials_malformed_json_degrades_cleanly(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "credentials.json").write_text("{not valid json", encoding="utf-8")

    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: blue-falcon" in text
    assert "Target credentials" not in text


def test_credentials_empty_list_emits_no_block(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_credentials(tmp_path, {"credentials": []})
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Workspace slug: blue-falcon" in text
    assert "Target credentials" not in text


def test_credentials_entry_without_id_skipped(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_credentials(
        tmp_path,
        {"credentials": [{"username": "nobody"}, {"id": "real", "username": "bob"}]},
    )
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "id: real" in text
    assert "username: bob" in text
    assert "username: nobody" not in text


def test_credentials_skipped_without_engagement_slug(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_credentials(tmp_path, {"credentials": [{"id": "admin"}]})
    req = _FakeRequest(state={"workspace_path": str(tmp_path)})
    result = middleware._inject(req)

    assert result is req


def test_credentials_and_deconfliction_coexist(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_deconfliction(
        tmp_path,
        {"engagement_name": "blue-falcon", "deconfliction_code": "ECHO-9"},
    )
    _write_credentials(tmp_path, {"credentials": [{"id": "admin", "username": "alice"}]})
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    result = middleware._inject(req)
    text = _flatten(result.system_message)

    assert "Deconfliction code: ECHO-9" in text
    assert "id: admin" in text
    assert text.index("Deconfliction code: ECHO-9") < text.index("id: admin")


def test_credentials_mtime_refresh_picks_up_edit(
    tmp_path: Path,
    middleware: EngagementContextMiddleware,
) -> None:
    _write_credentials(tmp_path, {"credentials": [{"id": "admin"}]})
    req = _FakeRequest(
        state={"engagement_name": "blue-falcon", "workspace_path": str(tmp_path)},
    )
    first = _flatten(middleware._inject(req).system_message)
    assert "id: admin" in first and "id: editor" not in first

    # Bump mtime past the cached entry so the loader re-reads the file.
    import os
    import time

    path = tmp_path / "plan" / "credentials.json"
    _write_credentials(tmp_path, {"credentials": [{"id": "editor"}]})
    future = time.time() + 10
    os.utime(path, (future, future))

    second = _flatten(middleware._inject(req).system_message)
    assert "id: editor" in second
