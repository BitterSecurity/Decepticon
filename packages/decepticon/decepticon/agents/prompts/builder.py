"""Agent system prompt assembly pipeline.

Structured prompt composition with:

1. **Static/dynamic section separation** — static sections (identity, rules, environment)
   are stable and cacheable; dynamic sections (skills metadata, engagement context) vary
   per invocation. A cache boundary marker separates them for Anthropic prompt caching.

2. **Tool prompt co-location** — tool prompts live with their tool code (e.g.,
   decepticon/tools/bash/prompt.py) and are injected here with role-specific variations.

3. **Cross-cutting patterns** — faithful reporting, verification gates, output discipline
   are injected consistently across all operational agents.

4. **Section registry** — prompts are composed from named sections, making it easy to
   add, remove, or reorder sections without touching the assembly logic.

Usage:
    # Simple (backward-compatible)
    load_prompt("recon", shared=["bash"])

    # Builder API (structured)
    prompt = (
        PromptBuilder("recon")
        .with_tool_prompts(["bash"])
        .with_shared(["language"])
        .build()
    )
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from decepticon.agents.prompts.registry import build_language_policy

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent

# Search order for prompt fragment lookup. ``_read_fragment(name)`` tries
# each base directory in order, returning the first ``<base>/<name>.md``
# that exists. The top-level dir holds cross-cutting fragments
# (``language.md``); ``standard/`` and ``plugins/`` hold per-agent
# prompts mirroring the bundle split applied to ``decepticon/agents/``
# and ``skills/``. External plugin packages ship their own prompts via
# their own ``importlib.resources`` lookups; this list is OSS-internal.
_PROMPT_SEARCH_PATHS: tuple[Path, ...] = (
    _PROMPTS_DIR,
    _PROMPTS_DIR / "standard",
    _PROMPTS_DIR / "plugins",
)

# ── Cache boundary marker ────────────────────────────────────────────────────
# Inserted between static and dynamic sections. Anthropic's prompt caching
# heuristic uses this to determine the cacheable prefix boundary.
# Everything above this marker is stable across invocations and can be cached.
CACHE_BOUNDARY = "\n\n---\n\n"

# ── Cross-cutting prompt patterns ─────────────────────────────────────────────

_FAITHFUL_REPORTING = """\
<FAITHFUL_REPORTING>
Report findings exactly as observed. Do NOT:
- Speculate about vulnerabilities that scans did not confirm
- Inflate severity to appear more productive
- Omit negative results — "no findings" is a valid and valuable result
- Assume a service is vulnerable without evidence

If a tool returns empty output or no results, report that explicitly. The operator
needs ground truth to make decisions, not optimistic guesses. A false positive
wastes more time than a missed finding — the operator will direct further investigation.
</FAITHFUL_REPORTING>"""

_VERIFICATION_GATE = """\
<VERIFICATION_GATE>
Before declaring any finding CRITICAL or HIGH severity:
1. Verify with at least one additional method (different tool, manual confirmation, or secondary evidence)
2. Confirm the target is in scope (re-check roe.json if uncertain)
3. Document the evidence chain: what tool reported it, what confirmed it

Do NOT skip verification to save time. Unverified findings that turn out to be
false positives erode operator trust and waste downstream effort.
</VERIFICATION_GATE>"""

_OUTPUT_DISCIPLINE = """\
<OUTPUT_DISCIPLINE>
## Response Length Rules

- **Between tool calls**: Keep text to 1-2 sentences. State what you found and what you're doing next. No summaries, no analysis paragraphs — save those for the report.
- **Final report / handoff**: Be thorough and structured. Use the full format from RESPONSE_RULES.
- **When the operator asks a question**: Answer directly and concisely. Lead with the answer, not the reasoning.

Do NOT narrate your thought process ("Let me now check...", "I'll proceed to...").
Just execute. The operator can see your tool calls.
</OUTPUT_DISCIPLINE>"""

_FINDING_PROTOCOL_POINTER = """\
<FINDING_PROTOCOL>
Record each supported finding in a separate `findings/FIND-NNN.md` with its
asset, observed behavior, reproducing steps, evidence pointer, supported
impact, severity rationale, and explicit uncertainty. Keep verification and
report status as drafts until the independent quality stages complete.
Use the configured scoring tool for numeric scores; never infer impact from
the vulnerability class alone.
</FINDING_PROTOCOL>"""

_ANALYST_MINDSET = """\
<ANALYST_MINDSET>
You are an analyst and collaborator, not just a tool executor. This means:
- **Interpret results**: Don't just dump output — highlight what matters and why
- **Suggest next steps**: Based on findings, recommend the logical next action
- **Challenge assumptions**: If the current approach isn't yielding results, say so and propose alternatives
- **Connect the dots**: Relate new findings to previous discoveries across the engagement
</ANALYST_MINDSET>"""


@lru_cache(maxsize=32)
def _read_fragment(name: str) -> str:
    """Read and cache a prompt fragment file from any configured search path."""
    for base in _PROMPT_SEARCH_PATHS:
        path = base / f"{name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    searched = ", ".join(str(p / f"{name}.md") for p in _PROMPT_SEARCH_PATHS)
    msg = f"Prompt fragment not found: {name}.md (searched: {searched})"
    raise FileNotFoundError(msg)


# ── workflow inline (Skillogy Amendment v0.2.2) ──────────────────────────────
# Each specialist with a ``<role>.md`` file under
# ``packages/decepticon/decepticon/agents/prompts/workflows/`` gets it
# concatenated into the cacheable static prefix at agent factory time. The
# runtime skill middleware (``SkillogyMiddleware`` and the legacy
# ``SkillsMiddleware``) no longer loads workflow content — the body lives
# inside the prompt-cache boundary and the middleware owns no filesystem
# behavior.
#
# Workflows live next to the agent identity prompts (``prompts/standard/``),
# not under ``skills/``, because they are agent identity (phase loop, scope
# rules, OPSEC discipline, hand-off format) — not on-demand skill catalog
# entries. Roles without a workflow file get no extra content (no fallback,
# no warning — the absence is the contract). See "Amendment v0.2.2" in
# docs/design/skillogy-brain-redesign.md.

_WORKFLOWS_DIR = _PROMPTS_DIR / "workflows"


@lru_cache(maxsize=32)
def _read_workflow_for_role(role: str) -> str | None:
    """Return the role's workflow body, or ``None`` when absent.

    Resolution: ``prompts/workflows/<role>.md`` (role name only — no
    skill-directory aliasing). Cached per-role; the disk hit only fires
    on first factory build per Python process. ``None`` is the
    conventional "no workflow" signal — ``build()`` skips the section
    entirely so non-workflow roles pay zero prompt cost.
    """
    path = _WORKFLOWS_DIR / f"{role}.md"
    if not path.exists():
        return None
    body = path.read_text(encoding="utf-8").strip()
    return body or None


def _format_workflow_block(role: str, body: str) -> str:
    """Wrap the workflow body with a header naming its on-disk source.

    The header lets future readers locate the source file for edits.
    """
    return (
        "<AGENT_WORKFLOW>\n"
        f"## Always-Loaded Workflow — `agents/prompts/workflows/{role}.md`\n\n"
        f"{body}\n"
        "</AGENT_WORKFLOW>"
    )


def _get_tool_prompt(tool_name: str, role: str | None = None) -> str:
    """Get a tool's prompt from its co-located prompt module.

    Tool prompts live with their tool code (e.g., tools/bash/prompt.py).
    This allows each tool to own its documentation and provide role-specific
    variations.
    """
    if tool_name == "bash":
        from decepticon.tools.bash.prompt import BASH_PROMPT

        return BASH_PROMPT

    # Fallback: try reading from the prompts directory (backward compat)
    return _read_fragment(tool_name)


# ── Roles that get cross-cutting prompt patterns ─────────────────────────────
# The orchestrator (decepticon) and soundwave don't do direct tool execution,
# so they get a subset of the patterns.
_OPERATIONAL_ROLES = {
    "recon",
    "exploit",
    "postexploit",
    "analyst",
    "reverser",
    "contract_auditor",
    "cloud_hunter",
    "ad_operator",
}


class PromptBuilder:
    """Structured prompt assembly with static/dynamic section separation.

    Structured prompt assembly with static/dynamic section separation:
    - Static sections are loaded from .md files and tool prompt modules
    - Cross-cutting patterns are injected based on agent role
    - A cache boundary marker separates static from dynamic content
    - Dynamic sections (skills metadata, context) are appended last

    Example:
        prompt = (
            PromptBuilder("recon")
            .with_tool_prompts(["bash"])
            .with_shared(["language"])
            .with_dynamic("Current engagement: acme-corp-2026")
            .build()
        )
    """

    def __init__(self, role: str) -> None:
        self._role = role
        self._tool_prompts: list[str] = []
        self._shared: list[str] = []
        self._dynamic_sections: list[str] = []

    def with_tool_prompts(self, tools: list[str]) -> PromptBuilder:
        """Add tool prompts (co-located with tool code)."""
        self._tool_prompts = tools
        return self

    def with_shared(self, fragments: list[str]) -> PromptBuilder:
        """Add shared prompt fragments."""
        self._shared = fragments
        return self

    def with_dynamic(self, section: str) -> PromptBuilder:
        """Add a dynamic section (varies per invocation, not cached)."""
        self._dynamic_sections.append(section)
        return self

    def build(self) -> str:
        """Assemble the complete system prompt.

        Layout:
            [Agent identity + rules + environment]  ← from .md file (static)
            [Cross-cutting patterns]                ← faithful reporting, etc. (static)
            [Tool prompts]                          ← from tool prompt modules (static)
            --- cache boundary ---
            [Shared fragments]                      ← skills metadata (semi-dynamic)
            [Dynamic sections]                      ← engagement context (dynamic)
        """
        parts: list[str] = []

        # 1. Agent-specific prompt (identity, rules, environment, workflow)
        parts.append(_read_fragment(self._role))

        # 2. Cross-cutting prompt patterns
        if self._role in _OPERATIONAL_ROLES:
            parts.append(_FAITHFUL_REPORTING)
            parts.append(_VERIFICATION_GATE)
            parts.append(_FINDING_PROTOCOL_POINTER)
            parts.append(_OUTPUT_DISCIPLINE)
            parts.append(_ANALYST_MINDSET)
        elif self._role == "decepticon":
            # Orchestrator gets reporting + output discipline but not verification gate
            parts.append(_FAITHFUL_REPORTING)
            parts.append(_FINDING_PROTOCOL_POINTER)
            parts.append(_OUTPUT_DISCIPLINE)
            parts.append(_ANALYST_MINDSET)

        # 3. Tool prompts (co-located with tool code, role-specific)
        for tool_name in self._tool_prompts:
            parts.append(_get_tool_prompt(tool_name, self._role))

        # 4. Always-loaded workflow (Skillogy Amendment v0.2.2). Sits inside
        #    the cacheable prefix so the agent's phase loop / scope rules /
        #    OPSEC discipline / handoff format are loaded before any tool
        #    call without the middleware owning filesystem behavior.
        workflow_body = _read_workflow_for_role(self._role)
        if workflow_body:
            parts.append(_format_workflow_block(self._role, workflow_body))

        # ── Cache boundary ──
        # Everything above is static and cacheable by Anthropic prompt caching.
        # Everything below may change between invocations.
        parts.append(CACHE_BOUNDARY)

        # 4. Current date/time (prevents model from assuming training cutoff date)
        now = datetime.now(timezone.utc)
        parts.append(
            f"<CURRENT_DATE>Today is {now.strftime('%Y-%m-%d')} "
            f"(UTC {now.strftime('%H:%M')}). "
            f"Use this date for all reporting and timestamps.</CURRENT_DATE>"
        )

        # 5. Shared fragments (skills metadata — semi-dynamic, changes with skill updates)
        for fragment_name in self._shared:
            parts.append(_read_fragment(fragment_name))

        # 6. Dynamic sections (engagement context, runtime state)
        for section in self._dynamic_sections:
            parts.append(section)

        return "\n\n".join(parts)


def load_prompt(
    name: str,
    *,
    shared: list[str] | None = None,
    override: str | dict[str, str] | None = None,
) -> str:
    """Load an agent system prompt with structured assembly.

    Static identity/rules/environment load from ``<name>.md`` and combine
    with cross-cutting patterns (faithful reporting, verification gate,
    output discipline) and tool prompts. The Anthropic prompt-caching
    boundary separates the cacheable static prefix from per-invocation
    dynamic content.

    Plugin prompt overrides (``PluginBundle(prompts={...})`` under the
    ``decepticon.bundles`` entry-point group) apply automatically. Library
    callers can supply an explicit ``override`` — ``str`` for full
    replace, ``dict`` with ``prepend`` / ``append`` / ``replace`` keys.
    Explicit overrides win over plugin overrides on conflict.

    Args:
        name: Prompt filename without extension (e.g., "recon", "exploit").
        shared: Shared fragment names to append (e.g., ["bash", "skills"]).
            "bash" is special-cased to use the co-located tool prompt module.
        override: Optional explicit prompt override. ``None`` (default) =
            only plugin overrides apply.

    Returns:
        Assembled system prompt string.
    """
    shared = shared or []

    # Separate tool prompts from shared fragments
    tool_prompts = [s for s in shared if s == "bash"]
    fragments = [s for s in shared if s != "bash"]

    # Cross-cutting language policy — applies to every agent. Prepended once
    # so every prompt picks up the same operator-language directive without
    # per-agent edits.
    #
    # DECEPTICON_LANGUAGE env var pins the output language (e.g. "en", "ko",
    # "no"). When set, auto-detect is disabled and the agent always responds
    # in the pinned language. When unset, the language.md fragment's
    # auto-detect logic applies.
    if "language" not in fragments:
        fragments = ["language", *fragments]

    prompt = PromptBuilder(name).with_tool_prompts(tool_prompts).with_shared(fragments).build()

    # Runtime language pin: when DECEPTICON_LANGUAGE is set, replace the
    # default English policy with a pinned-language directive so every agent
    # replies in the configured locale regardless of input language. The
    # env-based path is the CLI / single-tenant convention; multi-tenant
    # launchers (multi-tenant web) override per-run via config.configurable.language
    # which EngagementContextMiddleware injects as a later SystemMessage.
    pinned_lang = os.environ.get("DECEPTICON_LANGUAGE", "").strip()
    lang_policy = build_language_policy(pinned_lang)
    if lang_policy is not None:
        import re

        prompt = re.sub(
            r"<LANGUAGE_POLICY>.*?</LANGUAGE_POLICY>",
            lang_policy,
            prompt,
            flags=re.DOTALL,
        )

    # Apply Claude 4.x compatibility shim (no-op for other model families).
    # See decepticon/agents/prompts/claude4_compat.py and docs/model-compatibility.md.
    try:
        from decepticon.agents.prompts.claude4_compat import apply_compat_for_role

        prompt = apply_compat_for_role(prompt, name)
    except Exception as exc:
        # Fail soft: never break prompt loading because of the compat shim.
        log.debug("Claude 4.x compat shim skipped (swallowed): %s", exc)

    # Plugin + explicit prompt overrides apply last so prepend/append wrap
    # the fully-assembled prompt (language policy + compat shim included).
    # Lazy import avoids a circular dependency between assembly and prompts.
    from decepticon.agents.build import resolve_prompt_overrides

    merged = resolve_prompt_overrides(name, override=override)
    if "replace" in merged:
        prompt = merged["replace"]
    if merged.get("prepend"):
        prompt = f"{merged['prepend']}\n\n{prompt}"
    if merged.get("append"):
        prompt = f"{prompt}\n\n{merged['append']}"

    return prompt
