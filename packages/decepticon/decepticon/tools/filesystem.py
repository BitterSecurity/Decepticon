"""Filesystem tool-surface helpers for Decepticon middleware."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def filesystem_tools_without_execute(tools: Sequence[Any]) -> list[Any]:
    """Return filesystem tools supported by the engagement-scoped backend.

    Decepticon routes command execution through its dedicated bash tools, while
    Deep Agents' filesystem middleware owns file operations. Keeping this
    selection helper in ``decepticon.tools`` makes the public tool surface
    explicit and keeps middleware focused on backend scoping. Recursive delete
    is also omitted: upstream added it after the scoped backend's contract, and
    it must not bypass the existing Plan and immutable-RoE protections.
    """
    return [tool for tool in tools if getattr(tool, "name", None) not in {"execute", "delete"}]


__all__ = ["filesystem_tools_without_execute"]
