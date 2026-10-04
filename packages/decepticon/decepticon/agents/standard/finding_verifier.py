"""Independent finding adjudication for standard red-team engagements."""

from __future__ import annotations

from typing import Any

from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled


def create_finding_verifier(**overrides: Any):
    """Reuse the OSS verifier's sandbox and positive/negative-control tools."""
    from decepticon.agents.plugins.verifier import create_verifier_agent
    from decepticon.agents.prompts import load_prompt

    overrides.setdefault("system_prompt", load_prompt("finding_verifier", shared=["bash"]))
    return create_verifier_agent(**overrides)


SUBAGENT_SPEC = SubAgentSpec(
    name="finding_verifier",
    description=(
        "Independently adjudicate one findings/FIND-NNN.md using the recorded evidence, "
        "a fresh positive reproduction and equivalent negative control. Preserve the "
        "canonical finding and set verification_status plus verification_rationale."
    ),
    factory=create_finding_verifier,
    parent_agents=("decepticon",),
    bundle="standard",
)


if is_bundle_enabled("standard"):
    graph = create_finding_verifier()
