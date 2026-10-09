"""Operator steering tools — send, list, and acknowledge mid-run guidance.

``send_operator_guidance`` writes a JSONL entry to the engagement's
``guidance/inbox.jsonl`` so :class:`OperatorSteeringMiddleware` can inject it
into agent context on the next turn.

``list_operator_guidance`` peeks at the inbox without consuming messages —
useful for agents that need to inspect pending guidance before a turn drains
it.

``acknowledge_guidance`` records that specific guidance was acted upon by
appending an acknowledgment entry to the archive.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

log = logging.getLogger(__name__)

_VALID_KINDS = frozenset(
    {
        "guidance",
        "reprioritize",
        "exclude",
        "credential",
        "hint",
        "abort_objective",
    }
)

_VALID_PRIORITIES = frozenset({"low", "normal", "high", "critical"})


def _guidance_dir() -> Path | None:
    ws = os.environ.get("DECEPTICON_WORKSPACE_PATH")
    if not ws:
        return None
    return Path(ws) / "guidance"


def _inbox_path() -> Path | None:
    d = _guidance_dir()
    return d / "inbox.jsonl" if d else None


def _archive_path() -> Path | None:
    d = _guidance_dir()
    return d / "archive.jsonl" if d else None


@tool
def send_operator_guidance(
    text: str,
    kind: str = "guidance",
    priority: str = "normal",
) -> str:
    """Write operator guidance to the inbox for the running engagement.

    WHEN TO USE: Called by the operator (via CLI /guide or web dashboard)
    to steer the agent mid-run without restarting.

    Args:
        text: The guidance message.
        kind: One of: guidance, reprioritize, exclude, credential, hint, abort_objective
        priority: One of: low, normal, high, critical
    """
    inbox = _inbox_path()
    if inbox is None:
        return "ERROR: DECEPTICON_WORKSPACE_PATH not set — cannot write guidance."

    if kind not in _VALID_KINDS:
        return f"ERROR: invalid kind {kind!r} — must be one of {sorted(_VALID_KINDS)}"
    if priority not in _VALID_PRIORITIES:
        return f"ERROR: invalid priority {priority!r} — must be one of {sorted(_VALID_PRIORITIES)}"

    msg: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "text": text,
        "priority": priority,
        "timestamp": time.time(),
    }

    try:
        inbox.parent.mkdir(parents=True, exist_ok=True)
        with inbox.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, default=str) + "\n")
    except OSError as exc:
        log.error("failed to write operator guidance: %s", exc)
        return f"ERROR: failed to write guidance — {exc}"

    return f"Guidance queued (id={msg['id']}, kind={kind}, priority={priority})."


@tool
def list_operator_guidance() -> str:
    """List any pending operator guidance messages without consuming them.

    WHEN TO USE: To check if the operator has sent any guidance that has
    not yet been consumed by the steering middleware.
    """
    inbox = _inbox_path()
    if inbox is None:
        return "No workspace configured — guidance inbox unavailable."

    if not inbox.exists():
        return "No pending guidance messages."

    try:
        raw = inbox.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"ERROR: cannot read inbox — {exc}"

    if not raw:
        return "No pending guidance messages."

    messages: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not messages:
        return "No pending guidance messages."

    parts: list[str] = [f"{len(messages)} pending guidance message(s):\n"]
    for msg in messages:
        mid = msg.get("id", "?")
        kind = msg.get("kind", "guidance")
        priority = msg.get("priority", "normal")
        text = msg.get("text", "")
        parts.append(f"  [{mid}] ({kind}/{priority}) {text}")

    return "\n".join(parts)


@tool
def acknowledge_guidance(
    message_id: str,
    action_taken: str = "",
) -> str:
    """Acknowledge that operator guidance has been received and acted upon.

    WHEN TO USE: After the agent has processed operator guidance, call this
    to record an acknowledgment so the operator can see the agent acted on it.

    Args:
        message_id: The id of the guidance message to acknowledge.
        action_taken: Optional description of what the agent did in response.
    """
    archive = _archive_path()
    if archive is None:
        return "ERROR: DECEPTICON_WORKSPACE_PATH not set — cannot write acknowledgment."

    ack: dict[str, Any] = {
        "type": "acknowledgment",
        "message_id": message_id,
        "action_taken": action_taken,
        "acknowledged_at": time.time(),
    }

    try:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ack, default=str) + "\n")
    except OSError as exc:
        log.error("failed to write guidance acknowledgment: %s", exc)
        return f"ERROR: failed to write acknowledgment — {exc}"

    return f"Acknowledged guidance {message_id}."


STEERING_TOOLS = [send_operator_guidance, list_operator_guidance, acknowledge_guidance]

__all__ = [
    "STEERING_TOOLS",
    "send_operator_guidance",
    "list_operator_guidance",
    "acknowledge_guidance",
]
