"""Tests for ``decepticon.middleware.skillogy`` (Phase 1a, Amendment v0.2.2).

Covers:
  - Env-flag parsing (legacy ``DECEPTICON_USE_SKILLOGY`` + preferred
    ``DECEPTICON_SKILL_BACKEND``).
  - ``_PHASE_FOR_ROLE`` mapping shape — every OSS specialist resolves
    to a known ``:Phase.name``.
  - The three ``@tool`` wrappers (find_skill, load_skill, traverse)
    against a stub backend — happy path + error envelope.
  - ``SkillogyMiddleware`` constructor: three tools registered,
    no spurious 4th tool from the removed ``run_cypher_read`` lane.
  - ``_render_phase_block`` — MoCs present, MoCs empty, backend failure.
  - ``_inject`` — static schema cheat-sheet + dynamic phase block
    are both present, original system message is preserved.
  - ``maybe_install_skillogy`` swap rule and role → phase threading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import SystemMessage

from decepticon.middleware import skillogy as sk
from decepticon.middleware.skillogy import (
    _PHASE_FOR_ROLE,
    _POLICY_PROMPT,
    SkillogyMiddleware,
    _is_enabled,
    _make_find_skill_tool,
    _make_load_skill_tool,
    _make_traverse_tool,
    maybe_install_skillogy,
)

# ── _is_enabled ────────────────────────────────────────────────────────


class TestIsEnabled:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_legacy_truthy_values_enable(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", val)
        monkeypatch.delenv("DECEPTICON_SKILL_BACKEND", raising=False)
        assert _is_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off"])
    def test_explicit_falsy_values_disable(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", val)
        monkeypatch.delenv("DECEPTICON_SKILL_BACKEND", raising=False)
        assert _is_enabled() is False

    @pytest.mark.parametrize("val", ["", "maybe"])
    def test_non_falsy_values_enable(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        """Anything that isn't an explicit ``0``/``false``/``no``/``off``
        keeps the default-on behaviour — including unrecognised strings.
        """
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", val)
        monkeypatch.delenv("DECEPTICON_SKILL_BACKEND", raising=False)
        assert _is_enabled() is True

    def test_preferred_skillogy_brain_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DECEPTICON_USE_SKILLOGY", raising=False)
        monkeypatch.setenv("DECEPTICON_SKILL_BACKEND", "skillogy_brain")
        assert _is_enabled() is True

    def test_preferred_other_value_does_not_force_enable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only ``DECEPTICON_SKILL_BACKEND=skillogy_brain`` is the explicit
        opt-in rail. Any other value is inert; whether Skillogy ends up
        installed is then up to ``DECEPTICON_USE_SKILLOGY`` (which now
        defaults to enabled). An explicit ``DECEPTICON_USE_SKILLOGY=0``
        plus any other ``DECEPTICON_SKILL_BACKEND`` value still disables.
        """
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "0")
        monkeypatch.setenv("DECEPTICON_SKILL_BACKEND", "skills")
        assert _is_enabled() is False

    def test_unset_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default-on: leaving both env vars unset enables Skillogy."""
        monkeypatch.delenv("DECEPTICON_USE_SKILLOGY", raising=False)
        monkeypatch.delenv("DECEPTICON_SKILL_BACKEND", raising=False)
        assert _is_enabled() is True


# ── _PHASE_FOR_ROLE — mapping completeness ─────────────────────────────


class TestPhaseForRoleMapping:
    """The 19 OSS standard agents (orchestrators + specialists) must each resolve to a known phase.

    Without this, ``maybe_install_skillogy`` quietly hands the middleware
    no phase block and the agent loses its MoC summary. The mapping is
    the single seam between the agent factory role surface and the
    Skillogy graph's ``:Phase.name`` keys.
    """

    def test_oss_standard_roles_mapped(self) -> None:
        expected = {
            "recon",
            "exploit",
            "finding_verifier",
            "finding_reporter",
            "postexploit",
            "ad_operator",
            "cloud_hunter",
            "mobile_operator",
            "wireless_operator",
            "phisher",
            "analyst",
            "contract_auditor",
            "reverser",
            "osint_operator",
            "iot_operator",
            "ics_operator",
            "forensicator",
            "supply_chain_operator",
            "blue_cell",
            "soundwave",
            "autohunt",
            "decepticon",
        }
        assert set(_PHASE_FOR_ROLE) == expected, (
            "OSS standard roles drifted from phase mapping — "
            "update _PHASE_FOR_ROLE when adding/removing a specialist"
        )

    def test_all_phase_names_lowercase_hyphenated(self) -> None:
        # Sanity check: graph keys are kebab-case (per seeds/phases.yaml).
        # A surprise underscore here would silently miss the MoC summary.
        for role, phase in _PHASE_FOR_ROLE.items():
            assert phase == phase.lower(), f"{role} → {phase!r} is not lowercase"
            assert "_" not in phase, f"{role} → {phase!r} should be kebab-case"


# ── tool wrappers ──────────────────────────────────────────────────────


class _StubBackend:
    """In-process stand-in for ``Neo4jBackend`` used by the tool wrappers.

    Records calls + lets each test pin a canned response per method.
    """

    def __init__(
        self,
        *,
        load_response: dict | None = None,
        find_response: list[dict] | None = None,
        traverse_response: list[dict] | None = None,
        moc_response: list[dict] | None = None,
        load_exc: Exception | None = None,
        find_exc: Exception | None = None,
        traverse_exc: Exception | None = None,
        moc_exc: Exception | None = None,
    ) -> None:
        self._load_response = load_response
        self._find_response = find_response or []
        self._traverse_response = traverse_response or []
        self._moc_response = moc_response or []
        self._load_exc = load_exc
        self._find_exc = find_exc
        self._traverse_exc = traverse_exc
        self._moc_exc = moc_exc
        self.load_calls: list[str] = []
        self.find_calls: list[dict] = []
        self.traverse_calls: list[dict] = []
        self.moc_calls: list[str] = []

    def load_skill(self, path: str) -> dict | None:
        self.load_calls.append(path)
        if self._load_exc is not None:
            raise self._load_exc
        return self._load_response

    def find_skill(
        self,
        *,
        query: str | None = None,
        subdomain: str | None = None,
        mitre_id: str | None = None,
        tag: str | None = None,
        tactic_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        self.find_calls.append(
            {
                "query": query,
                "subdomain": subdomain,
                "mitre_id": mitre_id,
                "tag": tag,
                "tactic_id": tactic_id,
                "limit": limit,
            }
        )
        if self._find_exc is not None:
            raise self._find_exc
        return self._find_response

    def traverse(
        self,
        from_path: str,
        edge_types: list[str] | None = None,
        depth: int = 2,
    ) -> list[dict]:
        self.traverse_calls.append(
            {"from_path": from_path, "edge_types": edge_types, "depth": depth}
        )
        if self._traverse_exc is not None:
            raise self._traverse_exc
        return self._traverse_response

    def query_moc_summary(self, phase: str, *, limit: int = 25) -> list[dict]:
        self.moc_calls.append(phase)
        if self._moc_exc is not None:
            raise self._moc_exc
        return self._moc_response

    def close(self) -> None:  # parity with the real backend's API
        pass


class TestFindSkillTool:
    def test_happy_path_returns_count_and_hits(self) -> None:
        hits = [{"name": "kerberoasting", "path": "/skills/.../SKILL.md", "subdomain": "ad"}]
        backend = _StubBackend(find_response=hits)
        tool = _make_find_skill_tool(backend)

        out = tool.invoke({"subdomain": "active-directory", "tag": "kerberoasting"})
        data = json.loads(out)
        assert data["count"] == 1
        assert data["hits"] == hits
        assert backend.find_calls == [
            {
                "query": None,
                "subdomain": "active-directory",
                "mitre_id": None,
                "tag": "kerberoasting",
                "tactic_id": None,
                "limit": 20,
            }
        ]

    def test_value_error_surfaces_as_clean_error(self) -> None:
        backend = _StubBackend(find_exc=ValueError("requires at least one of: ..."))
        tool = _make_find_skill_tool(backend)
        data = json.loads(tool.invoke({}))
        assert data == {"error": "requires at least one of: ..."}

    def test_generic_exception_wrapped_with_repr(self) -> None:
        backend = _StubBackend(find_exc=RuntimeError("boom"))
        tool = _make_find_skill_tool(backend)
        data = json.loads(tool.invoke({"query": "any"}))
        assert "error" in data
        assert "find_skill failed" in data["error"]


class TestLoadSkillTool:
    def test_path_route_returns_body_only(self) -> None:
        backend = _StubBackend(
            load_response={
                "name": "x",
                "path": "/skills/x/SKILL.md",
                "body": "BODY-CONTENT",
                "subdomain": "web-exploitation",
            }
        )
        tool = _make_load_skill_tool(backend)
        out = tool.invoke({"name_or_path": "/skills/x/SKILL.md"})
        # Body only (+ minimal header) — NOT a JSON props dump, and the
        # search-side metadata (subdomain) must NOT leak into the reading context.
        assert not out.strip().startswith("{")
        assert "BODY-CONTENT" in out
        assert "Skill: x" in out
        assert "web-exploitation" not in out
        assert backend.load_calls == ["/skills/x/SKILL.md"]

    def test_name_route_resolves_via_find_then_loads(self) -> None:
        # find_skill returns a hit; load_skill is then called with the hit's path.
        backend = _StubBackend(
            find_response=[{"name": "kerberoasting", "path": "/skills/ad/k/SKILL.md"}],
            load_response={
                "name": "kerberoasting",
                "path": "/skills/ad/k/SKILL.md",
                "body": "kerb body",
            },
        )
        tool = _make_load_skill_tool(backend)
        out = tool.invoke({"name_or_path": "kerberoasting"})
        assert "kerb body" in out
        assert "Skill: kerberoasting" in out
        # find_skill was hit first (name route), then load_skill with the path.
        assert backend.find_calls and backend.find_calls[0]["query"] == "kerberoasting"
        assert backend.load_calls == ["/skills/ad/k/SKILL.md"]

    def test_name_route_no_exact_match_returns_error_envelope(self) -> None:
        # Substring hit but no name equality — the wrapper rejects.
        backend = _StubBackend(
            find_response=[{"name": "kerberoasting-blind", "path": "/skills/x/SKILL.md"}]
        )
        tool = _make_load_skill_tool(backend)
        data = json.loads(tool.invoke({"name_or_path": "kerberoasting"}))
        assert "error" in data
        assert "no Skill with name or path matching" in data["error"]

    def test_backend_returns_none_for_path_surfaces_error(self) -> None:
        backend = _StubBackend(load_response=None)
        tool = _make_load_skill_tool(backend)
        data = json.loads(tool.invoke({"name_or_path": "/skills/missing/SKILL.md"}))
        assert "error" in data
        assert "no Skill at path" in data["error"]

    def test_backend_exception_wrapped(self) -> None:
        backend = _StubBackend(load_exc=RuntimeError("driver down"))
        tool = _make_load_skill_tool(backend)
        data = json.loads(tool.invoke({"name_or_path": "/skills/x/SKILL.md"}))
        assert "load_skill failed" in data["error"]


class TestTraverseTool:
    def test_happy_path_returns_rows(self) -> None:
        rows = [{"labels": ["Skill"], "key": "neighbor", "depth": 1, "edge_chain": ["IN_PHASE"]}]
        backend = _StubBackend(traverse_response=rows)
        tool = _make_traverse_tool(backend)
        out = tool.invoke({"from_path": "/skills/x/SKILL.md", "depth": 3})
        data = json.loads(out)
        assert data == {"count": 1, "rows": rows}
        assert backend.traverse_calls == [
            {"from_path": "/skills/x/SKILL.md", "edge_types": None, "depth": 3}
        ]

    def test_exception_wrapped(self) -> None:
        backend = _StubBackend(traverse_exc=RuntimeError("bad path"))
        tool = _make_traverse_tool(backend)
        data = json.loads(tool.invoke({"from_path": "/skills/x/SKILL.md"}))
        assert "traverse failed" in data["error"]


# ── SkillogyMiddleware construction + phase block + injection ──────────


class TestMiddlewareConstruction:
    def test_three_tools_registered(self) -> None:
        mw = SkillogyMiddleware(backend=_StubBackend())
        names = [t.name for t in mw.tools]
        assert names == ["find_skill", "load_skill", "traverse"], (
            "Tool order or set drifted — Amendment v0.2.2 fixes the surface "
            "at exactly these three tools."
        )

    def test_no_run_cypher_read_tool(self) -> None:
        mw = SkillogyMiddleware(backend=_StubBackend())
        assert all(t.name != "run_cypher_read" for t in mw.tools), (
            "run_cypher_read must not be re-introduced into the agent surface — "
            "see Amendment v0.2.2 (backend method is kept for internal use only)."
        )

    def test_from_env_with_explicit_phase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _StubBackend(moc_response=[])
        monkeypatch.setattr(sk, "_backend_factory", lambda: stub)
        mw = SkillogyMiddleware.from_env(agent_phase="reconnaissance")
        assert mw._phase == "reconnaissance"
        assert stub.moc_calls == []


class TestPhaseBlockRender:
    def test_no_phase_means_no_block(self) -> None:
        mw = SkillogyMiddleware(agent_phase=None, backend=_StubBackend())
        assert mw._phase_block == ""

    def test_common_skills_are_listed_without_backend_lookup(self) -> None:
        backend = _StubBackend(moc_exc=RuntimeError("driver down"))
        mw = SkillogyMiddleware(agent_phase="reconnaissance", backend=backend)
        block = mw._phase_block
        assert "[Skillogy quick reference]" in block
        assert "/skills/standard/recon/passive-recon/SKILL.md" in block
        assert "/skills/standard/recon/active-recon/SKILL.md" in block
        assert "not required startup calls" in block
        assert backend.moc_calls == []

    def test_phase_without_common_skills_has_no_block(self) -> None:
        backend = _StubBackend()
        mw = SkillogyMiddleware(agent_phase="wireless", backend=backend)
        assert mw._phase_block == ""

    def test_quick_reference_respects_role_scope(self) -> None:
        mw = SkillogyMiddleware(
            agent_phase="reconnaissance",
            backend=_StubBackend(),
            allowed_path_prefixes=["/skills/standard/recon/web-recon/"],
        )
        assert "/skills/standard/recon/web-recon/SKILL.md" in mw._phase_block
        assert "passive-recon" not in mw._phase_block


# ── _inject — static schema + dynamic phase block ──────────────────────


@dataclass
class _FakeRequest:
    system_message: SystemMessage | None
    overrides: dict[str, Any] = field(default_factory=dict)

    def override(self, *, system_message: SystemMessage) -> _FakeRequest:
        return _FakeRequest(system_message=system_message, overrides={"taken": True})


class TestInject:
    def test_no_existing_system_message_creates_one_with_policy(self) -> None:
        mw = SkillogyMiddleware(backend=_StubBackend(moc_response=[]))
        out = mw._inject(_FakeRequest(system_message=None))
        assert isinstance(out.system_message, SystemMessage)
        blocks = out.system_message.content
        assert isinstance(blocks, list)
        text = blocks[0]["text"]
        # Static cheat-sheet must be present.
        assert "[Skillogy access]" in text
        assert "Graph schema" in text
        assert "find_skill" in text
        # No phase block when phase is None.
        assert "[Skillogy quick reference]" not in text

    def test_existing_system_message_blocks_preserved_and_policy_appended(
        self,
    ) -> None:
        mw = SkillogyMiddleware(backend=_StubBackend(moc_response=[]))
        original = SystemMessage(content="BASE_PROMPT")
        out = mw._inject(_FakeRequest(system_message=original))
        blocks = out.system_message.content
        assert isinstance(blocks, list)
        # The original block must still appear somewhere.
        flat = json.dumps(blocks)
        assert "BASE_PROMPT" in flat
        # The injected block lands LAST.
        assert blocks[-1]["text"].lstrip().startswith("[Skillogy access]")

    def test_phase_block_concatenated_after_policy(self) -> None:
        backend = _StubBackend()
        mw = SkillogyMiddleware(agent_phase="reconnaissance", backend=backend)
        out = mw._inject(_FakeRequest(system_message=None))
        text = out.system_message.content[0]["text"]
        # Both blocks present, schema first, phase context second.
        idx_schema = text.find("[Skillogy access]")
        idx_phase = text.find("[Skillogy quick reference]")
        assert 0 <= idx_schema < idx_phase, (
            "Phase block must come after the static schema cheat-sheet"
        )
        assert "passive-recon/SKILL.md" in text

    def test_append_policy_false_returns_request_untouched(self) -> None:
        mw = SkillogyMiddleware(
            backend=_StubBackend(),
            append_policy_to_system=False,
        )
        req = _FakeRequest(system_message=SystemMessage(content="BASE_PROMPT"))
        assert mw._inject(req) is req  # short-circuit

    def test_policy_prompt_const_stays_under_token_budget(self) -> None:
        # Hard cap: keep the static prefix under ~3 KB so 16 specialists ×
        # N turns stay within the catalog-replacement budget described in
        # the amendment. Tweak only when intentionally reshaping the surface.
        assert len(_POLICY_PROMPT) < 3000, (
            f"Static _POLICY_PROMPT grew to {len(_POLICY_PROMPT)} chars — "
            "reshape required; keep it lean."
        )


# ── maybe_install_skillogy swap + role threading ───────────────────────


class TestMaybeInstallSkillogy:
    def test_env_explicitly_disabled_returns_stack_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With Skillogy default-on, the identity short-circuit only
        triggers when the operator opts out via explicit
        ``DECEPTICON_USE_SKILLOGY=0`` (or ``false`` / ``no`` / ``off``).
        Default-unset now installs Skillogy, not the file-system
        ``SkillsMiddleware``.
        """
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "0")
        monkeypatch.delenv("DECEPTICON_SKILL_BACKEND", raising=False)
        from decepticon.middleware.skills import SkillsMiddleware

        fake_skills = MagicMock(spec=SkillsMiddleware)
        other = object()
        stack = [fake_skills, other]
        out = maybe_install_skillogy(stack, role="recon")
        assert out is stack  # identity short-circuit

    def test_env_enabled_swaps_skills_for_skillogy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
        stub = _StubBackend(moc_response=[])
        monkeypatch.setattr(sk, "_backend_factory", lambda: stub)

        from decepticon.middleware.skills import SkillsMiddleware

        fake_skills = MagicMock(spec=SkillsMiddleware)
        other = object()
        stack = [other, fake_skills]

        out = maybe_install_skillogy(stack, role="recon")
        assert len(out) == 2
        assert out[0] is other  # non-skills entries preserved
        assert isinstance(out[1], SkillogyMiddleware)
        # The role was resolved to the right phase.
        assert out[1]._phase == "reconnaissance"
        assert stub.moc_calls == []

    def test_env_enabled_unknown_role_yields_no_phase_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
        stub = _StubBackend()
        monkeypatch.setattr(sk, "_backend_factory", lambda: stub)

        from decepticon.middleware.skills import SkillsMiddleware

        fake_skills = MagicMock(spec=SkillsMiddleware)
        out = maybe_install_skillogy([fake_skills], role="plugin_specialist")
        installed = next(m for m in out if isinstance(m, SkillogyMiddleware))
        assert installed._phase is None
        assert installed._phase_block == ""
        assert stub.moc_calls == []  # backend never consulted for unknown role

    def test_env_enabled_no_role_argument_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
        stub = _StubBackend()
        monkeypatch.setattr(sk, "_backend_factory", lambda: stub)

        from decepticon.middleware.skills import SkillsMiddleware

        fake_skills = MagicMock(spec=SkillsMiddleware)
        out = maybe_install_skillogy([fake_skills])  # no role kwarg
        installed = next(m for m in out if isinstance(m, SkillogyMiddleware))
        assert installed._phase is None

    def test_env_enabled_no_skills_does_not_append(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_USE_SKILLOGY", "1")
        monkeypatch.setattr(sk, "_backend_factory", lambda: _StubBackend())

        other = object()
        out = maybe_install_skillogy([other], role="recon")
        # Swap-only: no SkillsMiddleware means no skillogy layer is added.
        assert out == [other]
        assert not any(isinstance(m, SkillogyMiddleware) for m in out)
