"""Mid-run operator steering middleware.

Allows operators to inject guidance, reprioritize objectives, add exclusions,
or provide hints without killing and restarting the run.  Guidance is written
to ``workspace/<slug>/guidance/inbox.jsonl`` (by the CLI ``/guide`` command or
the web dashboard) and drained by this middleware at the start of each agent
turn.

Hook: ``before_model`` — runs every turn so injected guidance lands on the
very next inference step.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

log = logging.getLogger(__name__)

# Recognised guidance kinds — anything else falls through as generic guidance.
_KINDS = frozenset(
    {
        "guidance",
        "reprioritize",
        "exclude",
        "credential",
        "hint",
        "abort_objective",
    }
)


class OperatorSteeringMiddleware(AgentMiddleware):
    """Drain operator guidance inbox and inject into agent context."""

    INBOX_FILENAME = "inbox.jsonl"
    ARCHIVE_FILENAME = "archive.jsonl"

    def __init__(self) -> None:
        super().__init__()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def guidance_dir(self) -> Path | None:
        ws = os.environ.get("DECEPTICON_WORKSPACE_PATH")
        if not ws:
            return None
        return Path(ws) / "guidance"

    @property
    def inbox_path(self) -> Path | None:
        d = self.guidance_dir
        return d / self.INBOX_FILENAME if d else None

    @property
    def archive_path(self) -> Path | None:
        d = self.guidance_dir
        return d / self.ARCHIVE_FILENAME if d else None

    # ------------------------------------------------------------------
    # Inbox drain
    # ------------------------------------------------------------------

    def drain_inbox(self) -> list[dict[str, Any]]:
        """Read and archive all pending guidance messages.

        Atomicity: the inbox is read in full, archived, then cleared.  In
        the unlikely event that the archive write fails the inbox is NOT
        cleared so the messages survive for the next turn.
        """
        inbox = self.inbox_path
        if inbox is None or not inbox.exists():
            return []

        messages: list[dict[str, Any]] = []
        try:
            raw = inbox.read_text(encoding="utf-8").strip()
            if not raw:
                return []
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("failed to read operator guidance inbox: %s", exc)
            return []

        if not messages:
            return []

        # Archive consumed messages before clearing the inbox.
        archive = self.archive_path
        if archive:
            try:
                archive.parent.mkdir(parents=True, exist_ok=True)
                with archive.open("a", encoding="utf-8") as fh:
                    for msg in messages:
                        msg["consumed_at"] = time.time()
                        fh.write(json.dumps(msg, default=str) + "\n")
            except OSError as exc:
                log.warning(
                    "failed to archive operator guidance — leaving inbox intact: %s",
                    exc,
                )
                return []

        # Clear inbox only after successful archive.
        try:
            inbox.write_text("", encoding="utf-8")
        except OSError as exc:
            log.warning("failed to clear guidance inbox: %s", exc)

        log.info("drained %d operator guidance message(s)", len(messages))
        return messages

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_guidance(messages: list[dict[str, Any]]) -> str:
        """Format guidance messages for injection into agent context."""
        parts: list[str] = ["\n## 🎯 Operator Guidance (injected mid-run)\n"]
        for msg in messages:
            kind = msg.get("kind", "guidance")
            text = msg.get("text", "")
            priority = msg.get("priority", "normal")

            if kind == "reprioritize":
                parts.append(f"**REPRIORITIZE**: {text}")
            elif kind == "exclude":
                parts.append(f"**NEW EXCLUSION**: {text} — do NOT touch this target/path.")
            elif kind == "credential":
                parts.append(f"**CREDENTIAL PROVIDED**: {text}")
            elif kind == "hint":
                parts.append(f"**OPERATOR HINT**: {text}")
            elif kind == "abort_objective":
                parts.append(f"**ABORT OBJECTIVE**: {text} — stop working on this immediately.")
            else:
                parts.append(f"**GUIDANCE** [{priority}]: {text}")

        parts.append("\n---\n")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    def _inject(self) -> dict[str, Any] | None:
        """Core logic shared by sync and async hooks."""
        messages = self.drain_inbox()
        if not messages:
            return None
        guidance_text = self.format_guidance(messages)
        return {"messages": [SystemMessage(content=guidance_text)]}

    def before_model(self, state, runtime):  # type: ignore[override]
        return self._inject()

    async def abefore_model(self, state, runtime):  # type: ignore[override]
        return self._inject()
