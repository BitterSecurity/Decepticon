"""ACL regression tests for SkillogyMiddleware (ADR-0008).

When the Skillogy backend replaced ``FilesystemBackend`` as the default
skill-retrieval surface (PR #613), the legacy ``sources=[...]``
path-prefix ACL was not carried forward — Phase 1a spec OQ-3
explicitly deferred per-agent slicing to Phase 2. ADR-0008 reverses
that omission: the path-prefix allowlist that
``skills_sources_for(role)`` produces is enforced by the Skillogy
middleware on every ``find_skill`` / ``load_skill`` / ``traverse``
call, so the two skill backends agree on per-role visibility.

These tests pin the contract end-to-end at the middleware boundary —
they exercise the same closures the LLM tool calls hit, with a fake
backend that records the kwargs forwarded down. The Neo4j Cypher
clause is covered by the backend's own integration tests; what we
care about here is that the role context arrives at the backend
unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from decepticon.middleware.skillogy import (
    SkillogyMiddleware,
    SkillogyScopeError,
    _resolve_allowed_path_prefixes,
    maybe_install_skillogy,
)
from decepticon.middleware.skills import SkillsMiddleware


class _RecordingBackend:
    """Fake backend that captures every call's kwargs so the test can
    assert the ACL list arrived intact at the wire layer."""

    def __init__(self) -> None:
        self.find_calls: list[dict[str, Any]] = []
        self.load_calls: list[dict[str, Any]] = []
        self.traverse_calls: list[dict[str, Any]] = []

    def find_skill(self, **kwargs):
        self.find_calls.append(kwargs)
        return [
            {
                "name": "demo",
                "path": "/skills/standard/recon/demo/SKILL.md",
                "subdomain": "reconnaissance",
            }
        ]

    def load_skill(self, path, **kwargs):
        self.load_calls.append({"path": path, **kwargs})
        return {"name": "demo", "path": path, "body": "# demo"}

    def traverse(self, from_path, **kwargs):
        self.traverse_calls.append({"from_path": from_path, **kwargs})
        return []

    def query_moc_summary(self, phase, **_):  # pragma: no cover - phase block not under test
        return []


class _StubSkillsMiddleware(SkillsMiddleware):
    """Skinniest SkillsMiddleware stand-in. The real one requires a
    backend + sources at construction; the swap hook only inspects the
    *type*, so a barebones subclass suffices for the test."""

    def __init__(self) -> None:
        from langchain.agents.middleware import AgentMiddleware

        AgentMiddleware.__init__(self)


def _tools_by_name(mw):
    return {t.name: t for t in mw.tools}


# ── direct middleware: ACL list arrives at the backend on every tool call ──


def test_find_skill_forwards_allowed_path_prefixes_to_backend():
    backend = _RecordingBackend()
    prefixes = ["/skills/standard/recon/", "/skills/shared/"]
    mw = SkillogyMiddleware(
        backend=backend,
        append_policy_to_system=False,
        allowed_path_prefixes=prefixes,
    )
    _tools_by_name(mw)["find_skill"].invoke({"subdomain": "reconnaissance"})
    assert backend.find_calls, "find_skill should have hit the backend"
    assert backend.find_calls[0]["allowed_path_prefixes"] == prefixes


def test_load_skill_forwards_allowed_path_prefixes_for_path_lookup():
    backend = _RecordingBackend()
    prefixes = ["/skills/standard/recon/", "/skills/shared/"]
    mw = SkillogyMiddleware(
        backend=backend,
        append_policy_to_system=False,
        allowed_path_prefixes=prefixes,
    )
    _tools_by_name(mw)["load_skill"].invoke(
        {"name_or_path": "/skills/standard/recon/demo/SKILL.md"}
    )
    assert backend.load_calls
    assert backend.load_calls[0]["allowed_path_prefixes"] == prefixes


def test_load_skill_by_name_forwards_acl_through_find_and_load():
    """When the agent passes a frontmatter ``name`` instead of a path,
    the middleware uses ``find_skill`` to resolve it before loading. The
    ACL must flow through both calls so the name→path lookup can't
    sidestep the allowlist."""
    backend = _RecordingBackend()
    prefixes = ["/skills/standard/recon/", "/skills/shared/"]
    mw = SkillogyMiddleware(
        backend=backend,
        append_policy_to_system=False,
        allowed_path_prefixes=prefixes,
    )
    _tools_by_name(mw)["load_skill"].invoke({"name_or_path": "demo"})
    assert backend.find_calls[0]["allowed_path_prefixes"] == prefixes
    assert backend.load_calls[0]["allowed_path_prefixes"] == prefixes


def test_traverse_forwards_allowed_path_prefixes_to_backend():
    backend = _RecordingBackend()
    prefixes = ["/skills/standard/recon/", "/skills/shared/"]
    mw = SkillogyMiddleware(
        backend=backend,
        append_policy_to_system=False,
        allowed_path_prefixes=prefixes,
    )
    _tools_by_name(mw)["traverse"].invoke({"from_path": "/skills/standard/recon/demo/SKILL.md"})
    assert backend.traverse_calls
    assert backend.traverse_calls[0]["allowed_path_prefixes"] == prefixes


def test_unset_allowlist_keeps_backend_unrestricted():
    """``allowed_path_prefixes=None`` is the library / pytest path. The
    middleware closure must omit the kwarg entirely (rather than send
    ``None``) so legacy backends that don't accept ``**kwargs`` keep
    working unchanged — and the real backends' "no ACL" semantics
    aren't silently flipped on."""
    backend = _RecordingBackend()
    mw = SkillogyMiddleware(backend=backend, append_policy_to_system=False)
    _tools_by_name(mw)["find_skill"].invoke({"subdomain": "any"})
    assert "allowed_path_prefixes" not in backend.find_calls[0]


# ── resolver: role → skills_sources_for(role) fallback ──


def test_resolver_explicit_skill_sources_wins_over_role_default():
    custom = ["/skills/plugins/llm-redteam/"]
    assert _resolve_allowed_path_prefixes(role="recon", skill_sources=custom) == custom


def test_resolver_role_falls_back_to_skills_sources_for():
    # Compare to the same helper the legacy SkillsMiddleware uses so the
    # two backends share one source-of-truth contract.
    from decepticon.agents.middleware_slots import skills_sources_for

    expected = list(skills_sources_for("recon"))
    assert _resolve_allowed_path_prefixes(role="recon", skill_sources=None) == expected


def test_resolver_no_role_returns_none():
    assert _resolve_allowed_path_prefixes(role=None, skill_sources=None) is None


def test_resolver_raises_when_role_lookup_fails(monkeypatch):
    def unavailable(_role):
        raise RuntimeError("plugin registry unavailable")

    monkeypatch.setattr("decepticon.agents.middleware_slots.skills_sources_for", unavailable)
    with pytest.raises(SkillogyScopeError, match="scope resolution failed"):
        _resolve_allowed_path_prefixes(role="recon", skill_sources=None)


@pytest.mark.parametrize(
    "sources",
    [[], ["/skills/"], ["/skills/shared"], ["/skills/shared/", "../outside/"]],
)
def test_resolver_rejects_empty_or_broad_role_scope(sources):
    with pytest.raises(SkillogyScopeError, match="nonempty restricted scope"):
        _resolve_allowed_path_prefixes(role="recon", skill_sources=sources)


def test_direct_middleware_rejects_empty_scope():
    with pytest.raises(SkillogyScopeError, match="nonempty restricted scope"):
        SkillogyMiddleware(backend=_RecordingBackend(), allowed_path_prefixes=[])


def test_role_scope_failure_never_installs_unrestricted_tools(monkeypatch):
    monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
    monkeypatch.setattr("decepticon.agents.middleware_slots.skills_sources_for", lambda _role: [])
    with pytest.raises(SkillogyScopeError, match="nonempty restricted scope"):
        maybe_install_skillogy([_StubSkillsMiddleware()], role="recon")


def test_empty_scope_is_irrelevant_without_a_skill_slot(monkeypatch):
    monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
    stack = [object()]
    assert maybe_install_skillogy(stack, role="recon", skill_sources=[]) is stack


# ── maybe_install_skillogy: role wiring picks up the ACL automatically ──


def test_maybe_install_threads_role_acl_into_middleware(monkeypatch):
    """End-to-end glue: ``build_middleware(role="recon")`` ultimately
    calls ``maybe_install_skillogy(role="recon", skill_sources=None)``,
    which must inject a middleware whose backend calls carry the recon
    role's path-prefix allowlist."""
    monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
    backend = _RecordingBackend()
    monkeypatch.setattr("decepticon.middleware.skillogy._backend_factory", lambda: backend)
    stack = [_StubSkillsMiddleware()]
    out = maybe_install_skillogy(stack, role="recon")
    installed = [m for m in out if isinstance(m, SkillogyMiddleware)]
    assert len(installed) == 1
    _tools_by_name(installed[0])["find_skill"].invoke({"subdomain": "any"})
    from decepticon.agents.middleware_slots import skills_sources_for

    assert backend.find_calls[0]["allowed_path_prefixes"] == list(skills_sources_for("recon"))


def test_maybe_install_no_role_keeps_acl_unset(monkeypatch):
    """Standalone/library invocation — no role context, ACL stays off so
    the library's unrestricted backend behaviour is preserved."""
    monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
    backend = _RecordingBackend()
    monkeypatch.setattr("decepticon.middleware.skillogy._backend_factory", lambda: backend)
    out = maybe_install_skillogy([_StubSkillsMiddleware()], role=None)
    installed = [m for m in out if isinstance(m, SkillogyMiddleware)]
    _tools_by_name(installed[0])["find_skill"].invoke({"subdomain": "any"})
    assert "allowed_path_prefixes" not in backend.find_calls[0]


# ── tool surface output shape unchanged ──


# find_skill / traverse stay JSON (structured search results). load_skill is the
# exception since the body-only follow-up: it returns the skill BODY as plain
# markdown (header + content), not a JSON envelope — see
# test_middleware_load_skill_tool_returns_body. So it is intentionally NOT in
# this JSON-shape parametrization.
@pytest.mark.parametrize("tool_name", ["find_skill", "traverse"])
def test_tool_output_shape_still_json(tool_name):
    """ADR-0008 changes who-can-see-what but not the wire format for the
    search-shaped tools: find_skill / traverse results must still parse as JSON
    so existing agent-side post-processing keeps working."""
    backend = _RecordingBackend()
    mw = SkillogyMiddleware(
        backend=backend,
        append_policy_to_system=False,
        allowed_path_prefixes=["/skills/standard/recon/", "/skills/shared/"],
    )
    tools = _tools_by_name(mw)
    if tool_name == "find_skill":
        result = tools[tool_name].invoke({"subdomain": "x"})
    else:
        result = tools[tool_name].invoke({"from_path": "/skills/standard/recon/demo/SKILL.md"})
    json.loads(result)  # raises if the closure broke the JSON envelope
