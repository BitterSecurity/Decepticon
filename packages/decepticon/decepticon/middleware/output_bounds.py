"""Bounded tool output middleware with workspace spill.

Caps tool results at configurable limits. Oversized output is written to
the workspace and replaced with a reference path + head/tail preview.
Prevents a single large tool result from blowing the context window.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_MAX_LINES = 500
DEFAULT_MAX_BYTES = 30_000
SPILL_DIR = ".tool-output"


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _spill_dir() -> Path:
    d = _workspace() / SPILL_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def bound_output(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    tool_name: str = "",
) -> str:
    """Bound a tool output string, spilling to disk if needed."""
    text_bytes = text.encode("utf-8", errors="replace")
    lines = text.splitlines()

    needs_bound = len(text_bytes) > max_bytes or len(lines) > max_lines
    if not needs_bound:
        return text

    # Spill full output to disk
    spill_id = hashlib.sha1(text_bytes[:4096]).hexdigest()[:12]
    spill_name = f"{tool_name or 'output'}-{spill_id}.txt"
    spill_path = _spill_dir() / spill_name
    spill_path.write_text(text, encoding="utf-8")

    # Build head + tail preview
    head_lines = min(max_lines // 3, 50)
    tail_lines = min(max_lines // 3, 50)

    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:]) if len(lines) > head_lines + tail_lines else ""

    # Byte-aware truncation of head
    if len(head.encode("utf-8")) > max_bytes // 2:
        head = head.encode("utf-8")[: max_bytes // 2].decode("utf-8", errors="ignore")

    omitted = len(lines) - head_lines - tail_lines
    size_kb = len(text_bytes) / 1024

    parts = [
        f"[OUTPUT TRUNCATED: {len(lines)} lines, {size_kb:.1f} KB]",
        f"[Full output saved to: {spill_path}]",
        f"[Use: grep/sed/head/tail on {spill_path} to read specific sections]",
        "",
        "=== HEAD ===",
        head,
    ]
    if omitted > 0:
        parts.append(f"\n... [{omitted} lines omitted] ...\n")
    if tail:
        parts.append("=== TAIL ===")
        parts.append(tail)

    return "\n".join(parts)


class BoundedOutputMiddleware:
    """Middleware that bounds all tool outputs to prevent context overflow.

    Wraps the tool execution pipeline. Any tool result exceeding the
    configured limits is spilled to workspace and replaced with a
    head/tail preview + path reference.
    """

    def __init__(
        self,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self.max_lines = max_lines
        self.max_bytes = max_bytes

    def bound(self, text: str, tool_name: str = "") -> str:
        return bound_output(text, self.max_lines, self.max_bytes, tool_name)
