"""Red team engagement document schemas.

Defines the machine-readable document set for planning and executing
red team engagements. The baseline documents map to the military-style
planning hierarchy:

  RoE     → legal scope & boundaries       (guard rail, checked every iteration)
  CONOPS  → operational concept & threat    (strategic context)
  OPPLAN  → tactical objectives & status    (ralph loop task tracker)

Alongside these and the DeconflictionPlan, Soundwave writes five expansion
documents that frame an AI-autonomous engagement: ThreatProfile, ContactPlan,
DataHandlingPlan, AbortPlan, and CleanupPlan. See EngagementBundle.

The OPPLAN is the direct analogue of ralph's prd.json — it drives the
autonomous loop, with each objective checked off as it passes validation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ── Enums ─────────────────────────────────────────────────────────────


class EngagementType(StrEnum):
    EXTERNAL = "external"
    INTERNAL = "internal"
    HYBRID = "hybrid"
    ASSUMED_BREACH = "assumed-breach"
    PHYSICAL = "physical"


class ObjectivePhase(StrEnum):
    RECON = "recon"
    INITIAL_ACCESS = "initial-access"
    POST_EXPLOIT = "post-exploit"
    C2 = "c2"
    EXFILTRATION = "exfiltration"
    REPORTING = "reporting"


class OpsecLevel(StrEnum):
    """OPSEC posture for an objective.

    Determines C2 tier selection, tool choices, and detection avoidance rigor.
    Based on Red Team Maturity Model levels and C2 tier mapping.
    See docs/red-team/opplan-domain-knowledge.md for details.
    """

    LOUD = "loud"  # No evasion; testing detection capability
    STANDARD = "standard"  # Basic OPSEC; modify default signatures
    CAREFUL = "careful"  # Active evasion; avoid known signatures
    QUIET = "quiet"  # Minimal footprint; blend with normal traffic
    SILENT = "silent"  # Zero detection tolerance; abort if burned


class C2Tier(StrEnum):
    """C2 infrastructure tier for objective execution."""

    INTERACTIVE = "interactive"  # Direct operator control, seconds callback
    SHORT_HAUL = "short-haul"  # Reliable access, minutes-hours callback
    LONG_HAUL = "long-haul"  # Persistent fallback, hours-days callback


class ObjectiveStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AttackTechnique(BaseModel):
    id: str
    name: str
    description: str


class AttackAnnotation(BaseModel):
    catalog: str
    version: str
    tactic_id: str
    tactic_name: str
    tactic_description: str
    techniques: list[AttackTechnique] = Field(default_factory=list)


class ObjectiveOutcome(StrEnum):
    FINDING = "finding"
    NO_FINDING = "no-finding"
    OBJECTIVE_MET = "objective-met"
    INCONCLUSIVE = "inconclusive"
    INFRASTRUCTURE_ERROR = "infrastructure-error"
    SCOPE_REFUSED = "scope-refused"
    INVALIDATED = "invalidated"


class FindingSeverity(StrEnum):
    """CVSS-aligned severity levels for individual findings."""

    CRITICAL = "critical"  # CVSS 9.0-10.0
    HIGH = "high"  # CVSS 7.0-8.9
    MEDIUM = "medium"  # CVSS 4.0-6.9
    LOW = "low"  # CVSS 0.1-3.9
    INFORMATIONAL = "informational"  # CVSS 0.0 — observation only


class FindingConfidence(StrEnum):
    """Confidence level for a finding — drives verification requirements."""

    VERIFIED = "verified"  # Confirmed with 2+ methods (required for CRITICAL/HIGH)
    PROBABLE = "probable"  # Strong indicators, single method
    UNVERIFIED = "unverified"  # Initial observation, needs confirmation


class RemediationPriority(StrEnum):
    """Remediation urgency aligned with PTES/CREST reporting standards."""

    IMMEDIATE = "immediate"  # 0-7 days: patch, config change
    SHORT_TERM = "short-term"  # 30 days: detection rules, SIEM update
    LONG_TERM = "long-term"  # 90+ days: architecture improvement


# ── Finding / Evidence / Attack Path ─────────────────────────────────


class Evidence(BaseModel):
    """Artifact reference attached to a finding.

    Each piece of evidence points to a file in the engagement workspace
    (e.g., scan output, HTTP request/response, terminal log, pcap).
    SHA-256 hash provides chain-of-custody integrity verification.
    """

    type: str = Field(
        description=(
            "Evidence type: screenshot, http-request, terminal-log, pcap, artifact, scan-output"
        )
    )
    path: str = Field(description="Relative path within engagement workspace")
    description: str = ""
    sha256: str = Field(default="", description="SHA-256 hash for integrity verification")
    collected_at: str = Field(default="", description="ISO 8601 timestamp of collection")


class Finding(BaseModel):
    """Individual vulnerability or security finding -- one Markdown file per finding.

    Follows bug bounty report structure (HackerOne/Bugcrowd) enriched with
    red team metadata (detection gaps, ATT&CK mapping, agent provenance).
    CVSS v4.0 is the primary scoring system per FIRST 2023 recommendation.
    On-disk format: YAML frontmatter + Markdown body.

    File naming: findings/FIND-001.md, findings/FIND-002.md, ...
    """

    id: str = Field(description="Auto-generated ID: FIND-001, FIND-002, ...")
    title: str = Field(description="Bug bounty format: '[Type] in [Target] allows [Impact]'")
    severity: FindingSeverity
    cvss_score: float | None = Field(default=None, description="Numeric CVSS score (0.0-10.0)")
    cvss_vector: str = Field(
        default="", description="Full CVSS vector string, e.g. CVSS:4.0/AV:N/AC:L/..."
    )
    cvss_version: str = Field(default="4.0", description="CVSS version used (4.0 primary)")
    cwe: list[str] = Field(default_factory=list, description="CWE IDs, e.g. ['CWE-89']")
    mitre: list[str] = Field(
        default_factory=list, description="MITRE ATT&CK technique IDs, e.g. ['T1190']"
    )

    # Where
    affected_target: str = Field(description="IP, hostname, or URL of affected system")
    affected_component: str = Field(
        default="", description="Specific service, endpoint, port, or parameter"
    )

    # What
    description: str = Field(description="Technical description of the vulnerability")
    steps_to_reproduce: list[str] = Field(
        default_factory=list, description="Ordered reproduction steps"
    )
    impact: str = Field(default="", description="Business and technical impact assessment")

    # Evidence
    evidence: list[Evidence] = Field(default_factory=list)

    # Detection gap tracking (Purple Team / TIBER-EU)
    detected: bool | None = Field(
        default=None, description="Whether Blue Team detected this activity"
    )
    detection_notes: str = Field(
        default="", description="Which detection mechanisms fired or failed"
    )

    # Remediation (PTES/CREST report structure)
    remediation: str = Field(default="", description="Specific fix recommendation")
    remediation_priority: RemediationPriority | None = Field(
        default=None, description="Urgency: immediate, short-term, long-term"
    )

    # AI Agent metadata
    objective_id: str = Field(
        default="", description="Server-issued OPPLAN objective ID that found this"
    )
    phase: ObjectivePhase | None = None
    agent: str = Field(
        default="", description="Agent that discovered this: recon/exploit/postexploit"
    )
    iteration: int = Field(default=0, description="Ralph loop iteration number")
    confidence: FindingConfidence = FindingConfidence.VERIFIED
    discovered_at: str = Field(default="", description="ISO 8601 discovery timestamp")
    verified_methods: list[str] = Field(
        default_factory=list,
        description="Methods used to verify (e.g. ['nmap', 'manual curl'])",
    )


class AttackPathStep(BaseModel):
    """Single hop in a kill chain attack path."""

    order: int = Field(description="Step sequence number (1-based)")
    phase: ObjectivePhase
    technique: str = Field(description="ATT&CK technique name")
    mitre: str = Field(description="ATT&CK technique ID, e.g. T1190")
    source: str = Field(description="Origin host/service for this hop")
    target: str = Field(description="Destination host/service")
    tool: str = Field(default="", description="Tool used for this step")
    detected: bool | None = Field(default=None, description="Whether this step was detected")
    finding_id: str = Field(default="", description="Related finding ID (FIND-xxx)")


class AttackPath(BaseModel):
    """Kill chain traversal path -- connects findings into an attack narrative.

    Documents the complete chain from initial access to objective completion,
    mapping each hop to ATT&CK techniques. Combined severity may exceed
    individual finding scores when chained (e.g., Medium + Medium = Critical).

    File naming: findings/attack-paths/PATH-001.md
    """

    id: str = Field(description="Auto-generated ID: PATH-001, PATH-002, ...")
    name: str = Field(description="Descriptive name, e.g. 'External to DB Admin via SSRF Chain'")
    description: str = Field(default="", description="Narrative description of the attack path")
    steps: list[AttackPathStep] = Field(default_factory=list)
    combined_severity: FindingSeverity = FindingSeverity.CRITICAL
    finding_ids: list[str] = Field(
        default_factory=list, description="All FIND-xxx IDs in this path"
    )


# ── RoE (Rules of Engagement) ────────────────────────────────────────


class ScopeEntry(BaseModel):
    """A single in-scope or out-of-scope target."""

    target: str = Field(description="Domain, IP range (CIDR), or asset identifier")
    type: str = Field(description="domain, ip-range, cloud-resource, physical, etc.")
    notes: str = ""


class EscalationContact(BaseModel):
    """Emergency or escalation contact."""

    name: str
    role: str
    channel: str = Field(description="Phone, email, Slack, etc.")
    available: str = Field(default="24/7", description="Availability window")


class RoE(BaseModel):
    """Rules of Engagement — legally binding scope and boundaries.

    Checked at the start of every ralph loop iteration as a guard rail.
    """

    engagement_name: str
    client: str
    start_date: str
    end_date: str
    engagement_type: EngagementType
    testing_window: str = Field(
        description="Authorized testing hours, e.g. 'Mon-Fri 09:00-18:00 KST'"
    )

    # Scope
    in_scope: list[ScopeEntry] = Field(default_factory=list)
    out_of_scope: list[ScopeEntry] = Field(default_factory=list)

    # Boundaries
    prohibited_actions: list[str] = Field(
        default_factory=lambda: [
            "Denial of Service (DoS/DDoS)",
            "Social engineering of employees (unless authorized)",
            "Physical access attempts (unless authorized)",
            "Data exfiltration of real customer data",
            "Modification or deletion of production data",
        ]
    )
    permitted_actions: list[str] = Field(default_factory=list)

    # Escalation
    escalation_contacts: list[EscalationContact] = Field(default_factory=list)
    # Deprecated: prefer AbortPlan (``plan/abort.json``) for structured
    # halt-triggers + AI-aware safety gates. Kept as a one-line legacy
    # default for readers that don't load the expansion doc.
    incident_procedure: str = Field(
        default=(
            "Stop immediately, document the incident, notify engagement lead within 15 minutes."
        ),
        description=("DEPRECATED one-liner — see plan/abort.json for structured halt-triggers."),
    )

    # Legal
    authorization_reference: str = Field(
        default="", description="Reference to signed authorization letter or contract"
    )

    # Operational limits
    # Deprecated: prefer DataHandlingPlan (``plan/data-handling.json``) for
    # structured fields (data classes, retention, encryption). Kept here
    # as a one-line summary for legacy readers that don't load the
    # expansion doc.
    data_handling: str = Field(
        default="",
        description=(
            "DEPRECATED summary — see plan/data-handling.json for structured fields. "
            "Kept as a one-line legacy summary."
        ),
    )
    # Deprecated: prefer CleanupPlan (``plan/cleanup.json``) for the
    # full artifact inventory + removal commands. The bool is kept as
    # a legacy "did the engagement opt out of cleanup entirely?" flag.
    cleanup_required: bool = Field(
        default=True,
        description=(
            "DEPRECATED flag — see plan/cleanup.json for the full artifact "
            "inventory and removal commands. Kept as a legacy opt-out switch."
        ),
    )

    # Metadata
    version: str = "1.0"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── CONOPS (Concept of Operations) ───────────────────────────────────


class ThreatActor(BaseModel):
    """Threat actor profile to emulate."""

    name: str = Field(description="Actor name or archetype, e.g. 'APT29', 'Opportunistic External'")
    sophistication: str = Field(description="low, medium, high, nation-state")
    motivation: str = Field(description="financial, espionage, disruption, hacktivism")
    initial_access: list[str] = Field(
        default_factory=list, description="Expected initial access techniques (MITRE IDs)"
    )
    ttps: list[str] = Field(
        default_factory=list, description="Key MITRE ATT&CK technique IDs this actor uses"
    )


class KillChainPhase(BaseModel):
    """A phase in the engagement kill chain."""

    phase: ObjectivePhase
    description: str
    success_criteria: str = ""
    tools: list[str] = Field(default_factory=list)


class CONOPS(BaseModel):
    """Concept of Operations — strategic engagement overview.

    Readable by both technical operators and non-technical stakeholders.
    """

    engagement_name: str
    executive_summary: str = Field(description="2-3 sentence overview a CEO could understand")

    # Threat model
    threat_actors: list[ThreatActor] = Field(default_factory=list)
    attack_narrative: str = Field(
        default="", description="Story-form description of the simulated attack scenario"
    )

    # Kill chain
    kill_chain: list[KillChainPhase] = Field(default_factory=list)

    # Operational
    methodology: str = Field(default="PTES + MITRE ATT&CK framework")
    # Deprecated: prefer ContactPlan (``plan/contact.json``) for the
    # structured operator + escalation + abort channels matrix. Kept as
    # a one-line legacy summary.
    communication_plan: str = Field(
        default="",
        description=(
            "DEPRECATED one-liner — see plan/contact.json for the structured operator + "
            "escalation + abort channels."
        ),
    )

    # Timeline
    phases_timeline: dict[str, str] = Field(
        default_factory=dict, description="Phase name → date range mapping"
    )

    # Success criteria
    success_criteria: list[str] = Field(default_factory=list)


# ── Deconfliction Plan ───────────────────────────────────────────────


class DeconflictionEntry(BaseModel):
    """A deconfliction identifier for red team activity."""

    type: str = Field(description="source-ip, user-agent, tool-hash, time-window, etc.")
    value: str
    description: str = ""


class DeconflictionPlan(BaseModel):
    """Deconfliction plan — separating red team activity from real threats."""

    engagement_name: str
    identifiers: list[DeconflictionEntry] = Field(default_factory=list)
    notification_procedure: str = Field(
        default="Red team lead notifies SOC 30 minutes before active scanning begins."
    )
    soc_contact: str = ""
    deconfliction_code: str = Field(
        default="", description="Shared secret code for real-time deconfliction calls"
    )


# ── OPPLAN (Operations Plan) — the ralph loop driver ─────────────────


class Objective(BaseModel):
    """A single engagement objective — analogous to ralph's user story.

    Each leaf objective must be completable in one agent context window.
    The dispatcher selects from the prerequisite-ready frontier.
    """

    id: str = Field(description="Opaque objective ID issued by the OPPLAN server")
    phase: ObjectivePhase
    title: str
    description: str
    acceptance_criteria: list[str] = Field(
        description="Verifiable criteria — each must be checkable"
    )
    priority: int = Field(description="Scheduling preference among ready objectives (1 = highest).")
    status: ObjectiveStatus = ObjectiveStatus.PENDING
    """pending → in-progress → completed/blocked. blocked → in-progress (retry) or completed (abandon)."""
    mitre: list[str] = Field(
        default_factory=list,
        description="MITRE ATT&CK technique IDs (e.g. ['T1190', 'T1059.004'])",
    )
    attack: AttackAnnotation | None = Field(
        default=None,
        description="Server-validated ATT&CK tactic and technique metadata",
    )

    # Red team-specific fields (not found in pentest planning)
    opsec: OpsecLevel = Field(
        default=OpsecLevel.STANDARD,
        description="OPSEC posture — drives tool selection and detection avoidance rigor",
    )
    opsec_notes: str = Field(
        default="", description="Specific OPSEC constraints for this objective"
    )
    c2_tier: C2Tier = Field(
        default=C2Tier.INTERACTIVE,
        description="C2 tier: interactive (seconds), short-haul (minutes), long-haul (hours)",
    )
    concessions: list[str] = Field(
        default_factory=list,
        description="Pre-authorized assists if objective is blocked (TIBER/CORIE concept)",
    )

    notes: str = ""
    blocked_by: list[str] = Field(
        default_factory=list, description="Objective IDs that must complete first"
    )
    any_of: list[list[str]] = Field(
        default_factory=list,
        description="For each group, at least one listed objective must complete first",
    )
    required_fact_ids: list[str] = Field(
        default_factory=list,
        description="Verified fact IDs required before this objective can start",
    )
    outcome: ObjectiveOutcome | None = Field(
        default=None,
        description="Observed result, separate from execution status (for example no-finding or inconclusive)",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Workspace evidence paths supporting the observed result",
    )
    owner: str = Field(default="", description="Sub-agent currently executing this objective")
    parent_id: str | None = Field(
        default=None,
        description=(
            "Optional parent objective ID. When set, this objective is a "
            "sub-task of the parent — the parent cannot move to COMPLETED "
            "until every child is COMPLETED or CANCELLED. Inspired by "
            "PentestGPT's Pentesting Task Tree (PTT)."
        ),
    )


class PlanFact(BaseModel):
    id: str = Field(min_length=1)
    producer_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    verified: bool = False


class OPPLAN(BaseModel):
    """Operations Plan — the tactical task tracker for the ralph loop.

    The autonomous loop selects a ready leaf from the versioned graph,
    executes it, records evidence, and advances or revises the plan.

    Hierarchical mode: any objective with ``parent_id`` set becomes a
    child of that parent. Trees are arbitrary depth — but real plans
    rarely need more than two levels in practice. The flat-list code
    paths still work when no hierarchy is set.
    """

    engagement_name: str
    threat_profile: str = Field(
        description="Short threat actor summary for context injection each iteration"
    )
    objectives: list[Objective] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    facts: list[PlanFact] = Field(default_factory=list)

    # ── Hierarchy helpers ──────────────────────────────────────────────

    def by_id(self, objective_id: str) -> Objective | None:
        for obj in self.objectives:
            if obj.id == objective_id:
                return obj
        return None

    def children_of(self, parent_id: str) -> list[Objective]:
        """Direct children of ``parent_id`` (single level)."""
        return [o for o in self.objectives if o.parent_id == parent_id]

    def descendants_of(self, parent_id: str) -> list[Objective]:
        """Every descendant of ``parent_id`` (depth-first)."""
        out: list[Objective] = []
        stack = list(self.children_of(parent_id))
        while stack:
            obj = stack.pop()
            out.append(obj)
            stack.extend(self.children_of(obj.id))
        return out

    def root_objectives(self) -> list[Objective]:
        """Top-level objectives (no parent)."""
        return [o for o in self.objectives if not o.parent_id]

    def has_hierarchy(self) -> bool:
        return any(o.parent_id for o in self.objectives)

    def detect_cycle(self, objective_id: str, parent_id: str) -> bool:
        """Return True if attaching ``objective_id`` under ``parent_id``
        would create a cycle. Walks the proposed parent chain upward."""
        if objective_id == parent_id:
            return True
        cur = self.by_id(parent_id)
        seen: set[str] = set()
        while cur is not None:
            if cur.id in seen:
                return True
            seen.add(cur.id)
            if cur.id == objective_id:
                return True
            if not cur.parent_id:
                return False
            cur = self.by_id(cur.parent_id)
        return False

    def tree(self) -> list[dict[str, object]]:
        """Return the objective list as a nested ``[{...children: []}]`` dict."""

        def _build(parent_id: str | None) -> list[dict[str, object]]:
            return [
                {
                    "id": o.id,
                    "title": o.title,
                    "phase": o.phase.value if hasattr(o.phase, "value") else str(o.phase),
                    "status": o.status.value if hasattr(o.status, "value") else str(o.status),
                    "priority": o.priority,
                    "children": _build(o.id),
                }
                for o in sorted(
                    [x for x in self.objectives if x.parent_id == parent_id],
                    key=lambda x: x.priority,
                )
            ]

        return _build(None)


# ── ThreatProfile (standalone) ───────────────────────────────────────
#
# Distinct from ``CONOPS.threat_actors`` (which stays for backward-compat
# inside the CONOPS doc): the standalone ``plan/threat-profile.json``
# carries the MITRE-group-ID-keyed adversary persona that Decepticon's
# OPPLAN builder consults to pick TTP sequences. CTI delta and per-tier
# numeric grading live here, not in CONOPS.
# ─────────────────────────────────────────────────────────────────────


class ThreatTier(StrEnum):
    """Adversary tier — numeric grading aligned with the threat-profile
    skill (Tier 1–4). Distinct from ``ThreatActor.sophistication``,
    which stays as the free-form CONOPS embedded field."""

    TIER_1 = "tier-1"  # Opportunistic attacker (script-kiddie, opportunistic)
    TIER_2 = "tier-2"  # Targeted cybercriminal (ransomware crew, financial)
    TIER_3 = "tier-3"  # APT / nation-state
    TIER_4 = "tier-4"  # Insider threat (privileged access)


class ThreatProfile(BaseModel):
    """Adversary persona for emulation — stored at ``plan/threat-profile.json``.

    Distinct from ``CONOPS.threat_actors`` (embedded), this is the
    standalone JSON the orchestrator's OPPLAN-builder consults to choose
    TTP sequences and the operations agents consult to bound tool
    selection. One profile per engagement; multi-actor scenarios should
    pick the dominant emulation target.
    """

    engagement_name: str
    actor_name: str = Field(
        description="Primary actor identifier, e.g. 'APT29-like (Cozy Bear)' or 'Custom — Insider'"
    )
    actor_aliases: list[str] = Field(default_factory=list)
    group_id: str = Field(default="", description="MITRE Groups ID if known, e.g. 'G0050'")
    tier: ThreatTier
    sophistication: str = Field(description="low / medium / high / nation-state")
    motivation: str = Field(description="espionage / financial / disruption / hacktivism / insider")
    initial_access: list[str] = Field(
        default_factory=list,
        description="MITRE ATT&CK initial-access technique IDs (TA0001)",
    )
    key_ttps: list[str] = Field(
        default_factory=list,
        description="Top 5–10 MITRE ATT&CK technique IDs this actor relies on",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="Realistic toolset for emulation (e.g. Cobalt Strike, Mimikatz, custom RAT)",
    )
    infrastructure: list[str] = Field(
        default_factory=list,
        description="C2 patterns, VPS providers, domain naming heuristics",
    )
    recent_cti_delta: str = Field(
        default="",
        description="Free-text summary of CTI deltas from the most recent 12 months",
    )
    confidence: FindingConfidence = Field(
        default=FindingConfidence.PROBABLE,
        description="Confidence that the live actor matches this profile",
    )

    version: str = "1.0"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Cleanup & Restoration Plan ───────────────────────────────────────
#
# Inventory of every artifact the agent will (or did) create during the
# engagement, plus the concrete command to remove each one and a
# verifier command to confirm removal. Drives the post-engagement
# cleanup tool — without this list, dummy accounts / beacons / scheduled
# tasks routinely outlive the engagement.
# ─────────────────────────────────────────────────────────────────────


class CleanupArtifact(BaseModel):
    """Single artifact the agent created on a target during the engagement.

    Filled in mostly during execution (by the operations agents) but
    Soundwave seeds the plan with the expected categories based on the
    CONOPS kill chain so each phase has a place to record what it
    touched.
    """

    artifact_type: str = Field(
        description=(
            "Category: beacon / account / file / scheduled-task / service / "
            "registry-key / persistence-mechanism / tool / network-rule"
        )
    )
    host: str = Field(description="Hostname, IP, or asset identifier where the artifact lives")
    path: str = Field(
        description="File path, registry key, account name, or other concrete locator"
    )
    sha256: str = Field(default="", description="SHA-256 of the artifact when it's a file")
    persistence_mech: str = Field(
        default="",
        description=(
            "If this is a persistence implant: which mech "
            "(scheduled-task, service, registry-run, cron, systemd-unit, ...)"
        ),
    )
    removal_command: str = Field(
        description="Concrete command to remove the artifact (idempotent if possible)"
    )
    verifier_command: str = Field(
        default="", description="Command whose zero exit confirms removal"
    )
    created_by_objective: str = Field(
        default="", description="OPPLAN objective ID that created this artifact"
    )
    removed: bool = False
    removed_at: str = Field(default="", description="ISO 8601 timestamp of confirmed removal")


class CleanupPlan(BaseModel):
    """Post-engagement cleanup roster — ``plan/cleanup.json``.

    Replaces ``RoE.cleanup_required`` (deprecated bool flag). The
    orchestrator and operations agents append to ``artifacts`` as they
    create persistent state on targets; the engagement is not "complete"
    until every artifact has ``removed=True`` or has an explicit
    ``cancellation_reason`` recorded.
    """

    engagement_name: str
    completion_criteria: str = Field(
        default=(
            "All listed artifacts have removed=True OR a documented "
            "cancellation_reason (e.g. operator decided to leave a "
            "honeyfile for blue team training)."
        )
    )
    artifacts: list[CleanupArtifact] = Field(default_factory=list)
    pre_engagement_baseline: str = Field(
        default="",
        description=(
            "Reference to a pre-engagement system snapshot or backup "
            "(volume ID, snapshot timestamp, restore command) so the "
            "operator can roll back if cleanup is incomplete."
        ),
    )

    version: str = "1.0"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Abort / Crisis Plan ──────────────────────────────────────────────
#
# Halt triggers, response actions, and AI-aware safety gates. Replaces
# ``RoE.incident_procedure`` (deprecated free-form string). The
# orchestrator's SafetyMiddleware reads this to decide when to freeze
# the agent loop.
# ─────────────────────────────────────────────────────────────────────


class TriggerSeverity(StrEnum):
    """Severity tier for an abort trigger — drives the agent's response."""

    INFO = "info"  # Log only, continue
    WARNING = "warning"  # Pause current step, ask operator
    CRITICAL = "critical"  # Halt the active objective, preserve evidence
    EMERGENCY = "emergency"  # Halt all agents, freeze workspace, page operator


class AbortTrigger(BaseModel):
    """A single condition that, when matched, triggers the response_action."""

    condition: str = Field(
        description=(
            "Concrete condition, e.g. 'SOC issues real-incident alert', "
            "'real production data observed in scan output', 'scope "
            "boundary violation detected'"
        )
    )
    severity: TriggerSeverity
    response_action: str = Field(
        description=(
            "Agent's mandated action, e.g. 'halt current objective, "
            "snapshot workspace, notify operator within 60s'"
        )
    )
    auto_halt: bool = Field(
        default=True,
        description=(
            "Whether the agent halts itself on detection (True) or only "
            "flags the trigger and awaits operator decision (False)"
        ),
    )


class AbortPlan(BaseModel):
    """Halt-trigger roster + AI-aware safety gates — ``plan/abort.json``.

    AI-specific fields (``hallucination_threshold``, ``destructive_action_gate``,
    ``output_validation``) defend against the LLM-driven failure modes
    documented in OWASP Top 10 for Agentic Applications (2025.12).
    """

    engagement_name: str
    halt_triggers: list[AbortTrigger] = Field(
        default_factory=lambda: [
            AbortTrigger(
                condition="Real-incident alert from blue team / SOC",
                severity=TriggerSeverity.EMERGENCY,
                response_action="Halt all agents; preserve evidence; notify operator immediately",
                auto_halt=True,
            ),
            AbortTrigger(
                condition="Production data observed in collected evidence",
                severity=TriggerSeverity.CRITICAL,
                response_action="Halt active objective; quarantine evidence; await operator",
                auto_halt=True,
            ),
            AbortTrigger(
                condition="Scope boundary violation detected",
                severity=TriggerSeverity.CRITICAL,
                response_action="Halt active objective; document the violation; await operator",
                auto_halt=True,
            ),
        ]
    )
    abort_signal_channel: str = Field(
        default="",
        description=(
            "Operator-side mechanism to signal abort (file marker, HTTP "
            "endpoint, ask_user_question response). Empty = no out-of-band "
            "abort channel; operator pauses via CLI Ctrl+C only."
        ),
    )
    recovery_procedure: str = Field(
        default=(
            "After abort: snapshot workspace; export evidence with chain-of-custody; "
            "run cleanup.json removal commands; require operator approval before resume."
        )
    )

    # ── AI-aware safety gates (OWASP Top 10 for Agentic Applications, 2025.12) ──
    hallucination_threshold: int = Field(
        default=3,
        description=(
            "Count of consecutive unverified-success claims before the agent "
            "is forced into evidence-collection mode rather than progress"
        ),
        ge=1,
    )
    destructive_action_gate: bool = Field(
        default=True,
        description=(
            "If True, every command flagged as destructive (rm -rf, drop "
            "table, format, ...) must pass verify-before-run policy. "
            "Disable only for read-only engagements."
        ),
    )
    output_validation: str = Field(
        default="verify-evidence-hash",
        description=(
            "Method used to validate agent-reported successes: "
            "'verify-evidence-hash' (sha256 every artifact), "
            "'second-tool-confirm' (re-run with different tool), "
            "'operator-spot-check' (ask_user_question)"
        ),
    )

    version: str = "1.0"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Contact Plan ─────────────────────────────────────────────────────
#
# Slim version of the traditional white-cell/blue-cell/red-cell matrix.
# For Decepticon's autonomous flow the agent only needs the operator
# channel, escalation chain, and abort recipient — full red-team
# cell-management lives outside the agent. Replaces
# ``CONOPS.communication_plan`` (deprecated free-form string).
# ─────────────────────────────────────────────────────────────────────


class Contact(BaseModel):
    """A single human contact reachable by the agent or operator."""

    name: str
    role: str = Field(description="e.g. 'Primary Operator', 'Client SOC Lead', 'Engagement Owner'")
    channel: str = Field(
        description=(
            "Resolvable channel: 'signal:+1234567890', 'email:soc@client.example', "
            "'pagerduty:service-key', 'slack:#redteam-ops'"
        )
    )
    availability: str = Field(default="24/7", description="Coverage window")


class ContactPlan(BaseModel):
    """Operator + escalation + abort channels — ``plan/contact.json``.

    Designed for autonomous-AI engagements where the agent itself is the
    "red cell" and only humans staff the white / blue cells. Full
    multi-cell rosters belong in an external comms doc.
    """

    engagement_name: str
    primary_operator: Contact
    escalation_chain: list[Contact] = Field(
        default_factory=list,
        description=(
            "Ordered list — agent escalates down this chain when the "
            "primary operator is unreachable past the abort_plan's "
            "response window."
        ),
    )
    abort_signal_recipient: Contact | None = Field(
        default=None,
        description="Who the agent pages on EMERGENCY-severity abort triggers",
    )
    external_soc_endpoint: str = Field(
        default="",
        description=(
            "Optional webhook the agent POSTs to before active scanning "
            "(deconfliction notice). Empty = no external SOC integration."
        ),
    )
    blackout_windows: list[str] = Field(
        default_factory=list,
        description=(
            "ISO 8601 datetime ranges (e.g. '2026-05-21T22:00:00+09:00/PT8H') "
            "during which the agent must not run active operations"
        ),
    )

    version: str = "1.0"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Data Handling Plan ───────────────────────────────────────────────
#
# Evidence collection, storage, encryption, retention, and chain of
# custody. Replaces ``RoE.data_handling`` (deprecated free-form string).
# Drives the operations agents' evidence-storage logic and the post-
# engagement purge.
# ─────────────────────────────────────────────────────────────────────


class DataClass(BaseModel):
    """A single category of data the agent may encounter or collect."""

    name: str = Field(
        description="e.g. 'credentials', 'pii', 'health-records', 'trade-secrets', 'source-code'"
    )
    classification: str = Field(description="public / internal / restricted / secret")
    retention_days: int = Field(
        description="Days to keep collected evidence of this class before automatic purge",
        ge=0,
    )
    encryption_at_rest: bool = True
    encryption_in_transit: bool = True
    handling_notes: str = Field(
        default="",
        description=(
            "Class-specific rules, e.g. 'redact PII before quoting in findings', "
            "'never store cleartext credentials — hash with bcrypt before logging'"
        ),
    )


class DataHandlingPlan(BaseModel):
    """Evidence storage + chain-of-custody — ``plan/data-handling.json``.

    Default ``data_classes`` cover credentials / PII / source-code with
    conservative retention; engagements with compliance requirements
    (GDPR, HIPAA, PCI-DSS) override via the interview.
    """

    engagement_name: str
    data_classes: list[DataClass] = Field(
        default_factory=lambda: [
            DataClass(
                name="credentials",
                classification="restricted",
                retention_days=30,
                handling_notes="Hash before logging; never quote cleartext in findings.",
            ),
            DataClass(
                name="pii",
                classification="restricted",
                retention_days=14,
                handling_notes="Redact PII fields in findings; aggregate before reporting.",
            ),
            DataClass(
                name="source-code",
                classification="internal",
                retention_days=90,
                handling_notes="OK to quote relevant snippets in findings under fair use.",
            ),
            DataClass(
                name="business-data",
                classification="restricted",
                retention_days=14,
                handling_notes="Do not export beyond the engagement workspace; summarize only.",
            ),
        ]
    )
    evidence_storage_path: str = Field(
        default="/workspace/<engagement>/evidence/",
        description="Engagement-workspace-relative path where collected evidence is stored",
    )
    chain_of_custody: bool = Field(
        default=True,
        description="If True, every evidence file gets a SHA-256 hash recorded in the Finding",
    )
    purge_after_days: int = Field(
        default=90,
        description=(
            "Hard cap — every artifact in evidence/ older than this is purged "
            "regardless of data_class.retention_days"
        ),
        ge=0,
    )
    compliance_frameworks: list[str] = Field(
        default_factory=list,
        description="Frameworks that bind this engagement: GDPR / HIPAA / PCI-DSS / SOC2 / etc.",
    )

    version: str = "1.0"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Engagement Bundle ─────────────────────────────────────────────────


class EngagementBundle(BaseModel):
    """Complete engagement document set.

    Soundwave generates the three baseline documents (``roe``, ``conops``,
    ``deconfliction``) plus the five expansion documents introduced for
    the AI-autonomous red-team flow (``threat_profile``, ``cleanup``,
    ``abort``, ``contact``, ``data_handling``). The orchestrator
    (Decepticon) generates ``opplan`` separately by reading the
    expansion docs — Soundwave does not write opplan.

    The ralph loop reads roe + opplan each iteration; the operations
    agents consult ``cleanup`` (to record artifacts) and ``abort``
    (for halt-trigger evaluation) during execution.
    """

    roe: RoE
    conops: CONOPS
    opplan: OPPLAN
    deconfliction: DeconflictionPlan
    # ── Expansion documents (Soundwave writes; orchestrator + ops consume) ──
    threat_profile: ThreatProfile | None = None
    cleanup: CleanupPlan | None = None
    abort: AbortPlan | None = None
    contact: ContactPlan | None = None
    data_handling: DataHandlingPlan | None = None

    def save(self, engagement_dir: str) -> dict[str, str]:
        """Save all documents to an engagement workspace directory.

        Layout — under ``<engagement_dir>/plan/``: roe.json, conops.json,
        opplan.json, and deconfliction.json are always written; the expansion
        documents (threat-profile.json, cleanup.json, abort.json, contact.json,
        data-handling.json) are written only when populated, so legacy callers
        that set just the baseline four stay compatible.

        Phase artifact directories such as ``recon/``, ``exploit/``,
        ``post-exploit/``, ``findings/``, and ``report/`` are created lazily
        by the tool or agent that writes a real artifact there. This avoids
        polluting a fresh Docker workspace with empty scaffold directories.

        Returns a mapping of document type → file path.
        """
        import json
        from pathlib import Path

        root = Path(engagement_dir)
        plan_dir = root / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)

        files = {}
        # Baseline four + the expansion five (optional). The four
        # baseline docs always serialize even when their content is
        # default; the expansion docs only serialize when populated so
        # legacy callers writing only roe/conops/opplan/deconfliction
        # remain compatible.
        baseline = [
            ("roe", self.roe),
            ("conops", self.conops),
            ("opplan", self.opplan),
            ("deconfliction", self.deconfliction),
        ]
        expansion = [
            ("threat-profile", self.threat_profile),
            ("cleanup", self.cleanup),
            ("abort", self.abort),
            ("contact", self.contact),
            ("data-handling", self.data_handling),
        ]
        for name, doc in [*baseline, *[(n, d) for n, d in expansion if d is not None]]:
            path = plan_dir / f"{name}.json"
            path.write_text(
                json.dumps(doc.model_dump(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            files[name] = str(path)

        return files
