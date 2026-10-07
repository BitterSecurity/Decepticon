"""Bounded graceful shutdown on SIGINT / SIGTERM (Windows: console events).

On first signal the handler best-effort persists operator-visible state
inside the engagement workspace, then exits with the conventional
``128 + signum`` code. A second signal within the duplicate window
forces immediate exit. The total flush is bounded by ``MAX_FLUSH_SECONDS``
regardless of how slow individual writes are.

The handler is decoupled from the LangGraph orchestrator via the
``state_provider`` callback so this module has zero import-time side
effects and is safe to import from any process.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

log = logging.getLogger("decepticon.runtime.shutdown")


LangGraphState: TypeAlias = Mapping[str, Any]


MAX_FLUSH_SECONDS: float = 5.0
"""Total upper bound on the synchronous post-signal flush.

The handler joins the worker thread with this deadline and exits even
if writes are still in flight when the deadline expires."""

PER_COMPONENT_FLUSH_SECONDS: float = 2.0
"""Per-component upper bound: one hung checkpoint writer cannot consume
the whole ``MAX_FLUSH_SECONDS`` budget and starve the remaining steps."""

DUPLICATE_SIGNAL_WINDOW_SECONDS: float = 2.0
"""Second signal within this window of the first forces immediate exit."""

EXIT_CODE_SIGINT: int = 130
EXIT_CODE_SIGTERM: int = 143


_install_lock = threading.RLock()
_state_lock = threading.RLock()
_installed: bool = False
_state_provider: Callable[[], LangGraphState] | None = None
_last_signal_at: float | None = None
_flushing: bool = False
_previous_handlers: dict[int, Any] = {}
_windows_handler_routine: Any = None


@dataclass
class _FlushResult:
    workspace: Path | None = None
    findings_written: int = 0
    opplan_written: bool = False
    partial_executive_written: bool = False
    event_written: bool = False
    errors: list[str] = field(default_factory=list)

    def summary_line(self, signal_name: str) -> str:
        if self.workspace is None:
            return f"[shutdown] {signal_name}: no workspace; skipped checkpoint"
        parts = [
            f"findings={self.findings_written}",
            f"opplan={'ok' if self.opplan_written else 'skip'}",
            f"executive={'ok' if self.partial_executive_written else 'skip'}",
        ]
        if self.event_written:
            parts.append("event=ok")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return f"[shutdown] {signal_name}: " + " ".join(parts) + f" -> {self.workspace}"


def install_shutdown_handlers(state_provider: Callable[[], LangGraphState]) -> None:
    """Install bounded graceful shutdown handlers.

    Idempotent: calling more than once with the same provider replaces
    the registered provider but does not restack OS-level handlers. The
    handler captures state through ``state_provider`` at signal time, so
    callers should keep the returned object pointing at the latest
    LangGraph state.
    """
    global _installed, _state_provider
    with _install_lock:
        _state_provider = state_provider
        if _installed:
            return
        _register_posix_handlers()
        if os.name == "nt":
            _register_windows_handler()
        _installed = True


def _register_posix_handlers() -> None:
    try:
        _previous_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, _on_signal)
    except (ValueError, OSError) as exc:
        log.warning("Could not register SIGINT handler: %s", exc)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is None:
        return
    try:
        _previous_handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, _on_signal)
    except (ValueError, OSError) as exc:
        log.warning("Could not register SIGTERM handler: %s", exc)


def _register_windows_handler() -> None:
    global _windows_handler_routine
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def _windows_handler(ctrl_type: int) -> bool:
        if ctrl_type in (0, 1):
            _on_signal(signal.SIGINT, None)
        else:
            _on_signal(getattr(signal, "SIGTERM", signal.SIGINT), None)
        return True

    routine = handler_type(_windows_handler)
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCtrlHandler.argtypes = (handler_type, wintypes.BOOL)
    kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    if not kernel32.SetConsoleCtrlHandler(routine, True):
        log.warning("SetConsoleCtrlHandler registration returned 0")
        return
    _windows_handler_routine = routine


def _on_signal(signum: int | None, frame: Any) -> None:
    del frame
    global _last_signal_at, _flushing
    now = time.monotonic()
    sig_name = _signal_name(signum)
    exit_code = _exit_code_for(signum)

    with _state_lock:
        prev = _last_signal_at
        _last_signal_at = now
        already_flushing = _flushing

    if prev is not None and (now - prev) <= DUPLICATE_SIGNAL_WINDOW_SECONDS:
        _force_exit(exit_code, reason="duplicate signal")
        return
    if already_flushing:
        _force_exit(exit_code, reason="second signal during flush")
        return

    with _state_lock:
        _flushing = True

    deadline = time.monotonic() + MAX_FLUSH_SECONDS
    result_holder: dict[str, _FlushResult] = {}

    def _worker() -> None:
        try:
            result_holder["r"] = _run_flush(sig_name, deadline)
        except Exception as exc:  # noqa: BLE001 - last line of defense
            result_holder["r"] = _FlushResult(errors=[f"flush crashed: {exc!r}"])

    worker = threading.Thread(target=_worker, name="decepticon-shutdown-flush", daemon=True)
    worker.start()
    worker.join(timeout=MAX_FLUSH_SECONDS)

    result = result_holder.get(
        "r",
        _FlushResult(errors=["flush exceeded deadline; partial state"]),
    )
    try:
        sys.stdout.write(result.summary_line(sig_name) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass

    _force_exit(exit_code, reason="flush complete")


def _signal_name(signum: int | None) -> str:
    if signum is None:
        return "SIGNAL"
    try:
        return signal.Signals(signum).name
    except (ValueError, KeyError):
        return f"signal-{signum}"


def _exit_code_for(signum: int | None) -> int:
    if signum is None:
        return EXIT_CODE_SIGINT
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None and signum == sigterm:
        return EXIT_CODE_SIGTERM
    return EXIT_CODE_SIGINT


def _force_exit(code: int, *, reason: str) -> None:
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    log.debug("Force exit (%s) code=%d", reason, code)
    os._exit(code)


def _run_flush(signal_name: str, deadline: float) -> _FlushResult:
    result = _FlushResult()
    state: LangGraphState | None = None
    if _state_provider is not None:
        try:
            state = _state_provider()
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"state_provider failed: {exc!r}")
            return result
    if state is None:
        return result

    workspace = _resolve_workspace(state)
    result.workspace = workspace
    if workspace is None:
        return result

    ok, val = _run_step(
        "findings", lambda: _write_inflight_findings(state, workspace), deadline, result
    )
    if ok and isinstance(val, int):
        result.findings_written = val

    ok, val = _run_step(
        "opplan", lambda: _write_opplan_checkpoint(state, workspace), deadline, result
    )
    if ok:
        result.opplan_written = bool(val)

    ok, val = _run_step(
        "executive", lambda: _write_partial_executive(state, workspace), deadline, result
    )
    if ok:
        result.partial_executive_written = bool(val)

    ok, val = _run_step(
        "event",
        lambda: _write_checkpoint_event_if_available(state, workspace, result, signal_name),
        deadline,
        result,
    )
    if ok:
        result.event_written = bool(val)

    return result


def _run_step(
    label: str,
    func: Callable[[], Any],
    deadline: float,
    result: _FlushResult,
) -> tuple[bool, Any]:
    """Run ``func`` bounded by ``min(PER_COMPONENT_FLUSH_SECONDS, time_left)``.

    The ``_write_*`` helpers are synchronous filesystem calls, so we cannot
    cancel them cooperatively; instead each runs in its own short-lived
    daemon thread and we ``join`` with a per-component timeout. On timeout
    we record a structured error and return ``(False, None)`` so the next
    checkpoint step can still execute within the remaining total budget.
    """
    cap = min(PER_COMPONENT_FLUSH_SECONDS, _time_left(deadline))
    if cap <= 0:
        return False, None
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["v"] = func()
        except Exception as exc:  # noqa: BLE001 - reported via result.errors
            box["e"] = exc

    t = threading.Thread(target=_runner, name=f"decepticon-shutdown-{label}", daemon=True)
    t.start()
    t.join(timeout=cap)
    if t.is_alive():
        result.errors.append(f"{label}: timed out after {cap:.2f}s")
        return False, None
    if "e" in box:
        result.errors.append(f"{label}: {box['e']!r}")
        return False, None
    return True, box.get("v")


def _time_left(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _resolve_workspace(state: LangGraphState) -> Path | None:
    raw = state.get("workspace_path") if isinstance(state, Mapping) else None
    if not raw:
        return None
    path = Path(str(raw))
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
    return path


def _write_atomic(target: Path, content: str | bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    if isinstance(content, bytes):
        tmp.write_bytes(content)
    else:
        tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


def _next_finding_id(findings_dir: Path) -> int:
    if not findings_dir.exists():
        return 1
    highest = 0
    for f in findings_dir.glob("FIND-*.md"):
        stem = f.stem
        suffix = stem.split("-", 1)[1] if "-" in stem else ""
        try:
            n = int(suffix)
        except ValueError:
            continue
        if n > highest:
            highest = n
    return highest + 1


def _write_inflight_findings(state: LangGraphState, workspace: Path) -> int:
    if not isinstance(state, Mapping):
        return 0
    candidates = state.get("pending_findings") or state.get("findings") or []
    if not isinstance(candidates, (list, tuple)):
        return 0
    findings_dir = workspace / "findings"
    written = 0
    next_id = _next_finding_id(findings_dir)
    for raw in candidates:
        if not isinstance(raw, Mapping):
            body = _finding_fallback_body(raw)
        else:
            body = _finding_markdown(raw)
        target = findings_dir / f"FIND-{next_id:03d}.md"
        _write_atomic(target, body)
        written += 1
        next_id += 1
    return written


def _finding_markdown(finding: Mapping[str, Any]) -> str:
    title = str(finding.get("title", "Untitled finding"))
    severity = str(finding.get("severity", "info")).upper()
    summary = str(finding.get("summary", ""))
    evidence = finding.get("evidence")
    lines = [
        f"# {title}",
        "",
        f"- **Severity:** {severity}",
        "- **Captured by:** graceful-shutdown checkpoint",
        "",
    ]
    if summary:
        lines += ["## Summary", "", summary, ""]
    if evidence is not None:
        lines += [
            "## Evidence (raw, captured at shutdown)",
            "",
            "```json",
            json.dumps(evidence, indent=2, sort_keys=True, default=str),
            "```",
            "",
        ]
    return "\n".join(lines)


def _finding_fallback_body(raw: Any) -> str:
    return (
        "# Untitled finding (raw capture)\n\n"
        "- **Severity:** UNKNOWN\n"
        "- **Captured by:** graceful-shutdown checkpoint\n\n"
        "## Raw payload\n\n```\n"
        f"{raw!r}\n"
        "```\n"
    )


def _write_opplan_checkpoint(state: LangGraphState, workspace: Path) -> bool:
    if not isinstance(state, Mapping):
        return False
    objectives = state.get("objectives")
    if objectives is None:
        return False
    payload: dict[str, Any] = {
        "schema_version": "2",
        "saved_at": time.time(),
        "engagement_name": state.get("engagement_name", "engagement"),
        "threat_profile": state.get("threat_profile") or "",
        "revision": state.get("plan_revision", 0),
        "facts": _jsonable(state.get("plan_facts") or []),
        "objectives": _jsonable(objectives),
    }
    target = workspace / "plan" / "opplan.json"
    _write_atomic(target, json.dumps(payload, indent=2, default=str))
    return True


def _write_partial_executive(state: LangGraphState, workspace: Path) -> bool:
    if not isinstance(state, Mapping):
        return False
    engagement = str(state.get("engagement_name") or "Engagement")
    graph = state.get("knowledge_graph") or state.get("graph")
    body: str
    if graph is not None:
        try:
            from decepticon.tools.reporting.executive import render_executive_summary

            body = render_executive_summary(graph, engagement_name=engagement)
        except Exception as exc:  # noqa: BLE001
            body = _fallback_executive(engagement, state, note=f"graph render failed: {exc!r}")
    else:
        body = _fallback_executive(engagement, state, note="no knowledge graph in state")
    target = workspace / "report" / "report_partial_executive.md"
    _write_atomic(target, body)
    return True


def _fallback_executive(engagement: str, state: LangGraphState, *, note: str) -> str:
    objectives = state.get("objectives") if isinstance(state, Mapping) else None
    revision = state.get("plan_revision") if isinstance(state, Mapping) else None
    lines = [
        f"# {engagement} — Partial Executive Summary (graceful shutdown)",
        "",
        "## Status",
        "",
        f"- {note}",
        "- Persisted from in-memory state at shutdown; not a final report.",
        "",
        "## OPPLAN snapshot",
        "",
        f"- plan revision: {revision!r}",
        f"- objectives recorded: {len(objectives) if isinstance(objectives, (list, tuple)) else 'unknown'}",
        "",
    ]
    return "\n".join(lines)


def _write_checkpoint_event_if_available(
    state: LangGraphState,
    workspace: Path,
    result: _FlushResult,
    signal_name: str,
) -> bool:
    events_path = workspace / "events.jsonl"
    if not events_path.exists():
        return False
    event = {
        "ts": time.time(),
        "type": "engagement.checkpoint",
        "reason": "signal",
        "signal": signal_name,
        "findings_written": result.findings_written,
        "partial_executive": "report/report_partial_executive.md"
        if result.partial_executive_written
        else None,
        "opplan_checkpoint": "plan/opplan.json" if result.opplan_written else None,
    }
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")
    return True


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))
