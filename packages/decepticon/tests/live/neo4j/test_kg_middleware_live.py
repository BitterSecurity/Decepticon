"""Live integration tests for :class:`KGMiddleware` against compose Neo4j.

End-to-end check of the four hooks on a real KGStore — before_agent
hydrates from engagement_name, the summary updates after a write, the
revision marker forces a rebuild on the next turn, and wrap_tool_call
refuses to write without an engagement.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from decepticon.middleware.kg import KG_SYSTEM_PROMPT, KGMiddleware
from decepticon.middleware.kg_internal.store import KGStore


def _fake_request(
    *, state: dict[str, Any], system_message: SystemMessage | None = None
) -> SimpleNamespace:
    """Synthetic ModelRequest stand-in (matches the shape OPPLAN tests use)."""
    request = SimpleNamespace(state=state, system_message=system_message)

    def override(**kwargs: Any) -> SimpleNamespace:
        new = SimpleNamespace(**vars(request))
        for k, v in kwargs.items():
            setattr(new, k, v)
        return new

    request.override = override  # type: ignore[attr-defined]
    return request


def test_middleware_hydrates_state_from_engagement_name_live(
    kgstore: KGStore, engagement: str
) -> None:
    mw = KGMiddleware(store=kgstore)
    out = mw.before_agent({"engagement_name": engagement}, runtime=None)
    assert out is not None
    assert out["kg_engagement"] == engagement
    assert out["kg_revision"].startswith(f"rev-{engagement}-")
    # Even an empty engagement gets at least the header in the summary.
    assert f"engagement={engagement}" in out["kg_summary"]


def test_middleware_summary_updates_after_kg_record_write(
    kgstore: KGStore, engagement: str
) -> None:
    mw = KGMiddleware(store=kgstore)

    # Initial hydrate.
    state = {"engagement_name": engagement}
    state.update(mw.before_agent(state, runtime=None) or {})
    initial_revision = state["kg_revision"]
    assert "Top vulnerabilities" not in state["kg_summary"]

    # Invoke kg_record through the tool the middleware exposes.
    [kg_record, _] = mw.tools
    payload = {
        "name": "kg_record",
        "type": "tool_call",
        "id": "live-mw-call",
        "args": {
            "observations": json.dumps(
                [
                    {
                        "kind": "Vulnerability",
                        "key": f"vuln::mw-test::{engagement}",
                        "label": "MW test vuln",
                        "props": {"severity": "critical"},
                    }
                ]
            ),
            "state": state,
        },
    }
    result_msg = kg_record.invoke(payload)
    result = json.loads(result_msg.content)
    assert result["created"] == 1

    # after_model simulating: detect the kg_record call and mark dirty.
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "kg_record", "args": {"observations": "[]"}, "id": "live-mw-call"}],
    )
    after_update = mw.after_model({"messages": [ai]}, runtime=None)
    assert after_update == {"kg_revision": "dirty"}
    state.update(after_update)

    # Next turn's before_agent rebuilds the summary.
    second = mw.before_agent(state, runtime=None)
    assert second is not None
    assert second["kg_revision"] != initial_revision
    assert "Top vulnerabilities" in second["kg_summary"]
    assert "MW test vuln" in second["kg_summary"]


def test_middleware_wrap_model_call_injects_summary_into_system_message(
    kgstore: KGStore, engagement: str
) -> None:
    """End-to-end check: after before_agent, wrap_model_call should
    append the KG_SYSTEM_PROMPT + the summary block to the system
    message."""
    mw = KGMiddleware(store=kgstore)
    state = {"engagement_name": engagement}
    state.update(mw.before_agent(state, runtime=None) or {})

    sys_msg = SystemMessage(content="agent base prompt")
    request = _fake_request(state=state, system_message=sys_msg)

    out = mw.wrap_model_call(request, lambda req: req)
    blocks = out.system_message.content
    assert any(KG_SYSTEM_PROMPT in (b.get("text") or "") for b in blocks if isinstance(b, dict))
    assert any(
        f"engagement={engagement}" in (b.get("text") or "") for b in blocks if isinstance(b, dict)
    )


def test_middleware_wrap_tool_call_rejects_kg_record_without_engagement(
    kgstore: KGStore,
) -> None:
    """End-to-end refusal: no engagement on state → middleware returns
    a ToolMessage and the live store is NEVER touched."""
    mw = KGMiddleware(store=kgstore)
    [kg_record, _] = mw.tools

    tool_request = SimpleNamespace(
        tool=SimpleNamespace(name="kg_record"),
        state={},
        tool_call=SimpleNamespace(id="rejected-call"),
    )

    captured: list[Any] = []
    out = mw.wrap_tool_call(
        tool_request,
        lambda req: captured.append(req) or "should not be reached",
    )
    assert isinstance(out, ToolMessage)
    payload = json.loads(out.content)
    assert "error" in payload
    assert "kg_engagement" in payload["error"]
    # Handler must not have been invoked → no Neo4j write.
    assert captured == []


def test_middleware_wrap_tool_call_passes_through_when_engagement_set(
    kgstore: KGStore, engagement: str
) -> None:
    mw = KGMiddleware(store=kgstore)
    tool_request = SimpleNamespace(
        tool=SimpleNamespace(name="kg_record"),
        state={"kg_engagement": engagement},
        tool_call=SimpleNamespace(id="allowed-call"),
    )
    captured: list[Any] = []
    out = mw.wrap_tool_call(
        tool_request,
        lambda req: captured.append(req) or "handler-ran",
    )
    assert out == "handler-ran"
    assert len(captured) == 1


def test_middleware_full_lifecycle_loop_live(kgstore: KGStore, engagement: str) -> None:
    """Two-turn loop end-to-end: turn 1 writes a Host via kg_record,
    turn 2's summary reflects the write."""
    mw = KGMiddleware(store=kgstore)

    # Turn 1.
    state: dict[str, Any] = {"engagement_name": engagement}
    state.update(mw.before_agent(state, runtime=None) or {})
    [kg_record, _] = mw.tools
    payload = {
        "name": "kg_record",
        "type": "tool_call",
        "id": "tc-turn-1",
        "args": {
            "observations": json.dumps(
                [
                    {
                        "kind": "Host",
                        "key": f"host::full-loop::{engagement}",
                        "label": "full-loop-host",
                    }
                ]
            ),
            "state": state,
        },
    }
    write_result = json.loads(kg_record.invoke(payload).content)
    assert write_result["created"] == 1

    # The middleware's after_model would mark dirty given the AI message.
    state.update(
        mw.after_model(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "kg_record", "args": {"observations": "[]"}, "id": "tc-turn-1"}
                        ],
                    )
                ]
            },
            runtime=None,
        )
        or {}
    )

    # Turn 2 — before_agent picks up the new revision and rebuilds.
    update = mw.before_agent(state, runtime=None)
    assert update is not None
    assert update["kg_revision"].endswith(tuple("0123456789"))  # not "dirty"
    # Stats line should now show at least one node.
    assert "Nodes**: 1" in update["kg_summary"] or "Nodes**: 0" not in update["kg_summary"]
