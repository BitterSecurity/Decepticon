from __future__ import annotations

import importlib
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from decepticon.middleware import opplan as opplan_mod
from decepticon.middleware.opplan import OPPLAN_SYSTEM_PROMPT, OPPLANMiddleware


def _obj_dict(obj_id: str, **overrides: Any) -> dict:
    base = {
        "id": obj_id,
        "title": f"objective {obj_id}",
        "phase": "recon",
        "description": "...",
        "acceptance_criteria": ["criterion"],
        "priority": 1,
        "status": "pending",
        "mitre": [],
        "opsec": "standard",
        "opsec_notes": "",
        "c2_tier": "interactive",
        "concessions": [],
        "blocked_by": [],
        "owner": "",
        "notes": "",
        "parent_id": None,
    }
    base.update(overrides)
    return base


class _FakeRequest:
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


class TestReduceEngagementName:
    def test_returns_update_when_update_is_not_none(self) -> None:
        result = opplan_mod._reduce_engagement_name("old", "new")
        assert result == "new"

    def test_returns_current_when_update_is_none(self) -> None:
        result = opplan_mod._reduce_engagement_name("keep", None)
        assert result == "keep"


class TestReduceWorkspacePath:
    def test_returns_update_when_update_is_not_none(self) -> None:
        result = opplan_mod._reduce_workspace_path("/ws1", "/ws2")
        assert result == "/ws2"

    def test_returns_current_when_update_is_none(self) -> None:
        result = opplan_mod._reduce_workspace_path("/ws1", None)
        assert result == "/ws1"


class TestOpplanMaxRowsDefault:
    def test_default_max_rows_is_40(self) -> None:
        assert opplan_mod._OPPLAN_MAX_ROWS == 40


class TestOpplanMaxRowsEnv:
    def test_valid_int_env_sets_max_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_OPPLAN_MAX_ROWS", "5")
        importlib.reload(opplan_mod)
        try:
            assert opplan_mod._OPPLAN_MAX_ROWS == 5
        finally:
            monkeypatch.delenv("DECEPTICON_OPPLAN_MAX_ROWS", raising=False)
            importlib.reload(opplan_mod)

    def test_invalid_env_falls_back_to_40(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_OPPLAN_MAX_ROWS", "notint")
        importlib.reload(opplan_mod)
        try:
            assert opplan_mod._OPPLAN_MAX_ROWS == 40
        finally:
            monkeypatch.delenv("DECEPTICON_OPPLAN_MAX_ROWS", raising=False)
            importlib.reload(opplan_mod)


class TestFormatOpplanStatusCounts:
    def test_all_status_types_counted_and_rendered_in_output(self) -> None:
        objs = [
            _obj_dict("OBJ-001", status="pending"),
            _obj_dict("OBJ-002", status="in-progress"),
            _obj_dict("OBJ-003", status="blocked"),
            _obj_dict("OBJ-004", status="completed"),
            _obj_dict("OBJ-005", status="cancelled"),
        ]
        result = opplan_mod._format_opplan_status(objs, "op", "apt")
        assert "Progress: 1/5 completed, 1 blocked, 1 in-progress, 1 pending" in result
        assert ", 1 cancelled" in result
        assert ">>IN-PROGRESS<<" in result
        assert "BLOCKED" in result
        assert "COMPLETED" in result
        assert "CANCELLED" in result
        assert "<OPPLAN_STATUS>" in result
        assert "</OPPLAN_STATUS>" in result

    def test_next_block_with_mitre_and_criteria(self) -> None:
        objs = [
            _obj_dict(
                "OBJ-001",
                status="pending",
                priority=1,
                title="Recon",
                phase="recon",
                mitre=["T1595", "T1592"],
                opsec="loud",
                c2_tier="beacon",
                acceptance_criteria=["c1", "c2"],
            ),
        ]
        result = opplan_mod._format_opplan_status(objs, "op", "apt")
        assert "**Next**: OBJ-001" in result
        assert "Recon" in result
        assert "MITRE: T1595, T1592" in result
        assert "OPSEC: loud" in result
        assert "C2: beacon" in result
        assert "Acceptance Criteria:" in result
        assert "- [ ] c1" in result
        assert "- [ ] c2" in result

    def test_next_block_mitre_empty_renders_na_and_no_criteria_section(self) -> None:
        objs = [
            _obj_dict(
                "OBJ-001",
                status="pending",
                mitre=[],
                acceptance_criteria=[],
            ),
        ]
        result = opplan_mod._format_opplan_status(objs, "op", "apt")
        assert "MITRE: n/a" in result
        assert "Acceptance Criteria:" not in result

    def test_all_complete_renders_all_objectives_complete(self) -> None:
        objs = [
            _obj_dict("OBJ-001", status="completed"),
            _obj_dict("OBJ-002", status="completed"),
        ]
        result = opplan_mod._format_opplan_status(objs, "op", "apt")
        assert "ALL OBJECTIVES COMPLETE" in result

    def test_only_blocked_renders_no_actionable_objectives(self) -> None:
        objs = [_obj_dict("OBJ-001", status="blocked")]
        result = opplan_mod._format_opplan_status(objs, "op", "apt")
        assert "No actionable objectives" in result
        assert "ALL OBJECTIVES COMPLETE" not in result

    def test_terminal_trimming_and_hidden_line(self) -> None:
        original_max = opplan_mod._OPPLAN_MAX_ROWS
        opplan_mod._OPPLAN_MAX_ROWS = 2
        try:
            objs = [_obj_dict("OBJ-001", status="pending")] + [
                _obj_dict(f"OBJ-{i:03d}", status="completed") for i in range(2, 7)
            ]
            result = opplan_mod._format_opplan_status(objs, "op", "apt")
            assert "_4 more terminal objectives_" in result
        finally:
            opplan_mod._OPPLAN_MAX_ROWS = original_max


class TestInjectOpplanContext:
    def test_next_objective_excludes_unmet_dependency(self) -> None:
        req = _FakeRequest(
            state={
                "objectives": [
                    _obj_dict("OBJ-001", priority=2),
                    _obj_dict("OBJ-002", priority=1, blocked_by=["OBJ-001"]),
                ]
            }
        )

        result = OPPLANMiddleware()._inject_opplan_context(req)

        assert "**Next**: OBJ-001" in _flatten(result.system_message)
        assert "Status-ready candidates: OBJ-001" in _flatten(result.system_message)

    def test_no_objectives_produces_single_static_block_with_cache_control(self) -> None:
        req = _FakeRequest(state={}, system_message=None)
        result = OPPLANMiddleware()._inject_opplan_context(req)
        content = result.system_message.content
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert OPPLAN_SYSTEM_PROMPT in content[0]["text"]

    def test_with_objectives_appends_dynamic_block_without_cache_control(self) -> None:
        req = _FakeRequest(
            state={
                "objectives": [_obj_dict("OBJ-001")],
                "engagement_name": "op",
                "threat_profile": "apt",
            },
            system_message=None,
        )
        result = OPPLANMiddleware()._inject_opplan_context(req)
        content = result.system_message.content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in content[1]
        assert "<OPPLAN_STATUS>" in content[1]["text"]
        assert "Engagement: op" in content[1]["text"]

    def test_existing_system_message_is_prepended_before_opplan_blocks(self) -> None:
        original_msg = SystemMessage(content="ORIGINAL_PROMPT")
        req = _FakeRequest(
            state={},
            system_message=original_msg,
        )
        result = OPPLANMiddleware()._inject_opplan_context(req)
        content = result.system_message.content
        assert isinstance(content, list)
        assert content[0]["text"] == "ORIGINAL_PROMPT"
        assert any(
            OPPLAN_SYSTEM_PROMPT in b.get("text", "") for b in content if isinstance(b, dict)
        )

    def test_returns_new_request_object_not_same_instance(self) -> None:
        req = _FakeRequest(state={}, system_message=None)
        result = OPPLANMiddleware()._inject_opplan_context(req)
        assert result is not req


class TestWrapModelCall:
    def test_wrap_model_call_invokes_handler_with_injected_request_and_returns_result(self) -> None:
        middleware = OPPLANMiddleware()
        captured: dict[str, Any] = {}

        def handler(r: Any) -> str:
            captured["req"] = r
            return "RESULT"

        req = _FakeRequest(
            state={
                "objectives": [_obj_dict("OBJ-001")],
                "engagement_name": "op",
                "threat_profile": "apt",
            },
        )
        result = middleware.wrap_model_call(req, handler)
        assert result == "RESULT"
        injected = captured["req"]
        full_text = _flatten(injected.system_message)
        assert "<OPPLAN_STATUS>" in full_text


class TestAwrapModelCall:
    async def test_awrap_model_call_invokes_async_handler_and_returns_result(self) -> None:
        middleware = OPPLANMiddleware()
        captured: dict[str, Any] = {}

        async def handler(r: Any) -> tuple[str, Any]:
            captured["req"] = r
            return ("R", r)

        req = _FakeRequest(
            state={
                "objectives": [_obj_dict("OBJ-001")],
                "engagement_name": "op",
                "threat_profile": "apt",
            },
        )
        result = await middleware.awrap_model_call(req, handler)
        assert isinstance(result, tuple)
        assert result[0] == "R"
        injected = captured["req"]
        full_text = _flatten(injected.system_message)
        assert OPPLAN_SYSTEM_PROMPT in full_text


class TestAfterModelNoMessages:
    def test_empty_messages_list_returns_none(self) -> None:
        assert OPPLANMiddleware().after_model({"messages": []}, runtime=None) is None

    def test_missing_messages_key_returns_none(self) -> None:
        assert OPPLANMiddleware().after_model({}, runtime=None) is None


class TestAfterModelNoAIMessageOrNoToolCalls:
    def test_only_human_message_returns_none(self) -> None:
        result = OPPLANMiddleware().after_model({"messages": [HumanMessage("hi")]}, runtime=None)
        assert result is None

    def test_ai_message_with_empty_tool_calls_returns_none(self) -> None:
        last_ai = AIMessage(content="x", tool_calls=[])
        result = OPPLANMiddleware().after_model({"messages": [last_ai]}, runtime=None)
        assert result is None


class TestAfterModelSingleOrZeroOpplanCalls:
    def test_one_opplan_call_plus_one_non_opplan_returns_none(self) -> None:
        last_ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-1", "name": "add_objective", "args": {}, "type": "tool_call"},
                {"id": "tc-2", "name": "bash", "args": {"command": "ls"}, "type": "tool_call"},
            ],
        )
        assert OPPLANMiddleware().after_model({"messages": [last_ai]}, runtime=None) is None


class TestAafterModelDelegatesToSync:
    async def test_parallel_opplan_calls_return_error_tool_messages(self) -> None:
        last_ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-a", "name": "list_objectives", "args": {}, "type": "tool_call"},
                {"id": "tc-b", "name": "add_objective", "args": {}, "type": "tool_call"},
            ],
        )
        result = await OPPLANMiddleware().aafter_model({"messages": [last_ai]}, runtime=None)
        assert result is not None
        msgs = result["messages"]
        assert len(msgs) == 1
        assert msgs[0].tool_call_id == "tc-b"

    async def test_single_opplan_call_returns_none_via_aafter_model(self) -> None:
        last_ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-x", "name": "add_objective", "args": {}, "type": "tool_call"},
            ],
        )
        result = await OPPLANMiddleware().aafter_model({"messages": [last_ai]}, runtime=None)
        assert result is None
