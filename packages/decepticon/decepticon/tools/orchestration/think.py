"""Think tool — no-side-effect chain-of-thought scratchpad."""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def think(thought: str) -> str:
    """Use this tool to think through a problem step by step.

    WHEN TO USE: When you need to reason through a complex decision,
    plan an attack chain, or work through exploit logic without taking
    any action. This tool has NO side effects.

    Args:
        thought: Your reasoning/analysis
    """
    return "Thought recorded. Continue with your next action."


THINK_TOOLS = [think]
