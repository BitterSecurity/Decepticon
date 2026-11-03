"""Enforce machine RoE on configured tool calls and record decisions.

Rules are loaded from the request workspace's plan/roe.json. Missing or invalid
machine configuration is refused; legacy mode keys do not enable audit-only or
warning-only execution. Abort, time windows, command/target rules, concurrency
and cadence are checked around gated calls.

Target extraction is best-effort. The network boundary is exclusively the
OpenShell kernel policy: it is attached at sandbox creation by the trusted
resolver and provisioning here verifies the admitted policy hash against
the RoE compiled from the same machine block
(``decepticon.middleware.policy_compiler``). An unverifiable boundary, or
a backend without OpenShell authority, fails loudly instead of running
unprotected — the legacy in-container nftables path has been retired.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import override

from decepticon.middleware._audit_sink import RoEAuditSink
from decepticon.middleware._command_targets import extract_targets
from decepticon_core.types.roe import (
    Decision,
    MachineEnforcement,
    evaluate_command,
    evaluate_target,
    evaluate_time_window,
)

log = logging.getLogger(__name__)


GATED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "bash",
        "bash_output",
        "bash_kill",
        "http_request",
        "proxy_send_request",
        "browser_action",
        # Open-web engine tools (ADR-0010). web_fetch is target-gated on its
        # url; web_search is OSINT (allowlisted provider) — gated for audit/
        # throttle but target-exempt via a no-op extractor below.
        "web_fetch",
        "web_search",
        "detect_tech_stack",
    }
)


def _host_from_url(url: Any) -> list[str]:
    if not isinstance(url, str) or not url:
        return []
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return []
    return [host] if host else []


def _hosts_from_url_arg(args: dict[str, Any]) -> list[str]:
    return _host_from_url(args.get("url"))


def _hosts_from_browser_action(args: dict[str, Any]) -> list[str]:
    raw = args.get("params_json")
    if not isinstance(raw, str) or not raw:
        return []
    try:
        params = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(params, dict):
        return []
    return _host_from_url(params.get("url"))


def _hosts_from_web_search(_args: dict[str, Any]) -> list[str]:
    """OSINT exemption: web_search hits an allowlisted provider, not a target.

    Returning no hosts means the per-target scope check is a no-op (the call is
    still audited + throttled because web_search is in GATED_TOOL_NAMES).
    """
    return []


NETWORK_TARGET_EXTRACTORS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "http_request": _hosts_from_url_arg,
    "proxy_send_request": _hosts_from_url_arg,
    "web_fetch": _hosts_from_url_arg,
    "web_search": _hosts_from_web_search,
    "detect_tech_stack": _hosts_from_url_arg,
    "browser_action": _hosts_from_browser_action,
}


def _load_rules_for_workspace(workspace_path: str | None) -> MachineEnforcement:
    if not workspace_path:
        return MachineEnforcement(configured=False)
    roe_path = Path(workspace_path) / "plan" / "roe.json"
    if not roe_path.exists():
        return MachineEnforcement(configured=False)
    try:
        data = json.loads(roe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("roe: failed to read %s: %s; refusing gated tools", roe_path, exc)
        return MachineEnforcement(configured=False)
    block = data.get("machine_enforcement") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return MachineEnforcement(configured=False)
    return MachineEnforcement.from_dict(block)


def _abort_marker_present(workspace_path: str | None) -> bool:
    if not workspace_path:
        return False
    try:
        return (Path(workspace_path) / ".abort").exists()
    except OSError:
        return False


def _halted_message(tool_name: str, tool_call_id: str | None) -> ToolMessage:
    body = (
        f"[AGENT_HALTED] code=EMERGENCY_ABORT tool={tool_name}\n"
        "An out-of-band emergency abort was signalled by the operator "
        "(.abort marker in the workspace). This gated call was NOT executed.\n"
        "Stop work immediately and await operator instructions - do NOT "
        "retry or attempt alternative commands this turn."
    )
    return ToolMessage(content=body, tool_call_id=tool_call_id or "", status="error")


def _refused_message(decision: Decision, tool_name: str, tool_call_id: str | None) -> ToolMessage:
    body = (
        f"[ROE_REFUSED] code={decision.reason_code} tool={tool_name}\n"
        f"reason: {decision.reason_detail}\n\n"
        "The engagement's RoE blocked this call. If you believe this is a\n"
        "false positive, ask the operator to update plan/roe.json "
        "(machine_enforcement block) and re-run the objective. Continuing\n"
        "with a different target / different technique is the expected\n"
        "response - do NOT re-issue the same command this turn."
    )
    return ToolMessage(content=body, tool_call_id=tool_call_id or "", status="error")


def _warn_message(decision: Decision, tool_message: ToolMessage) -> ToolMessage:
    body = (
        f"[ROE_WARN] code={decision.reason_code}\n"
        f"reason: {decision.reason_detail}\n\n"
        "The tool call ran, but the RoE evaluator flagged it. Review the\n"
        "operator's intent before continuing.\n\n"
        "----- ORIGINAL TOOL OUTPUT -----\n"
        f"{_to_text(tool_message.content)}"
    )
    return ToolMessage(
        content=body,
        tool_call_id=tool_message.tool_call_id,
        status=tool_message.status,
        name=tool_message.name,
    )


def _to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
        return "".join(chunks)
    return str(content)


_REDACT_MASK = "***"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(sshpass\s+-p\s+)('[^']*'|\"[^\"]*\"|\S+)"),
    re.compile(r"(?i)(\bPGPASSWORD=)('[^']*'|\"[^\"]*\"|\S+)"),
    re.compile(
        r"(?i)((?:--password|--pass|--token)(?:[=\s]+)|-p\s+)"
        r"('[^']*'|\"[^\"]*\"|\S+)"
    ),
    re.compile(r"(?i)((?:-u|--user)\s+)('[^']*'|\"[^\"]*\"|[^\s:]+):(\S+)"),
    re.compile(
        r"(?i)((?:-H|--header)(?:[=\s]+))"
        r"('(?:[^']*(?:authorization|bearer|api-key|apikey|x-api-key)[^']*)'"
        r"|\"(?:[^\"]*(?:authorization|bearer|api-key|apikey|x-api-key)[^\"]*)\")",
    ),
    re.compile(r"(?i)([^\s/@:]+/[^\s/@:]+:)([^\s@]+)(@)"),
    re.compile(r"(?i)([A-Za-z][\w.-]*:)([^\s@:]+)(@[\w.-]+)"),
)


def _redact_header_value(match: re.Match[str]) -> str:
    flag, raw = match.group(1), match.group(2)
    quote = raw[0]
    inner = raw[1:-1]
    if ":" in inner:
        name, _, _value = inner.partition(":")
        return f"{flag}{quote}{name}: {_REDACT_MASK}{quote}"
    return f"{flag}{quote}{_REDACT_MASK}{quote}"


def _redact_secrets(cmd: str) -> str:
    if not cmd:
        return cmd
    out = cmd
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{_REDACT_MASK}", out)
    out = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}{_REDACT_MASK}", out)
    out = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}{_REDACT_MASK}", out)
    out = _SECRET_PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}:{_REDACT_MASK}", out)
    out = _SECRET_PATTERNS[4].sub(_redact_header_value, out)
    out = _SECRET_PATTERNS[5].sub(lambda m: f"{m.group(1)}{_REDACT_MASK}{m.group(3)}", out)
    out = _SECRET_PATTERNS[6].sub(lambda m: f"{m.group(1)}{_REDACT_MASK}{m.group(3)}", out)
    return out


def _tool_call_args(request) -> dict[str, Any]:
    args = getattr(request, "tool_call_args", None)
    if not isinstance(args, dict):
        last = getattr(request, "tool_call", None)
        args = getattr(last, "args", None) if last else None
    return args if isinstance(args, dict) else {}


def _command_from_tool_call(request) -> str:
    args = _tool_call_args(request)
    cmd = args.get("command") or args.get("cmd") or ""
    return cmd if isinstance(cmd, str) else ""


def _egress_disabled() -> bool:
    """Deprecated opt-out flag, retained only to warn the operator.

    The nftables in-sandbox firewall this flag used to bypass has been
    removed: OpenShell is the only network-boundary authority, and the
    kernel policy is attached at sandbox creation by the trusted resolver
    — there is no provisioning step left to disable. Setting the flag
    changes nothing; the run keeps its OpenShell boundary.
    """
    disabled = os.environ.get("DECEPTICON_EGRESS_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if disabled:
        log.warning(
            "roe: DECEPTICON_EGRESS_DISABLE is retired; OpenShell kernel "
            "policies are structural and cannot be disabled per-run"
        )
    return disabled


class RoEGuardrailMiddleware(AgentMiddleware):
    """Evaluate every bash tool call against the engagement's RoE.

    Args:
        sink: ``RoEAuditSink`` instance. The middleware records every
            evaluation (PASS, WARN, REFUSE) so the engagement deliverable
            carries the full record.
        gated_tools: Override the default tool-name set. Use this to
            extend enforcement to additional tools (e.g. an HTTP
            request tool).
        jitter_frac: Fraction of ``min_inter_request_delay_ms`` added as
            random OPSEC jitter on top of the floor when a gated call is
            paced (0 disables jitter; the floor is still honoured).
    """

    def __init__(
        self,
        *,
        sink: RoEAuditSink | None = None,
        gated_tools: frozenset[str] | None = None,
        now: Callable[[], datetime] | None = None,
        jitter_frac: float = 0.25,
    ) -> None:
        super().__init__()
        self._sink = sink
        self._gated = gated_tools or GATED_TOOL_NAMES
        self._conc_lock = threading.Lock()
        self._conc_limit: int | None = None
        self._sync_sema: threading.Semaphore | None = None
        self._async_sema: asyncio.Semaphore | None = None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._jitter_frac = max(0.0, jitter_frac)
        self._pace_lock = threading.Lock()
        self._last_gated_monotonic = 0.0
        self._egress_lock = threading.Lock()
        self._egress_provisioned: set[tuple[str, str, str]] = set()

    @override
    def before_agent(self, state, runtime) -> None:
        self._maybe_provision_egress(state, getattr(runtime, "config", None))
        return None

    @override
    async def abefore_agent(self, state, runtime) -> None:
        # Provisioning does a one-time synchronous HTTP round-trip to the
        # sandbox daemon; offload it so the event loop isn't blocked.
        await asyncio.to_thread(
            self._maybe_provision_egress, state, getattr(runtime, "config", None)
        )
        return None

    @override
    def wrap_tool_call(self, request, handler) -> ToolMessage | Command:
        return self._dispatch_sync(request, handler)

    @override
    async def awrap_tool_call(self, request, handler) -> ToolMessage | Command:
        return await self._dispatch_async(request, handler)

    def _dispatch_sync(self, request, handler):
        halt = self._check_abort(request)
        if halt is not None:
            return halt
        decision, rules, tool_name = self._evaluate(request)
        self._record(request, tool_name, decision)
        if not decision.allow:
            return _refused_message(decision, tool_name, _tcid(request))
        wait = self._pace_wait_seconds(rules)
        if wait > 0:
            self._record_throttle(request, tool_name, wait)
            time.sleep(wait)
        gated = tool_name in self._gated
        with self._sync_gate(rules, gated):
            result = handler(request)
        return result

    async def _dispatch_async(self, request, handler):
        halt = self._check_abort(request)
        if halt is not None:
            return halt
        decision, rules, tool_name = self._evaluate(request)
        self._record(request, tool_name, decision)
        if not decision.allow:
            return _refused_message(decision, tool_name, _tcid(request))
        wait = self._pace_wait_seconds(rules)
        if wait > 0:
            self._record_throttle(request, tool_name, wait)
            await asyncio.sleep(wait)
        gated = tool_name in self._gated
        async with self._async_gate(rules, gated):
            result = await handler(request)
        return result

    def _resolve_limit(self, rules: MachineEnforcement) -> int | None:
        limit = rules.max_concurrent_connections
        if limit is None or limit <= 0:
            return None
        with self._conc_lock:
            if self._conc_limit is None:
                self._conc_limit = limit
            return self._conc_limit

    @contextmanager
    def _sync_gate(self, rules: MachineEnforcement, gated: bool):
        limit = self._resolve_limit(rules) if gated else None
        if limit is None:
            yield
            return
        with self._conc_lock:
            if self._sync_sema is None:
                self._sync_sema = threading.Semaphore(limit)
            sema = self._sync_sema
        sema.acquire()
        try:
            yield
        finally:
            sema.release()

    @asynccontextmanager
    async def _async_gate(self, rules: MachineEnforcement, gated: bool):
        limit = self._resolve_limit(rules) if gated else None
        if limit is None:
            yield
            return
        with self._conc_lock:
            if self._async_sema is None:
                self._async_sema = asyncio.Semaphore(limit)
            sema = self._async_sema
        async with sema:
            yield

    def _check_abort(self, request) -> ToolMessage | None:
        tool = getattr(request, "tool", None)
        tool_name = getattr(tool, "name", "unknown") if tool else "unknown"
        if tool_name not in self._gated:
            return None
        state = getattr(request, "state", {}) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        workspace = get("workspace_path") or None
        if not _abort_marker_present(workspace):
            return None
        self._record_abort(request, tool_name)
        return _halted_message(tool_name, _tcid(request))

    def _record_abort(self, request, tool_name: str) -> None:
        if self._sink is None:
            return
        state = getattr(request, "state", {}) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        engagement = get("engagement_name") or "unknown-engagement"
        objective = get("active_objective_id") or get("current_objective") or ""
        record = {
            "ts": time.time(),
            "event": "abort",
            "engagement": engagement,
            "objective_id": objective,
            "tool": tool_name,
            "decision": "refuse",
            "reason_code": "EMERGENCY_ABORT",
            "command_excerpt": _command_from_tool_call(request)[:512],
        }
        try:
            self._sink.append(record)
        except Exception as exc:  # noqa: BLE001 - audit must never break tool execution
            log.error("roe: audit sink write failed: %s", exc)

    def _maybe_provision_egress(self, state, config=None) -> None:
        """Verify the OpenShell kernel boundary once per workspace.

        OpenShell is the only network-boundary authority: the trusted
        resolver attaches the compiled RoE policy at sandbox creation, so
        provisioning here verifies that the admitted policy hash still
        matches the workspace's RoE. A backend without OpenShell authority
        cannot provide a kernel network boundary at all — provisioning
        fails loudly and the workspace is unmarked so a later turn retries
        (there is no nftables fallback to quietly run on).
        """
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        workspace = get("workspace_path") or None
        if not workspace:
            return
        _egress_disabled()  # deprecated: warn only, never bypasses the boundary
        rules = _load_rules_for_workspace(workspace)
        # Every materialized RoE is enforced; there is no audit/warn bypass.

        # Hold admission until verification completes. A concurrent caller may
        # never treat an in-progress check as a successful cached admission.
        with self._egress_lock:
            from decepticon.backends.factory import build_sandbox_backend

            backend = build_sandbox_backend(config)
            if getattr(backend, "egress_authority", "") != "openshell":
                raise RuntimeError(
                    "RoE requires an admitted OpenShell kernel boundary; "
                    "configure DECEPTICON_SANDBOX_PROVIDER=openshell"
                )
            compiled = self._verify_openshell_boundary(state, workspace, rules, backend)
            key = (workspace, compiled.canonical_hash, backend.id)
            if key not in self._egress_provisioned:
                self._record_provision(state, compiled)
                self._egress_provisioned.add(key)

    def _verify_openshell_boundary(self, state, workspace, rules, backend):
        """Confirm the admitted OpenShell policy is compiled from this RoE.

        The kernel policy is the authoritative boundary, so it must be
        provably derived from ``plan/roe.json``: the admission's
        ``effective_policy_hash`` must equal the hash of the policy this
        module compiles from the same machine block. Anything else means
        the sandbox's network contract cannot be tied to the operator's
        scope and provisioning fails loudly for a later retry.
        """
        from decepticon.middleware.policy_compiler import compile_openshell_policy

        compiled = compile_openshell_policy(rules)
        if compiled.errors:
            raise RuntimeError(
                "RoE cannot be compiled into an OpenShell policy: " + "; ".join(compiled.errors)
            )
        backend.check_admission()
        admission = backend.current_admission()
        if admission.effective_policy_hash != compiled.canonical_hash:
            raise RuntimeError(
                "OpenShell admission policy hash does not match the compiled "
                f"RoE policy ({admission.effective_policy_hash[:12]} != "
                f"{compiled.canonical_hash[:12]}); refusing to admit agents "
                "on an unverifiable boundary"
            )
        log.info(
            "roe: openshell kernel boundary verified for %s: policy=%s",
            workspace,
            compiled.canonical_hash[:12],
        )

        return compiled

    def _record_provision(self, state, compiled) -> None:
        if self._sink is None:
            return
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        record = {
            "ts": time.time(),
            "engagement": get("engagement_name") or "unknown-engagement",
            "objective_id": get("active_objective_id") or get("current_objective") or "",
            "event": "egress_provisioned",
            "authority": "openshell",
            "policy_hash": compiled.canonical_hash,
        }
        try:
            self._sink.append(record)
        except Exception as exc:  # noqa: BLE001 - audit must never break tool execution
            log.error("roe: provisioning audit write failed: %s", exc)

    def _evaluate(self, request) -> tuple[Decision, MachineEnforcement, str]:
        tool = getattr(request, "tool", None)
        tool_name = getattr(tool, "name", "unknown") if tool else "unknown"
        if tool_name not in self._gated:
            return Decision.allow_default(), MachineEnforcement(), tool_name
        state = getattr(request, "state", {}) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        workspace = get("workspace_path") or None
        rules = _load_rules_for_workspace(workspace)
        if not rules.configured:
            return (
                Decision.refuse(
                    code="ROE_CONFIG_MISSING",
                    detail="machine-readable RoE is missing or invalid; gated tool execution is fail-closed",
                    risk="high",
                ),
                rules,
                tool_name,
            )
        time_decision = evaluate_time_window(self._now(), rules)
        if not time_decision.allow:
            return time_decision, rules, tool_name
        extractor = NETWORK_TARGET_EXTRACTORS.get(tool_name)
        if extractor is not None:
            hosts = extractor(_tool_call_args(request))
            for host in sorted(set(hosts)):
                target_decision = evaluate_target(host, rules)
                if not target_decision.allow:
                    return target_decision, rules, tool_name
            return Decision.allow_default(), rules, tool_name
        command = _command_from_tool_call(request)
        cmd_decision = evaluate_command(command, rules)
        if not cmd_decision.allow:
            return cmd_decision, rules, tool_name
        targets = extract_targets(command)
        for target in sorted(targets):
            target_decision = evaluate_target(target, rules)
            if not target_decision.allow:
                return target_decision, rules, tool_name
        return Decision.allow_default(), rules, tool_name

    def _record(
        self,
        request,
        tool_name: str,
        decision: Decision,
    ) -> None:
        if self._sink is None:
            return
        state = getattr(request, "state", {}) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        engagement = get("engagement_name") or "unknown-engagement"
        objective = get("active_objective_id") or get("current_objective") or ""
        record = {
            "ts": time.time(),
            "engagement": engagement,
            "objective_id": objective,
            "tool": tool_name,
            "decision": "allow" if decision.allow else "refuse",
            "reason_code": decision.reason_code,
            "reason_detail": decision.reason_detail,
            "risk": decision.risk,
            "matched_targets": list(decision.matched_targets),
            "enforced": True,
            "command_excerpt": _redact_secrets(_command_from_tool_call(request))[:512],
        }
        try:
            self._sink.append(record)
        except Exception as exc:  # noqa: BLE001 - audit must never break tool execution
            log.error("roe: audit sink write failed: %s", exc)

    def _pace_wait_seconds(self, rules: MachineEnforcement) -> float:
        """Seconds to sleep before a gated call to honour ``min_inter_request_delay_ms``.

        The first runtime request-cadence control (opplan only prints an OPSEC
        label). Isolated calls never wait; bursts are spaced to the floor plus
        up to ``jitter_frac`` of it, so the cadence is not a fixed-interval IOC.
        Never goes below the configured minimum; returns 0 when unset.
        """
        floor = rules.min_inter_request_delay_ms / 1000.0
        if floor <= 0:
            return 0.0
        now = time.monotonic()
        with self._pace_lock:
            start_at = max(now, self._last_gated_monotonic + floor)
            self._last_gated_monotonic = start_at
        wait = start_at - now
        if wait > 0 and self._jitter_frac > 0:
            wait += random.uniform(0.0, floor * self._jitter_frac)
        return wait

    def _record_throttle(self, request, tool_name: str, wait_seconds: float) -> None:
        if self._sink is None:
            return
        state = getattr(request, "state", {}) or {}
        get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
        record = {
            "ts": time.time(),
            "engagement": get("engagement_name") or "unknown-engagement",
            "objective_id": get("active_objective_id") or get("current_objective") or "",
            "tool": tool_name,
            "event": "throttle",
            "reason_code": "MIN_INTER_REQUEST_DELAY",
            "delay_seconds": round(wait_seconds, 3),
        }
        try:
            self._sink.append(record)
        except Exception as exc:  # noqa: BLE001 - audit must never break tool execution
            log.error("roe: throttle audit write failed: %s", exc)


def _tcid(request) -> str | None:
    tc = getattr(request, "tool_call_id", None)
    if isinstance(tc, str):
        return tc
    last = getattr(request, "tool_call", None)
    tc_attr = getattr(last, "id", None) if last else None
    return tc_attr if isinstance(tc_attr, str) else None


def build_default_sink(workspace_path: str | None) -> RoEAuditSink | None:
    """Best-effort default sink path: ``<workspace>/audit/roe-decisions.jsonl``.

    Returns ``None`` when no workspace is set yet (during initial
    bootstrapping) so the middleware degrades to "no-sink" cleanly.
    Operators that want a deterministic path can also set
    ``DECEPTICON_ROE_AUDIT_PATH`` in the env.
    """
    env_path = os.environ.get("DECEPTICON_ROE_AUDIT_PATH")
    if env_path:
        return RoEAuditSink(path=Path(env_path))
    if not workspace_path:
        return None
    return RoEAuditSink(path=Path(workspace_path) / "audit" / "roe-decisions.jsonl")


# Retained compatibility alias; the planned 2.0.0 removal did not occur.
# The class was renamed
# ``RoEEnforcementMiddleware`` → ``RoEGuardrailMiddleware`` when RoE
# enforcement became genuinely layered (advisory command-parse here +
# authoritative sandbox egress). Downstream plugins / downstream subagents
# that import the old name still resolve it; new code uses RoEGuardrailMiddleware.
RoEEnforcementMiddleware = RoEGuardrailMiddleware
