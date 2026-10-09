"""Context engineering utilities for LLM interaction robustness.

Provides:
- Hallucinated tool name recovery (return error to model, don't crash)
- Nullish argument coercion ("null"/empty string → None)
- Model capability adaptation (detect image support, strict schemas)
- Prompt cache point management
"""

from __future__ import annotations

import json
from typing import Any


def coerce_nullish(value: Any) -> Any:
    """Coerce nullish string values to None.

    Models sometimes emit "null", "None", "undefined", or empty strings
    when they mean None/null. This normalizes those to Python None.
    """
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in ("", "null", "none", "undefined", "n/a"):
            return None
    return value


def coerce_json_string(value: str, expected_type: str = "auto") -> Any:
    """Coerce a JSON string that might be a stringified object/array.

    Models sometimes wrap JSON in quotes. This detects and parses it.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return value


def handle_hallucinated_tool(tool_name: str, available_tools: list[str]) -> str:
    """Generate a helpful error message for a hallucinated tool name.

    Instead of crashing, return a message that helps the model recover.
    """
    # Find closest match
    best_match = ""
    best_score = 0
    name_lower = tool_name.lower()

    for t in available_tools:
        t_lower = t.lower()
        # Substring match
        if name_lower in t_lower or t_lower in name_lower:
            score = len(set(name_lower) & set(t_lower)) / max(len(name_lower), 1)
            if score > best_score:
                best_score = score
                best_match = t

    msg = f"Tool '{tool_name}' does not exist."
    if best_match:
        msg += f" Did you mean '{best_match}'?"
    msg += f" Available tools: {', '.join(sorted(available_tools)[:20])}"
    if len(available_tools) > 20:
        msg += f" ... and {len(available_tools) - 20} more."

    return json.dumps({"error": msg}, indent=2)


class ModelCapabilities:
    """Detect and track model capabilities for adaptive tool/prompt configuration."""

    def __init__(self, model_name: str = ""):
        self.model_name = model_name.lower()

    @property
    def supports_images(self) -> bool:
        """Whether this model supports image/vision content."""
        vision_models = [
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-4-vision",
            "claude-3",
            "claude-sonnet",
            "claude-opus",
            "claude-4",
            "gemini",
            "gemini-pro",
            "gemini-2",
            "llava",
            "cogvlm",
            "qwen-vl",
        ]
        return any(v in self.model_name for v in vision_models)

    @property
    def supports_strict_schemas(self) -> bool:
        """Whether this model handles strict JSON schemas well."""
        strict_models = ["gpt-4", "gpt-3.5", "claude", "gemini"]
        return any(m in self.model_name for m in strict_models)

    @property
    def supports_prompt_cache(self) -> bool:
        """Whether this model supports prompt caching."""
        return "claude" in self.model_name or "anthropic" in self.model_name

    @property
    def max_tool_calls_per_turn(self) -> int:
        """Recommended max parallel tool calls."""
        if "gpt-4" in self.model_name:
            return 128
        if "claude" in self.model_name:
            return 50
        return 20

    def filter_tools(self, tools: list[Any]) -> list[Any]:
        """Filter tools based on model capabilities (e.g. drop image tools for text-only)."""
        if self.supports_images:
            return tools
        return [t for t in tools if not getattr(t, "requires_vision", False)]


def build_cache_optimized_prompt(
    base_system: str,
    scope_block: str = "",
    skills_block: str = "",
    dynamic_context: str = "",
) -> str:
    """Build a system prompt with cache-point-optimized ordering.

    Claude's prompt caching works best with stable content at the start.
    Order: base system prompt (stable) → scope (per-engagement, semi-stable)
    → skills (per-agent, loaded once) → dynamic context (per-turn).
    """
    parts = [base_system]
    if scope_block:
        parts.append(f"\n\n## Engagement Scope\n{scope_block}")
    if skills_block:
        parts.append(f"\n\n## Loaded Skills\n{skills_block}")
    if dynamic_context:
        parts.append(f"\n\n## Current Context\n{dynamic_context}")
    return "\n".join(parts)
