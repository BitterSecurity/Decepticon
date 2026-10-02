"""Middleware slot enum and per-role applicability mapping.

The Decepticon agent factories assemble their middleware stack by
walking ``MiddlewareSlot`` in declaration order; each role declares
which slots apply via ``SLOTS_PER_ROLE``.

This module is the pure-data half of the original
``decepticon.agents.middleware_slots``. The langchain/deepagents-bound
default factories (``DEFAULT_SLOT_FACTORIES`` and the ``_make_*``
helpers) stay in the framework — keeping core langchain-free.

Plugin authors implementing custom middleware reach for
``MiddlewareSlot`` and the ``SAFETY_CRITICAL_SLOTS`` set when wiring
``PluginBundle.replaced_middleware`` / ``disabled_middleware``.
"""

from __future__ import annotations

from enum import StrEnum


class MiddlewareSlot(StrEnum):
    """Named slots in the agent middleware stack.

    Enum declaration order = assembly order. The 16 agent factories
    walk this enum top-to-bottom; only slots in ``SLOTS_PER_ROLE[role]``
    are instantiated.
    """

    ENGAGEMENT_CONTEXT = "engagement-context"
    ROE_GUARDRAIL = "roe-guardrail"
    HITL_APPROVAL = "hitl-approval"
    UNTRUSTED_OUTPUT = "untrusted-output"
    PROMPT_INJECTION_SHIELD = "prompt-injection-shield"
    SKILLS = "skills"
    FILESYSTEM = "filesystem"
    SUBAGENT = "subagent"
    OPPLAN = "opplan"
    KG = "kg"
    EVENT_LOG = "event-log"
    SANDBOX_NOTIFICATION = "sandbox-notification"
    # ADR-0006: the orchestrator-only middleware that turns opscontrol
    # daemon state transitions into Claude-Code-style
    # ``● Workload 'ad': starting → running`` system-reminders so the
    # agent does not poll ``ops_status`` in a loop.
    OPSCONTROL_NOTIFICATION = "opscontrol-notification"
    BUDGET = "budget"
    MODEL_OVERRIDE = "model-override"
    MODEL_FALLBACK = "model-fallback"
    # Per-run LiteLLM virtual-key swap (multi-tenant cost attribution). Placed
    # AFTER MODEL_FALLBACK so it is inner-most relative to model selection and
    # re-keys whatever model is actually called (primary, /model override, or a
    # fallback model). No-op when no per-run key is threaded (OSS default).
    PROXY_KEY_OVERRIDE = "proxy-key-override"
    SUMMARIZATION = "summarization"
    PROMPT_CACHING = "prompt-caching"
    PATCH_TOOL_CALLS = "patch-tool-calls"

    # ── Compat alias (removed at 2.0.0) ──
    # The slot was renamed ``ROE_ENFORCEMENT`` → ``ROE_GUARDRAIL`` when
    # enforcement became genuinely layered (advisory command-parse +
    # authoritative sandbox egress); the parser is one guardrail among
    # several, not *the* enforcement. This alias keeps plugin bundles
    # that reference ``MiddlewareSlot.ROE_ENFORCEMENT`` resolving to the
    # same member. As an enum alias it shares ROE_GUARDRAIL's value and
    # assembly position, and is excluded from iteration — so the slot is
    # never assembled twice.
    ROE_ENFORCEMENT = "roe-guardrail"


SAFETY_CRITICAL_SLOTS: frozenset[MiddlewareSlot] = frozenset(
    {
        # EngagementContextMiddleware carries RoE constraints into every
        # tool call - disabling it lets an agent target out-of-scope
        # hosts without any guard rail. Replacement is fine if the new
        # middleware honours the same contract; full disable is the
        # actual hazard.
        MiddlewareSlot.ENGAGEMENT_CONTEXT,
        # RoEGuardrailMiddleware is the legal/safety gate for every
        # bash tool call. Disabling it lets the agent attempt commands
        # against out-of-scope targets without record. Replacement is
        # fine if the new middleware honours the same contract (target
        # extraction + RoE evaluation + chained audit log).
        MiddlewareSlot.ROE_GUARDRAIL,
        # UntrustedOutputMiddleware structurally separates attacker-
        # influenceable tool output (bash stdout, file reads, KG
        # queries) from authoritative instructions via the
        # <UNTRUSTED_TOOL_OUTPUT> envelope + system policy block.
        # Disabling it means a hostile HTTP response, banner, or file
        # content can re-author the agent's instructions for any
        # downstream model call. Replacement is fine if the new
        # middleware honours the same contract.
        MiddlewareSlot.UNTRUSTED_OUTPUT,
        # PromptInjectionShieldMiddleware is a deny-list defense that
        # wraps attacker-controlled tool output (HTTP bodies, banners,
        # file reads) before it reaches a downstream model call. Like
        # UNTRUSTED_OUTPUT it is in every role's baseline; disabling it
        # lets hostile tool output re-author the agent's instructions
        # with no guard rail. Replacement is fine if the new middleware
        # honours the same contract; full disable is the actual hazard.
        MiddlewareSlot.PROMPT_INJECTION_SHIELD,
        # SandboxNotification tracks background-job completion + emits
        # the CLI's ``? Background command`` event. Disabling it leaves
        # operator visibility broken on every background tool call.
        MiddlewareSlot.SANDBOX_NOTIFICATION,
        # OpscontrolNotification is the orchestrator-only equivalent
        # for the ADR-0006 ops_* tools: it surfaces workload state
        # transitions (BHCE cold-start finished, c2-sliver healthy, …)
        # as system-reminders. Disabling it forces the agent into a
        # polling loop on ops_status, which both wastes tokens and
        # creates the same bash-stale-poll failure mode the
        # SandboxNotification middleware exists to prevent.
        MiddlewareSlot.OPSCONTROL_NOTIFICATION,
        # HITLApprovalMiddleware is the operator-approval gate for
        # high-impact actions (credential dumping, destructive ops).
        # Disabling it lets an agent execute gated tools without any
        # human in the loop. Replacement is fine if the new middleware
        # honours the same approval contract; full disable is the hazard.
        MiddlewareSlot.HITL_APPROVAL,
    }
)
"""Slots a plugin can only replace/disable when
``DECEPTICON_ALLOW_SAFETY_OVERRIDES=1`` is set in the environment.

The gate is enforced by ``build_middleware`` in
``decepticon.agents.build``. Plugins are expected to honour the
overall contract (e.g. a replacement EngagementContextMiddleware still
needs to inject scope) — the gate exists so an accidentally-installed
plugin can't silently subvert the safety story.
"""


# Common slots every agent uses (the "tail" of the middleware stack).
# PROXY_KEY_OVERRIDE is in the tail so EVERY LLM-calling agent re-keys to the
# per-run virtual key when one is threaded (multi-tenant cost attribution); it
# is a no-op otherwise, so OSS / single-tenant runs are unaffected.
_TAIL_SLOTS: frozenset[MiddlewareSlot] = frozenset(
    {
        MiddlewareSlot.MODEL_FALLBACK,
        MiddlewareSlot.PROXY_KEY_OVERRIDE,
        MiddlewareSlot.SUMMARIZATION,
        MiddlewareSlot.PROMPT_CACHING,
        MiddlewareSlot.PATCH_TOOL_CALLS,
    }
)

# Base slots — knowledge + filesystem + tail + untrusted-output
# quarantine. Every agent gets these.
#
# UNTRUSTED_OUTPUT is in the base set because *every* agent (including
# read-only specialists like detector and planning-only agents like
# soundwave) reads bytes from the workspace, the knowledge graph, or
# bash stdout. The quarantine envelope is cheap and has no false-
# positive ceiling — it never blocks, it only annotates.
_BASE_SLOTS: frozenset[MiddlewareSlot] = _TAIL_SLOTS | {
    MiddlewareSlot.SKILLS,
    MiddlewareSlot.FILESYSTEM,
    MiddlewareSlot.UNTRUSTED_OUTPUT,
    MiddlewareSlot.ROE_GUARDRAIL,
    # Additive / no-op-safe slots every agent gets: structured event
    # logging, the prompt-injection shield (deny-list wrap; coexists
    # with UNTRUSTED_OUTPUT's allow-list via dedup in the shield), and
    # budget enforcement (no-op when caps<=0).
    MiddlewareSlot.EVENT_LOG,
    MiddlewareSlot.PROMPT_INJECTION_SHIELD,
    MiddlewareSlot.BUDGET,
}

# Standard bash-executing agents (recon/exploit/postexploit/analyst/
# reverser/contract_auditor/cloud_hunter/ad_operator + plugin
# specialists verifier/patcher/scanner/exploiter): base + engagement
# context + sandbox notification.
_BASH_AGENT_SLOTS: frozenset[MiddlewareSlot] = _BASE_SLOTS | {
    MiddlewareSlot.ENGAGEMENT_CONTEXT,
    MiddlewareSlot.SANDBOX_NOTIFICATION,
    MiddlewareSlot.HITL_APPROVAL,
}


SLOTS_PER_ROLE: dict[str, frozenset[MiddlewareSlot]] = {
    # ── Standard orchestrator ──
    "decepticon": _BASE_SLOTS
    | {
        MiddlewareSlot.ENGAGEMENT_CONTEXT,
        MiddlewareSlot.SUBAGENT,
        MiddlewareSlot.OPPLAN,
        MiddlewareSlot.MODEL_OVERRIDE,
        MiddlewareSlot.HITL_APPROVAL,
        # Only the orchestrator carries ops_* tools (ADR-0006 §2), so
        # only the orchestrator gets the workload-completion delivery
        # middleware. Specialists never see workload notifications
        # because they cannot start workloads.
        MiddlewareSlot.OPSCONTROL_NOTIFICATION,
    },
    # ── Standard non-bash agent (planning + interview) ──
    "soundwave": _BASE_SLOTS | {MiddlewareSlot.ENGAGEMENT_CONTEXT},
    "blue_cell": frozenset(
        {
            MiddlewareSlot.ROE_GUARDRAIL,
            MiddlewareSlot.UNTRUSTED_OUTPUT,
            MiddlewareSlot.PROMPT_INJECTION_SHIELD,
            MiddlewareSlot.MODEL_FALLBACK,
            MiddlewareSlot.PROXY_KEY_OVERRIDE,
            MiddlewareSlot.BUDGET,
            MiddlewareSlot.MODEL_OVERRIDE,
        }
    ),
    # ── Standard bash-executing specialists ──
    "recon": _BASH_AGENT_SLOTS,
    "exploit": _BASH_AGENT_SLOTS,
    "postexploit": _BASH_AGENT_SLOTS,
    # Three OSS roles run the KG middleware — the persistent-graph use
    # cases the spec calls out
    # (docs/design/2026-06-03-kg-middleware-redesign.md § 4.10).
    # ad_operator records BloodHound-derived principals / paths and
    # confirmed AD findings; contract_auditor records Foundry-confirmed
    # smart-contract vulnerabilities and chain candidates. The opt-in
    # ``kg_record`` / ``kg_ingest`` tools complement (do not replace)
    # the legacy AD_TOOLS / CONTRACT_TOOLS surfaces — the legacy
    # ingest paths still route through the ``graph_transaction`` shim
    # and migrate to KGStore in a separate RFC.
    "analyst": _BASH_AGENT_SLOTS | {MiddlewareSlot.KG},
    "reverser": _BASH_AGENT_SLOTS,
    "contract_auditor": _BASH_AGENT_SLOTS | {MiddlewareSlot.KG},
    "cloud_hunter": _BASH_AGENT_SLOTS,
    "ad_operator": _BASH_AGENT_SLOTS | {MiddlewareSlot.KG},
    "phisher": _BASH_AGENT_SLOTS,
    "mobile_operator": _BASH_AGENT_SLOTS,
    "wireless_operator": _BASH_AGENT_SLOTS,
    "osint_operator": _BASH_AGENT_SLOTS,
    "iot_operator": _BASH_AGENT_SLOTS,
    "ics_operator": _BASH_AGENT_SLOTS,
    "forensicator": _BASH_AGENT_SLOTS,
    "supply_chain_operator": _BASH_AGENT_SLOTS,
    # ── Plugin orchestrator (no EngagementContext per the existing
    # vulnresearch factory — it consumes its parent's context) ──
    "vulnresearch": _BASE_SLOTS | {MiddlewareSlot.SUBAGENT, MiddlewareSlot.OPPLAN},
    # ── Plugin read-only specialist (no bash, no SandboxNotification) ──
    "detector": _BASE_SLOTS,
    # ── Plugin bash-executing specialists ──
    "verifier": _BASH_AGENT_SLOTS,
    "patcher": _BASH_AGENT_SLOTS,
    "scanner": _BASH_AGENT_SLOTS,
    "exploiter": _BASH_AGENT_SLOTS,
}
"""Role → slot-set mapping. The assembler only walks slots present in
the role's set; anything else is skipped silently. Plugin agents
register their own role here via the ``decepticon.agents`` entry-point
group (handled by ``plugin_loader``)."""


__all__ = [
    "MiddlewareSlot",
    "SAFETY_CRITICAL_SLOTS",
    "SLOTS_PER_ROLE",
]
