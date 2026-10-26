"""HITLApprovalMiddleware — human-in-the-loop checkpoint approval for high-impact tool calls.

Autonomous execution on real client infrastructure cannot be all-or-nothing.
Some actions need a human stop-the-line gate:

- Deploying a C2 implant on production.
- Dumping credentials from a domain controller.
- Lateral move into PCI / HIPAA scope.
- Pushing a SIEM detection rule that could mute real alerts.
- Any action whose blast radius exceeds the operator's stated risk tolerance.

This middleware intercepts tool calls whose names or arguments match a
configurable policy and pauses the run until the operator approves,
denies, or redirects the action via a pluggable transport.

Transport abstraction
---------------------
The middleware does NOT hardcode any specific event-log format. PR #303
(events.jsonl) and any future operator-UI bridge plug in via the
:class:`ApprovalTransport` protocol: the middleware emits an
:class:`ApprovalRequest`, awaits a :class:`ApprovalDecision`, and applies
it. The default :class:`InProcessApprovalTransport` is useful for unit
tests; the file-backed :class:`FileBackedApprovalTransport` reads/writes
a JSONL queue compatible with the eventual #303 wire format.

Policy
------
Approval policy is declarative and OPPLAN-matrix-aware: each entry maps
an ATT&CK technique tag (T1003, T1059, etc.) to a required approval
action. Tool-name patterns are a secondary lever for tools that don't
carry technique tags.

Resolution
----------
On a matching tool call, the middleware:
1. Builds an :class:`ApprovalRequest` describing the tool, args (with
   secret-redaction), engagement scope, and matched policy.
2. Calls ``transport.submit(request)`` to publish it to the operator UI.
3. Polls ``transport.wait_for_decision(request.id, timeout)`` up to the
   policy-configured timeout.
4. Applies the decision:
   - ``allow``: pass through to the inner handler.
   - ``deny``: return a structured ToolMessage explaining the denial.
   - ``redirect``: pass through the operator-supplied replacement args.
   - ``timeout`` (no response): default to ``deny`` for safety.

Stack ordering
--------------
Place above SafeCommand so RoE refusals still fire after operator
approval (operator approval cannot override RoE — defense in depth)::

    HITLApproval -> SafeCommand -> PromptInjectionShield -> <skill layer>

The TODO note marks where the Skillogy / OPPLAN-matrix landings will
shift the policy resolution: technique-tagged objectives will be the
primary policy key once the matrix lands; tool-name patterns become a
fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import override

log = logging.getLogger(__name__)


@dataclass
class ApprovalPolicyRule:
    """Single approval rule. Either ``technique_tag`` or ``tool_pattern`` MUST be set."""

    technique_tag: str | None = None
    tool_pattern: str | None = None
    timeout_seconds: float = 300.0
    default_on_timeout: str = "deny"
    reason: str = ""


@dataclass
class ApprovalRequest:
    """A pending approval emitted to the operator UI."""

    request_id: str
    engagement_name: str
    agent_name: str
    tool_name: str
    tool_args_redacted: dict[str, Any]
    matched_rule: ApprovalPolicyRule
    reason: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "kind": "approval_request",
                "request_id": self.request_id,
                "engagement_name": self.engagement_name,
                "agent_name": self.agent_name,
                "tool_name": self.tool_name,
                "tool_args_redacted": self.tool_args_redacted,
                "matched_rule": {
                    "technique_tag": self.matched_rule.technique_tag,
                    "tool_pattern": self.matched_rule.tool_pattern,
                    "timeout_seconds": self.matched_rule.timeout_seconds,
                    "default_on_timeout": self.matched_rule.default_on_timeout,
                },
                "reason": self.reason,
                "created_at": self.created_at,
                "metadata": self.metadata,
            },
            default=str,
        )


@dataclass
class ApprovalDecision:
    """Operator response: allow / deny / redirect."""

    request_id: str
    action: str
    operator_note: str = ""
    redirect_args: dict[str, Any] | None = None
    decided_at: float = 0.0


@runtime_checkable
class ApprovalTransport(Protocol):
    """Transport contract for shuttling approvals between agent runtime and operator UI.

    PR #303 events.jsonl + future WebSocket bridge implement this protocol.
    """

    def submit(self, request: ApprovalRequest) -> None: ...

    def wait_for_decision(self, request_id: str, timeout: float) -> ApprovalDecision | None: ...


class InProcessApprovalTransport:
    """In-memory transport for tests and single-process operator UIs.

    Operator code calls :meth:`provide_decision` to unblock a pending request.
    """

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, ApprovalDecision] = {}

    def submit(self, request: ApprovalRequest) -> None:
        self._pending[request.request_id] = request

    def provide_decision(self, decision: ApprovalDecision) -> None:
        self._decisions[decision.request_id] = decision

    def wait_for_decision(self, request_id: str, timeout: float) -> ApprovalDecision | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if request_id in self._decisions:
                return self._decisions.pop(request_id)
            time.sleep(0.05)
        return None

    def pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())


class FileBackedApprovalTransport:
    """JSONL-backed transport compatible with the planned events.jsonl wire format.

    Writes ``approval_request`` records to ``requests_path`` and polls
    ``decisions_path`` for matching ``approval_decision`` records. Both
    files are append-only; consumers (operator UI, web dashboard) tail
    ``requests_path`` and append to ``decisions_path``.

    Decisions are matched by ``request_id`` so multiple approvals can be
    in flight concurrently without ordering coupling. Once a decision is
    consumed the transport records the byte offset so a subsequent call
    starts reading from the next line.
    """

    def __init__(
        self,
        requests_path: str | Path,
        decisions_path: str | Path,
        *,
        poll_interval: float = 0.25,
    ) -> None:
        self._requests_path = Path(requests_path)
        self._decisions_path = Path(decisions_path)
        self._requests_path.parent.mkdir(parents=True, exist_ok=True)
        self._decisions_path.parent.mkdir(parents=True, exist_ok=True)
        self._poll_interval = poll_interval
        self._decisions_offset = 0
        self._consumed_ids: set[str] = set()
        self._pending_decisions: dict[str, ApprovalDecision] = {}

    def submit(self, request: ApprovalRequest) -> None:
        with self._requests_path.open("a", encoding="utf-8") as fh:
            fh.write(request.to_jsonl() + "\n")

    def _scan_decisions(self) -> list[ApprovalDecision]:
        if not self._decisions_path.exists():
            return []
        out: list[ApprovalDecision] = []
        with self._decisions_path.open("r", encoding="utf-8") as fh:
            fh.seek(self._decisions_offset)
            while True:
                line = fh.readline()
                if not line:
                    break
                self._decisions_offset = fh.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("malformed decision line skipped")
                    continue
                if obj.get("kind") != "approval_decision":
                    continue
                rid = obj.get("request_id")
                if not rid or rid in self._consumed_ids:
                    continue
                out.append(
                    ApprovalDecision(
                        request_id=rid,
                        action=str(obj.get("action") or "deny"),
                        operator_note=str(obj.get("operator_note") or ""),
                        redirect_args=obj.get("redirect_args"),
                        decided_at=float(obj.get("decided_at") or time.time()),
                    )
                )
        return out

    def wait_for_decision(self, request_id: str, timeout: float) -> ApprovalDecision | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for decision in self._scan_decisions():
                self._pending_decisions.setdefault(decision.request_id, decision)
            decision = self._pending_decisions.pop(request_id, None)
            if decision is not None:
                self._consumed_ids.add(request_id)
                return decision
            time.sleep(self._poll_interval)
        return None


_DEFAULT_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "private_key",
        "auth",
        "authorization",
        "credentials",
        "session",
        "cookie",
    }
)


def _redact(args: Any) -> Any:
    if isinstance(args, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _DEFAULT_SECRET_KEYS else _redact(v))
            for k, v in args.items()
        }
    if isinstance(args, list):
        return [_redact(v) for v in args]
    return args


class HITLApprovalMiddleware(AgentMiddleware):
    """Pause tool execution and request operator approval for high-impact actions.

    See module docstring for stack ordering and policy semantics.

    TODO(OPPLAN matrix migration): once the OPPLAN -> ATT&CK matrix
    redesign lands, policy resolution should prefer technique-tag
    matching from the active objective; tool-name patterns remain as a
    fallback for unmapped tools. The data shape here already supports
    technique-tag rules — only the resolution order needs to flip.

    TODO(events.jsonl wiring): PR #303 lands a unified events.jsonl
    event log. When it merges, replace the FileBackedApprovalTransport's
    bespoke files with the unified log: ``approval_request`` and
    ``approval_decision`` records share the same file, matched by
    ``kind`` and ``request_id``. The transport contract here does not
    change; only its implementation does.
    """

    def __init__(
        self,
        policy: list[ApprovalPolicyRule],
        transport: ApprovalTransport | None = None,
        *,
        engagement_name: str = "",
        agent_name: str = "",
        tool_policy: dict[str, str] | None = None,
    ) -> None:
        """``tool_policy`` — per-tool policy map from the run's configurable.

        Claude Code pattern (builtinToolPolicy): explicit per-tool policies
        combine with the technique-tag rules. Resolution per tool call:
        explicit tool_policy entry wins; otherwise the technique/pattern
        rules apply; unknown tools follow the default-on (no gate) path.
        Values: "allow" (pre-approved), "ask" (always gate), "deny"
        (blocked). SAFETY_CRITICAL tools ignore "allow" — they always ask.
        """
        super().__init__()
        self._tool_policy = {k: v for k, v in (tool_policy or {}).items()
                             if v in ("allow", "ask", "deny")}
        self._policy = policy
        # Static transport (tests / single-process UIs). When None, the
        # transport is resolved per request from the request's workspace
        # so a long-lived server serving many engagements writes
        # approvals to the correct workspace (see _resolve_transport).
        self._transport = transport
        self._transport_cache: dict[str, ApprovalTransport] = {}
        self._engagement_name = engagement_name
        self._agent_name = agent_name

    def _resolve_transport(self, request: Any) -> ApprovalTransport:
        if self._transport is not None:
            return self._transport
        state = getattr(request, "state", None) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        workspace = get("workspace_path") or os.environ.get(
            "DECEPTICON_WORKSPACE_PATH", "/workspace"
        )
        workspace = str(workspace)
        cached = self._transport_cache.get(workspace)
        if cached is not None:
            return cached
        approvals = Path(workspace) / "approvals"
        transport = FileBackedApprovalTransport(
            approvals / "requests.jsonl",
            approvals / "decisions.jsonl",
        )
        self._transport_cache[workspace] = transport
        return transport

    def _tool_policy_decision(self, tool_name: str) -> str | None:
        from decepticon.agents.build import SAFETY_CRITICAL_TOOLS  # noqa: PLC0415 — lazy to avoid a cycle
        """Return the per-tool policy for ``tool_name`` or None.

        SAFETY_CRITICAL tools ignore "allow" — their pre-approval bypasses
        the gate, so they are downgraded to "ask" here.
        """
        value = self._tool_policy.get(tool_name)
        if value == "allow" and tool_name in SAFETY_CRITICAL_TOOLS:
            return "ask"
        return value or None

    def _policy_denial(self, tool_name: str) -> ToolMessage:
        """Structured denial for tool_policy=deny — no transport round-trip."""
        tool_call_id = ""
        return ToolMessage(
            content=f"tool_policy denies {tool_name}; execution blocked by operator configuration",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def _match_rule(self, tool_name: str, technique_tag: str | None) -> ApprovalPolicyRule | None:
        for rule in self._policy:
            if rule.technique_tag and technique_tag and rule.technique_tag == technique_tag:
                return rule
            if rule.tool_pattern and re.search(rule.tool_pattern, tool_name):
                return rule
        return None

    def _engagement(self, request: Any) -> str:
        if self._engagement_name:
            return self._engagement_name
        state = getattr(request, "state", None) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        return get("engagement_name", "") or os.environ.get("DECEPTICON_ENGAGEMENT_ID", "")

    def _technique_tag(self, request: Any) -> str | None:
        state = getattr(request, "state", None) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        objective = get("current_objective") if get else None
        if isinstance(objective, dict):
            tag = objective.get("technique_id") or objective.get("mitre")
            if isinstance(tag, str):
                return tag
            if isinstance(tag, list) and tag:
                return str(tag[0])
        return None

    def _build_request(
        self,
        tool_name: str,
        tool_args: Any,
        rule: ApprovalPolicyRule,
        request: Any,
    ) -> ApprovalRequest:
        return ApprovalRequest(
            request_id=str(uuid.uuid4()),
            engagement_name=self._engagement(request),
            agent_name=self._agent_name,
            tool_name=tool_name,
            tool_args_redacted=_redact(tool_args or {}),
            matched_rule=rule,
            reason=rule.reason or "policy match",
            created_at=time.time(),
        )

    def _decision_to_response(
        self,
        decision: ApprovalDecision | None,
        rule: ApprovalPolicyRule,
        request: Any,
        handler,
    ):
        if decision is None:
            action = rule.default_on_timeout
            note = (
                f"no operator response within {rule.timeout_seconds:.0f}s; "
                f"defaulting to ``{action}``"
            )
        else:
            action = decision.action
            note = decision.operator_note

        tool_call_id = getattr(request, "tool_call_id", "") or ""
        tool_name = getattr(getattr(request, "tool", None), "name", "") if request else ""
        if action == "allow":
            return handler(request)
        if action == "redirect" and decision and decision.redirect_args is not None:
            redirected = request.override(tool_call_args=decision.redirect_args)
            return handler(redirected)
        denial_text = f"HITL gate denied tool call ``{tool_name}``" + (f": {note}" if note else "")
        return ToolMessage(
            content=denial_text,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    async def _adecision_to_response(
        self,
        decision: ApprovalDecision | None,
        rule: ApprovalPolicyRule,
        request: Any,
        handler,
    ):
        if decision is None:
            action = rule.default_on_timeout
            note = (
                f"no operator response within {rule.timeout_seconds:.0f}s; "
                f"defaulting to ``{action}``"
            )
        else:
            action = decision.action
            note = decision.operator_note

        tool_call_id = getattr(request, "tool_call_id", "") or ""
        tool_name = getattr(getattr(request, "tool", None), "name", "") if request else ""
        if action == "allow":
            return await handler(request)
        if action == "redirect" and decision and decision.redirect_args is not None:
            redirected = request.override(tool_call_args=decision.redirect_args)
            return await handler(redirected)
        denial_text = f"HITL gate denied tool call ``{tool_name}``" + (f": {note}" if note else "")
        return ToolMessage(
            content=denial_text,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    @override
    def wrap_tool_call(self, request, handler) -> ToolMessage | Command:
        tool = getattr(request, "tool", None)
        tool_name = getattr(tool, "name", "") if tool else ""
        tool_args = getattr(request, "tool_call_args", {}) or {}
        # Per-tool policy gate (Claude Code builtinToolPolicy pattern):
        # explicit deny returns a structured refusal without touching the
        # transport; explicit ask forces the gate even without a rule.
        policy = self._tool_policy_decision(tool_name)
        if policy == "deny":
            return self._policy_denial(tool_name)
        rule = self._match_rule(tool_name, self._technique_tag(request))
        if rule is None:
            if policy == "ask":
                rule = ApprovalPolicyRule(
                    tool_pattern=re.escape(tool_name),
                    timeout_seconds=300.0,
                    default_on_timeout="deny",
                    reason=f"tool_policy sets {tool_name} to ask",
                )
            else:
                return handler(request)
        transport = self._resolve_transport(request)
        appr = self._build_request(tool_name, tool_args, rule, request)
        transport.submit(appr)
        decision = transport.wait_for_decision(appr.request_id, rule.timeout_seconds)
        return self._decision_to_response(decision, rule, request, handler)

    @override
    async def awrap_tool_call(self, request, handler) -> ToolMessage | Command:
        tool = getattr(request, "tool", None)
        tool_name = getattr(tool, "name", "") if tool else ""
        tool_args = getattr(request, "tool_call_args", {}) or {}
        policy = self._tool_policy_decision(tool_name)
        if policy == "deny":
            return self._policy_denial(tool_name)
        rule = self._match_rule(tool_name, self._technique_tag(request))
        if rule is None:
            if policy == "ask":
                rule = ApprovalPolicyRule(
                    tool_pattern=re.escape(tool_name),
                    timeout_seconds=300.0,
                    default_on_timeout="deny",
                    reason=f"tool_policy sets {tool_name} to ask",
                )
            else:
                return await handler(request)
        import asyncio

        transport = self._resolve_transport(request)
        appr = self._build_request(tool_name, tool_args, rule, request)
        transport.submit(appr)
        # FileBackedApprovalTransport.wait_for_decision blocks on time.sleep;
        # offload to a worker thread so the LangGraph event loop stays live.
        decision = await asyncio.to_thread(
            transport.wait_for_decision, appr.request_id, rule.timeout_seconds
        )
        return await self._adecision_to_response(decision, rule, request, handler)


DEFAULT_HIGH_IMPACT_POLICY: list[ApprovalPolicyRule] = [
    ApprovalPolicyRule(
        technique_tag="T1003",
        timeout_seconds=300,
        default_on_timeout="deny",
        reason="OS Credential Dumping (T1003) requires operator approval.",
    ),
    ApprovalPolicyRule(
        technique_tag="T1486",
        timeout_seconds=600,
        default_on_timeout="deny",
        reason="Data Encrypted for Impact (T1486) — destructive operation.",
    ),
    ApprovalPolicyRule(
        technique_tag="T1485",
        timeout_seconds=600,
        default_on_timeout="deny",
        reason="Data Destruction (T1485) — destructive operation.",
    ),
    ApprovalPolicyRule(
        tool_pattern=r"^(sliver_implant|sliver_generate|c2_deploy)",
        timeout_seconds=300,
        default_on_timeout="deny",
        reason="C2 implant deployment requires explicit operator approval.",
    ),
    ApprovalPolicyRule(
        tool_pattern=r"^(sigma_to|yara_to)_",
        timeout_seconds=180,
        default_on_timeout="deny",
        reason="Pushing detection rules to production SIEM/EDR requires approval.",
    ),
    ApprovalPolicyRule(
        tool_pattern=r"^bash$",
        timeout_seconds=120,
        default_on_timeout="allow",
        reason="(informational) bash invocation logged for audit only.",
    ),
]
