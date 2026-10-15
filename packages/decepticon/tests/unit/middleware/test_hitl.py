"""Tests for decepticon.middleware.hitl."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from decepticon.middleware.hitl import (
    ApprovalDecision,
    ApprovalPolicyRule,
    ApprovalRequest,
    FileBackedApprovalTransport,
    HITLApprovalMiddleware,
    InProcessApprovalTransport,
    _redact,
)


class _Tool:
    def __init__(self, name: str):
        self.name = name


class _Req:
    def __init__(self, tool_name: str, args: dict, state: dict | None = None):
        self.tool = _Tool(tool_name)
        self.tool_call_args = args
        self.tool_call_id = "tc_1"
        self.state = state or {}

    def override(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self


def _allow_handler(request):
    return ToolMessage(
        content="executed",
        tool_call_id=request.tool_call_id,
        name=request.tool.name,
    )


def test_redact_strips_known_secret_keys():
    out = _redact({"username": "alice", "password": "p1", "api_key": "k", "x": 1})
    assert out["username"] == "alice"
    assert out["password"] == "***REDACTED***"
    assert out["api_key"] == "***REDACTED***"
    assert out["x"] == 1


def test_redact_recurses_into_nested():
    out = _redact({"outer": {"token": "x", "name": "y"}})
    assert out["outer"]["token"] == "***REDACTED***"
    assert out["outer"]["name"] == "y"


def test_inprocess_transport_roundtrip():
    t = InProcessApprovalTransport()
    req = ApprovalRequest(
        request_id="r1",
        engagement_name="e",
        agent_name="a",
        tool_name="bash",
        tool_args_redacted={},
        matched_rule=ApprovalPolicyRule(tool_pattern="bash"),
        reason="r",
        created_at=time.time(),
    )
    t.submit(req)
    assert len(t.pending()) == 1

    def _supply():
        time.sleep(0.05)
        t.provide_decision(ApprovalDecision(request_id="r1", action="allow"))

    threading.Thread(target=_supply, daemon=True).start()
    decision = t.wait_for_decision("r1", timeout=1.0)
    assert decision is not None
    assert decision.action == "allow"


def test_inprocess_transport_times_out_when_no_decision():
    t = InProcessApprovalTransport()
    req = ApprovalRequest(
        request_id="r2",
        engagement_name="",
        agent_name="",
        tool_name="bash",
        tool_args_redacted={},
        matched_rule=ApprovalPolicyRule(tool_pattern="bash"),
        reason="",
        created_at=time.time(),
    )
    t.submit(req)
    assert t.wait_for_decision("r2", timeout=0.2) is None


def test_filebacked_transport_roundtrip(tmp_path: Path):
    requests = tmp_path / "requests.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    transport = FileBackedApprovalTransport(requests, decisions, poll_interval=0.05)
    req = ApprovalRequest(
        request_id="r3",
        engagement_name="eng",
        agent_name="recon",
        tool_name="bash",
        tool_args_redacted={"cmd": "id"},
        matched_rule=ApprovalPolicyRule(tool_pattern="bash"),
        reason="audit",
        created_at=time.time(),
    )
    transport.submit(req)
    assert "approval_request" in requests.read_text(encoding="utf-8")

    decisions.write_text(
        json.dumps(
            {
                "kind": "approval_decision",
                "request_id": "r3",
                "action": "allow",
                "operator_note": "looks fine",
                "decided_at": time.time(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    decision = transport.wait_for_decision("r3", timeout=2.0)
    assert decision is not None
    assert decision.action == "allow"
    assert decision.operator_note == "looks fine"


def test_filebacked_transport_returns_none_on_timeout(tmp_path: Path):
    transport = FileBackedApprovalTransport(
        tmp_path / "r.jsonl", tmp_path / "d.jsonl", poll_interval=0.05
    )
    transport.submit(
        ApprovalRequest(
            request_id="r4",
            engagement_name="",
            agent_name="",
            tool_name="bash",
            tool_args_redacted={},
            matched_rule=ApprovalPolicyRule(tool_pattern="bash"),
            reason="",
            created_at=time.time(),
        )
    )
    assert transport.wait_for_decision("r4", timeout=0.2) is None


@pytest.mark.parametrize("decision_order", [("first", "second"), ("second", "first")])
def test_filebacked_decisions_survive_waiting_for_another_request(
    tmp_path: Path, decision_order: tuple[str, str]
) -> None:
    decisions = tmp_path / "decisions.jsonl"
    transport = FileBackedApprovalTransport(
        tmp_path / "requests.jsonl", decisions, poll_interval=0.001
    )
    actions = {"first": "allow", "second": "deny"}
    decisions.write_text(
        "".join(
            json.dumps(
                {
                    "kind": "approval_decision",
                    "request_id": request_id,
                    "action": actions[request_id],
                }
            )
            + "\n"
            for request_id in decision_order
        ),
        encoding="utf-8",
    )
    first = transport.wait_for_decision("first", timeout=0.1)
    second = transport.wait_for_decision("second", timeout=0.1)
    assert first is not None and first.request_id == "first" and first.action == "allow"
    assert second is not None and second.request_id == "second" and second.action == "deny"


def test_filebacked_timeout_preserves_a_different_requests_decision(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions.jsonl"
    transport = FileBackedApprovalTransport(
        tmp_path / "requests.jsonl", decisions, poll_interval=0.001
    )
    decisions.write_text(
        json.dumps({"kind": "approval_decision", "request_id": "pending", "action": "deny"}) + "\n",
        encoding="utf-8",
    )
    assert transport.wait_for_decision("unanswered", timeout=0.02) is None
    pending = transport.wait_for_decision("pending", timeout=0.1)
    assert pending is not None and pending.request_id == "pending" and pending.action == "deny"


def test_middleware_passes_through_unmatched_tools():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(
        policy=[ApprovalPolicyRule(tool_pattern=r"^dangerous_tool$")],
        transport=t,
    )
    request = _Req("safe_tool", {"x": 1})
    result = mw.wrap_tool_call(request, _allow_handler)
    assert result.content == "executed"
    assert t.pending() == []


def test_middleware_blocks_matched_tool_until_decision():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(
        policy=[ApprovalPolicyRule(tool_pattern=r"^dangerous$", timeout_seconds=2.0)],
        transport=t,
    )
    request = _Req("dangerous", {"target": "10.0.0.1"})

    def _approve():
        time.sleep(0.1)
        pending = t.pending()
        assert len(pending) == 1
        t.provide_decision(ApprovalDecision(request_id=pending[0].request_id, action="allow"))

    threading.Thread(target=_approve, daemon=True).start()
    result = mw.wrap_tool_call(request, _allow_handler)
    assert result.content == "executed"


def test_middleware_denies_on_timeout_by_default():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(
        policy=[
            ApprovalPolicyRule(
                tool_pattern=r"^dangerous$",
                timeout_seconds=0.15,
                default_on_timeout="deny",
            )
        ],
        transport=t,
    )
    request = _Req("dangerous", {})
    result = mw.wrap_tool_call(request, _allow_handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "denied" in result.content.lower()


def test_middleware_matches_by_technique_tag():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(
        policy=[ApprovalPolicyRule(technique_tag="T1003", timeout_seconds=0.2)],
        transport=t,
    )
    state = {"current_objective": {"technique_id": "T1003"}}
    request = _Req("any_tool", {}, state=state)

    threading.Thread(
        target=lambda: (
            time.sleep(0.05),
            t.provide_decision(
                ApprovalDecision(request_id=t.pending()[0].request_id, action="allow")
            ),
        ),
        daemon=True,
    ).start()
    result = mw.wrap_tool_call(request, _allow_handler)
    assert result.content == "executed"


def test_middleware_redacts_secrets_in_request_payload():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(
        policy=[ApprovalPolicyRule(tool_pattern=r"^dangerous$", timeout_seconds=0.15)],
        transport=t,
    )
    request = _Req("dangerous", {"username": "u", "password": "very-secret"})
    mw.wrap_tool_call(request, _allow_handler)
    pending = t.pending()
    assert pending
    args = pending[0].tool_args_redacted
    assert args["username"] == "u"
    assert args["password"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_middleware_async_path_blocks_and_allows():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(
        policy=[ApprovalPolicyRule(tool_pattern=r"^dangerous$", timeout_seconds=2.0)],
        transport=t,
    )
    request = _Req("dangerous", {})

    async def _async_handler(req):
        return ToolMessage(content="async-ok", tool_call_id=req.tool_call_id, name=req.tool.name)

    def _approve():
        time.sleep(0.05)
        t.provide_decision(ApprovalDecision(request_id=t.pending()[0].request_id, action="allow"))

    threading.Thread(target=_approve, daemon=True).start()
    result = await mw.awrap_tool_call(request, _async_handler)
    assert result.content == "async-ok"


def test_default_high_impact_policy_includes_t1003_and_c2_deploy():
    from decepticon.middleware.hitl import DEFAULT_HIGH_IMPACT_POLICY

    has_t1003 = any(r.technique_tag == "T1003" for r in DEFAULT_HIGH_IMPACT_POLICY)
    has_c2 = any(
        r.tool_pattern and "sliver_implant" in r.tool_pattern for r in DEFAULT_HIGH_IMPACT_POLICY
    )
    assert has_t1003
    assert has_c2


class _SleepingTransport:
    """Fake transport whose ``wait_for_decision`` blocks on ``time.sleep``
    before returning a decision — used to prove ``awrap_tool_call`` offloads
    the wait to a thread instead of freezing the event loop."""

    def __init__(self, sleep_for: float = 0.3) -> None:
        self._sleep_for = sleep_for
        self.submitted: list[ApprovalRequest] = []

    def submit(self, request: ApprovalRequest) -> None:
        self.submitted.append(request)

    def wait_for_decision(self, request_id: str, timeout: float) -> ApprovalDecision | None:
        time.sleep(self._sleep_for)  # blocking, like FileBackedApprovalTransport
        return ApprovalDecision(request_id=request_id, action="allow")


@pytest.mark.asyncio
async def test_awrap_does_not_block_event_loop():
    """``awrap_tool_call`` must offload the blocking ``wait_for_decision``
    to a worker thread (asyncio.to_thread) so the event loop keeps running."""
    import asyncio

    transport = _SleepingTransport(sleep_for=0.3)
    mw = HITLApprovalMiddleware(
        policy=[ApprovalPolicyRule(tool_pattern=r"^dangerous$", timeout_seconds=2.0)],
        transport=transport,
    )
    request = _Req("dangerous", {})

    async def _async_handler(req):
        return ToolMessage(content="async-ok", tool_call_id=req.tool_call_id, name=req.tool.name)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    result = await mw.awrap_tool_call(request, _async_handler)
    ticker.cancel()

    assert result.content == "async-ok"
    # If the loop had been frozen during the 0.3s blocking wait, the ticker
    # could not have advanced. Several ticks prove the loop stayed live.
    assert ticks >= 3


def test_resolve_transport_uses_static_when_set():
    t = InProcessApprovalTransport()
    mw = HITLApprovalMiddleware(policy=[], transport=t)
    assert mw._resolve_transport(_Req("x", {})) is t


def test_resolve_transport_from_request_workspace(tmp_path: Path):
    """With no static transport, the workspace is resolved PER REQUEST from
    ``request.state['workspace_path']`` — not from build-time env."""
    mw = HITLApprovalMiddleware(policy=[], transport=None)
    req = _Req("x", {}, state={"workspace_path": str(tmp_path)})
    transport = mw._resolve_transport(req)
    approvals = tmp_path / "approvals"
    assert approvals.is_dir()
    assert transport._requests_path == approvals / "requests.jsonl"
    assert transport._decisions_path == approvals / "decisions.jsonl"
    # Cached by workspace: same request workspace -> same transport object.
    assert mw._resolve_transport(req) is transport


def test_resolve_transport_distinct_per_workspace(tmp_path: Path):
    """A long-lived server serving many engagements gets a distinct transport
    per workspace, so approvals land in the correct workspace."""
    mw = HITLApprovalMiddleware(policy=[], transport=None)
    ws_a = tmp_path / "eng-a"
    ws_b = tmp_path / "eng-b"
    ta = mw._resolve_transport(_Req("x", {}, state={"workspace_path": str(ws_a)}))
    tb = mw._resolve_transport(_Req("x", {}, state={"workspace_path": str(ws_b)}))
    assert ta is not tb
    assert ta._requests_path == ws_a / "approvals" / "requests.jsonl"
    assert tb._requests_path == ws_b / "approvals" / "requests.jsonl"


def test_resolve_transport_falls_back_to_env(tmp_path: Path, monkeypatch):
    """When the request carries no ``workspace_path``, fall back to
    ``DECEPTICON_WORKSPACE_PATH``."""
    monkeypatch.setenv("DECEPTICON_WORKSPACE_PATH", str(tmp_path))
    mw = HITLApprovalMiddleware(policy=[], transport=None)
    transport = mw._resolve_transport(_Req("x", {}))
    assert transport._requests_path == tmp_path / "approvals" / "requests.jsonl"
