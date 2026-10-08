"""OPPLANMiddleware — domain-specific objective tracking for red team engagements.

This middleware injects OPPLAN instructions and live objective status into the
model prompt, registers OPPLAN tools from ``decepticon.tools.opplan``, and
enforces one OPPLAN tool call per model step.

OPPLAN persistence uses the same configured backend as the engagement
filesystem tools. In production that backend is HTTPSandbox, scoped through
EngagementFilesystemBackend, so reads/writes target the sandbox's active
engagement workspace rather than the LangGraph host filesystem.

Tools:
  commit_opplan    — create or revise the complete DAG
  get_objective    — read single objective detail
  list_objectives  — list all objectives with progress summary
  update_objective — update status, notes, owner, and result
  load_opplan      — hydrate state from backend file

Design notes:
  - Domain model is engagement objective tracking for kill-chain execution
  - Enum-typed parameters (ObjectivePhase, OpsecLevel, C2Tier)
  - Kill chain dependencies (blocked_by) with execution-time validation
  - Dynamic OPPLAN status injection every LLM call (battle tracker)
  - Parallel mutation prevention for versioned DAG commits
  - Backend-mediated OPPLAN persistence at /workspace/plan/opplan.json
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Annotated, Any, NotRequired, cast, override

from deepagents.backends.protocol import BackendProtocol
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import OmitFromInput
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from decepticon.tools.opplan import (
    OPPLAN_TOOL_NAMES,
    OPPLAN_VIRTUAL_PATH,
    _assign_objective_ids,
    _canonicalize_attack_rows,
    _inspect_objective_rows,
    _persist_opplan_to_backend,
    _read_text_from_backend,
    _run_sandbox_backend,
    _scoped_opplan_backend,
    _upgrade_unversioned_plan,
    build_opplan_tools,
)
from decepticon_core.types.engagement import OPPLAN, Objective

_PLAN_MUTATION_TOOLS = frozenset(
    {
        "update_objective",
        "load_opplan",
        "commit_opplan",
        "record_plan_fact",
        "revoke_plan_fact",
    }
)


def _reduce_engagement_name(current: str | None, update: str | None) -> str | None:
    """Merge concurrent launcher/tool writes to the stable engagement slug."""
    return update if update is not None else current


def _reduce_workspace_path(current: str | None, update: str | None) -> str | None:
    """Merge concurrent writes to the active workspace path.

    Without a reducer, LangGraph rejects parallel state updates to the
    same key with INVALID_CONCURRENT_GRAPH_UPDATE. The launcher is the
    single source of truth, so every concurrent writer agrees on the
    same value — last-write-wins on non-None is sufficient and matches
    ``_reduce_engagement_name`` semantics. Closes #153.
    """
    return update if update is not None else current


# ── State Schema ──────────────────────────────────────────────────────


class OPPLANState(AgentState):
    """Extended agent state with OPPLAN objectives.

    Merged automatically by create_agent() when OPPLANMiddleware
    is in the middleware stack. All fields are excluded from input schema
    (OmitFromInput) — only the middleware tools can write to them.
    """

    objectives: Annotated[NotRequired[list[dict]], OmitFromInput]
    """List of OPPLAN objectives in dict form (serialized Objective models)."""

    engagement_name: NotRequired[Annotated[str, OmitFromInput, _reduce_engagement_name]]
    """Current engagement name for context."""

    threat_profile: Annotated[NotRequired[str], OmitFromInput]
    """Threat actor profile for context injection."""

    workspace_path: Annotated[NotRequired[str], OmitFromInput, _reduce_workspace_path]
    """Engagement workspace root path — set by launcher config/load_opplan."""

    plan_revision: Annotated[NotRequired[int], OmitFromInput]
    plan_facts: Annotated[NotRequired[list[dict]], OmitFromInput]


# ── System Prompt ─────────────────────────────────────────────────────

OPPLAN_SYSTEM_PROMPT = """\
## OPPLAN — Operational Plan Tracking

You have OPPLAN tools to manage red team engagement objectives.
These are always available — no mode switching needed.

### Persistence model

The launcher binds the engagement workspace at `/workspace`. Every mutation
through these tools (`commit_opplan`, `record_plan_fact`, `revoke_plan_fact`, `update_objective`,
and legacy objective tools) is **automatically persisted through the configured
filesystem/sandbox backend to `/workspace/plan/opplan.json`** — there is no
separate save step and no direct host-filesystem write. The persisted file is
a stable, sorted, human-readable JSON document with a `schema_version`, a
`saved_at` timestamp, and a `summary` block alongside the objectives list.

### Objective CRUD Tools

- **`commit_opplan`** — Create or revise the complete versioned objective DAG in
  one call. Omit `id` on every new node: the server assigns a UUID. Reference
  new nodes by their zero-based position in the submitted `objectives` array;
  existing nodes keep their saved IDs. These positions never become saved IDs.
  Use `expected_revision` (0 for a new plan); forward references are valid.
  Supply engagement metadata such as `engagement_name` and `threat_profile` on
  the first commit when known. Keep each leaf small enough for one specialist
  handoff. Do not add dependencies solely to impose workflow phase order.
  `blocked_by` means all predecessors, `any_of` means one per alternative group.
  Declare expected facts with `facts` and use `required_fact_ids` for evidence gates.
  Keep active and terminal objectives unchanged during a replan.
  A parent objective is complete only when its children are completed or
  cancelled. Check the committed result for issued IDs and the new revision
  before dispatching.
  Every objective needs `title`, `description`, `acceptance_criteria` (a list
  of checkable strings), `phase`, and `priority`; omit `id` for new nodes.
  Valid `phase` values are `recon`, `initial-access`, `post-exploit`, `c2`,
  `exfiltration`, and `reporting`. `reconnaissance` is not valid. `priority`
  must be an integer (1 is highest), not a label such as `high`. Example new
  node: `{"title":"Review authorized scope","description":"Read the approved scope","acceptance_criteria":["Scope recorded"],"phase":"recon","priority":1}`.

- For each objective, decide whether its concrete adversary behavior has a
  defensible Enterprise ATT&CK mapping. Tactics explain why, techniques explain
  how; neither defines execution order. Do not map administrative objectives
  such as scope review or report writing just to fill a field. For mapped
  objectives, use the available ATT&CK lookup tools to check candidates, then
  supply `attack_tactic_id` (for example `TA0001`) and technique IDs in `mitre`.
  The server validates active IDs and tactic-to-technique relationships against
  Enterprise ATT&CK v19.2 and saves canonical names and descriptions. Never
  invent those names or descriptions. If lookup is unavailable or evidence for
  a mapping is weak, leave both fields empty. `phase` is an operational workflow
  stage; neither `phase` nor ATT&CK tactic implies a DAG dependency.

- **`record_plan_fact`** — Attach existing workspace evidence to a declared fact
  after its producer completes. Path existence is checked; content truth is not.

- **`revoke_plan_fact`** — Revoke a fact when its evidence is disproven or lost.
  Active and completed dependent objectives become blocked transitively.

- **`get_objective`** — Read a single objective's full details.
  ALWAYS call this before `update_objective` (read-before-write, staleness prevention).

- **`list_objectives`** — List all objectives with progress summary.
  Use when: selecting the next objective, reviewing progress, situational awareness.

- **`update_objective`** — Update status, notes, or owner.
  ALWAYS call `get_objective` first.

- **`load_opplan`** — Bind the active workspace and hydrate state from an
  existing `plan/opplan.json`. Call before filesystem bootstrap. A missing file
  still binds the workspace; use `commit_opplan` to create the first DAG.

### Concurrency rule

OPPLAN tools must be called **strictly one at a time** — never two OPPLAN
tools in the same model step. The middleware will reject parallel
OPPLAN calls with an error so each call observes the previous result
before issuing the next. This applies to read tools (`get_objective`,
`list_objectives`) as well as mutating tools.
Every successful versioned plan mutation advances `plan_revision`. Before
dispatching `task`, use the revision in the live OPPLAN status below; the
`update_objective` result does not print it. After `update_objective` or a fact
change, do not reuse an earlier `commit_opplan` revision. Independent tasks
dispatched together use the same latest revision, after all preceding plan
mutations have completed.

### Workflow
```
load_opplan → inspect applicable scope, authorization, and planning context
          → commit_opplan(complete DAG, expected_revision=0)
          ↓
select status-ready leaf → verify scope/authorization → task(task_id, plan_revision)
          ↓
inspect evidence → update_objective(outcome, evidence_refs) → record_plan_fact
          ↓
revoke_plan_fact(fact_id, reason) if evidence is invalidated; reassess blocked work
          ↓
commit_opplan(complete DAG, expected_revision=current) when new work is discovered
```

### Status Transitions
```
pending → in-progress → completed    (evidence documented)
                       → blocked      (failure reason documented)
                       → cancelled    (abandon cleanly)
blocked → in-progress                 (retry with different approach)
        → cancelled                   (drop from plan)
```

### Rules — NEVER Violate
- NEVER execute objectives without the applicable engagement authorization
- NEVER call `update_objective` without calling `get_objective` first
- NEVER call OPPLAN tools in parallel (one tool per model step)
- ALWAYS provide a typed outcome and existing evidence paths when marking a
  versioned objective COMPLETED; `no-finding` is a valid completed result.
- ALWAYS include a typed failure outcome and reason/attempts in notes when
  marking a versioned objective BLOCKED.
- ALWAYS set owner to the assigned specialist before delegating.
- For dispatchers that accept `task_id` and `plan_revision`, bind them to the
  selected objective's saved ID and current plan revision. Use the live task
  schema for any additional required fields. Do not mutate the plan and
  dispatch work in the same model response.
- ALWAYS respect the validated DAG dependencies; phase is descriptive metadata,
  not an execution gate.
- NEVER invent ATT&CK names or descriptions; use the server's v19.2 catalog.
- NEVER use objective IDs as sequence numbers or infer execution order from them.
- Status-ready candidates are not authorization: check RoE and verify evidence
  before delegation or completion.
"""


# ── Formatting Helpers ────────────────────────────────────────────────


#: Maximum number of objective rows ``_format_opplan_status`` will
#: render into the system prompt. Past this cap, completed/cancelled
#: objectives collapse into a single summary line and only the
#: actionable (pending / in-progress / blocked) ones retain a full
#: table row. Overridable via ``DECEPTICON_OPPLAN_MAX_ROWS``.
try:
    _OPPLAN_MAX_ROWS = int(os.environ.get("DECEPTICON_OPPLAN_MAX_ROWS", "40"))
except ValueError:
    _OPPLAN_MAX_ROWS = 40

_STATUS_MARKERS = {
    "completed": "COMPLETED",
    "blocked": "BLOCKED",
    "cancelled": "CANCELLED",
    "in-progress": ">>IN-PROGRESS<<",
    "pending": "pending",
}

_TERMINAL_STATUSES = {"completed", "cancelled"}


def _format_opplan_status(
    objectives: list[dict],
    engagement_name: str,
    threat_profile: str,
    status_ready_ids: tuple[str, ...] | None = None,
) -> str:
    """Format OPPLAN for system prompt injection (concise battle tracker).

    Injected every LLM call via wrap_model_call, providing dynamic
    situational awareness — the red team equivalent of a battle
    tracker. To bound token cost on long / deeply-expanded plans we
    trim terminal objectives (completed / cancelled) from the main
    table once the total row count exceeds ``_OPPLAN_MAX_ROWS``.
    """
    total = len(objectives)
    completed = 0
    blocked = 0
    in_progress = 0
    pending = 0
    cancelled = 0
    for o in objectives:
        status = o.get("status") or ""
        if status == "completed":
            completed += 1
        elif status == "blocked":
            blocked += 1
        elif status == "in-progress":
            in_progress += 1
        elif status == "pending":
            pending += 1
        elif status == "cancelled":
            cancelled += 1

    actionable = [
        o
        for o in objectives
        if (
            o.get("id") in status_ready_ids
            if status_ready_ids is not None
            else o.get("status") in ("pending", "in-progress")
        )
    ]
    actionable.sort(key=lambda o: o.get("priority", 999))
    next_obj = actionable[0] if actionable else None

    progress_line = (
        f"Progress: {completed}/{total} completed, {blocked} blocked, "
        f"{in_progress} in-progress, {pending} pending"
    )
    if cancelled:
        progress_line += f", {cancelled} cancelled"

    lines = [
        "<OPPLAN_STATUS>",
        f"Engagement: {engagement_name}",
        f"Threat Profile: {threat_profile}",
        progress_line,
        *(
            [
                "Status-ready candidates: "
                f"{', '.join(status_ready_ids) or 'none'} "
                "(RoE and evidence not checked)"
            ]
            if status_ready_ids is not None
            else []
        ),
        "",
        "| ID | Phase | Title | Status | Priority | Owner |",
        "|---|---|---|---|---|---|",
    ]

    # Render actionable objectives in full, then terminal ones only
    # until the row budget is exhausted.
    sorted_objectives = sorted(objectives, key=lambda x: x.get("priority", 999))
    actionable_rows: list[dict[str, Any]] = []
    terminal_rows: list[dict[str, Any]] = []
    for o in sorted_objectives:
        if o.get("status") in _TERMINAL_STATUSES:
            terminal_rows.append(o)
        else:
            actionable_rows.append(o)

    rendered = 0
    for o in actionable_rows:
        status_marker = _STATUS_MARKERS.get(o.get("status", ""), o.get("status", ""))
        lines.append(
            f"| {o.get('id', '?')} | {o.get('phase', '?')} | "
            f"{o.get('title', '?')} | {status_marker} | "
            f"{o.get('priority', '?')} | {o.get('owner') or '-'} |"
        )
        rendered += 1

    remaining_budget = max(0, _OPPLAN_MAX_ROWS - rendered)
    shown_terminal = terminal_rows[:remaining_budget]
    for o in shown_terminal:
        status_marker = _STATUS_MARKERS.get(o.get("status", ""), o.get("status", ""))
        lines.append(
            f"| {o.get('id', '?')} | {o.get('phase', '?')} | "
            f"{o.get('title', '?')} | {status_marker} | "
            f"{o.get('priority', '?')} | {o.get('owner') or '-'} |"
        )
    hidden = len(terminal_rows) - len(shown_terminal)
    if hidden > 0:
        lines.append(f"| … | … | _{hidden} more terminal objectives_ | … | … | … |")

    if next_obj:
        lines.extend(
            [
                "",
                f"**Next**: {next_obj.get('id')} — {next_obj.get('title')}",
                f"  Phase: {next_obj.get('phase')} | "
                f"MITRE: {', '.join(next_obj.get('mitre') or []) or 'n/a'} | "
                f"OPSEC: {next_obj.get('opsec', 'standard')} | "
                f"C2: {next_obj.get('c2_tier', 'interactive')}",
            ]
        )
        criteria = next_obj.get("acceptance_criteria", [])
        if criteria:
            lines.append("  Acceptance Criteria:")
            for c in criteria:
                lines.append(f"    - [ ] {c}")
    else:
        lines.append("")
        # Guard against the empty-objectives case: ``all([])`` is vacuously
        # True, which previously rendered "ALL OBJECTIVES COMPLETE" for an
        # engagement that had zero objectives ever defined. Treat zero as
        # "no plan yet" rather than "all done".
        all_done = bool(objectives) and all(o.get("status") == "completed" for o in objectives)
        if all_done:
            lines.append("**ALL OBJECTIVES COMPLETE** — Generate final engagement report.")
        elif not objectives:
            lines.append("**No objectives defined** — Add objectives to begin the engagement.")
        else:
            lines.append("**No actionable objectives** — Review blocked items for retry.")

    lines.append("</OPPLAN_STATUS>")
    return "\n".join(lines)


# ── Middleware Class ──────────────────────────────────────────────────


class OPPLANMiddleware(AgentMiddleware):
    """Domain-specific OPPLAN tracking for red team engagements.

    Tools execute CRUD logic directly via InjectedState, appearing as proper
    `tool` type runs in LangSmith.

    - __init__: creates OPPLAN CRUD tools
    - wrap_model_call: injects dynamic OPPLAN progress into system message
    - after_model: validates no parallel state-mutating calls

    State schema (OPPLANState) is auto-merged by create_agent().
    """

    state_schema = OPPLANState

    def __init__(
        self,
        backend: BackendProtocol | None = None,
        initial_objectives: list[dict[str, Any]] | None = None,
        engagement_name: str = "",
        threat_profile: str = "",
        migrate_legacy_plan: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._backend = backend
        self._initial_objectives = initial_objectives
        self._engagement_name = engagement_name
        self._threat_profile = threat_profile
        self._migrate_legacy_plan = migrate_legacy_plan
        self.tools = build_opplan_tools(backend)

    @override
    def before_agent(self, state, runtime):
        if not state.get("workspace_path") or self._backend is None:
            return None
        if state.get("objectives") and state.get("plan_revision", 0):
            return None
        token = self._bind_run_sandbox(runtime)
        try:
            if state.get("objectives"):
                payload = {
                    "engagement_name": state.get("engagement_name", ""),
                    "threat_profile": state.get("threat_profile", ""),
                    "objectives": state["objectives"],
                    "facts": state.get("plan_facts", []),
                    "revision": 0,
                }
                try:
                    plan = OPPLAN.model_validate(payload)
                except ValueError:
                    if self._migrate_legacy_plan is None:
                        raise
                    plan = OPPLAN.model_validate(self._migrate_legacy_plan(payload))
                plan = _upgrade_unversioned_plan(self._backend, state["workspace_path"], plan)
                return {
                    "objectives": [
                        objective.model_dump(mode="json") for objective in plan.objectives
                    ],
                    "plan_facts": [fact.model_dump(mode="json") for fact in plan.facts],
                    "plan_revision": plan.revision,
                }
            scoped = _scoped_opplan_backend(self._backend, state["workspace_path"])
            if scoped is None:
                return None
            content, error = _read_text_from_backend(scoped, OPPLAN_VIRTUAL_PATH)
            if content is not None:
                payload = json.loads(content)
                try:
                    plan = OPPLAN.model_validate(payload)
                except ValueError:
                    if self._migrate_legacy_plan is None:
                        raise
                    migrated = self._migrate_legacy_plan(payload)
                    plan = OPPLAN.model_validate(migrated)
                    next_revision = int(payload.get("revision", 0)) + 1
                    persist_error = _persist_opplan_to_backend(
                        self._backend,
                        state["workspace_path"],
                        [objective.model_dump(mode="json") for objective in plan.objectives],
                        plan.engagement_name,
                        plan.threat_profile,
                        revision=next_revision,
                        facts=[fact.model_dump(mode="json") for fact in plan.facts],
                        strict=True,
                        expected_revision=int(payload.get("revision", 0)),
                    )
                    if persist_error:
                        raise RuntimeError(f"OPPLAN migration failed: {persist_error}")
                    plan.revision = next_revision
                plan = _upgrade_unversioned_plan(self._backend, state["workspace_path"], plan)
                return {
                    "objectives": [
                        objective.model_dump(mode="json") for objective in plan.objectives
                    ],
                    "plan_facts": [fact.model_dump(mode="json") for fact in plan.facts],
                    "plan_revision": plan.revision,
                    "engagement_name": plan.engagement_name,
                    "threat_profile": plan.threat_profile,
                }
            if error and "not found" not in error.lower() and "file_not_found" not in error:
                raise RuntimeError(f"Cannot read OPPLAN: {error}")
            if not self._initial_objectives:
                return None
            rows, _, _ = _assign_objective_ids(self._initial_objectives, [], set())
            rows = _canonicalize_attack_rows(rows, {})
            objectives = [Objective.model_validate(row) for row in rows]
            inspection = _inspect_objective_rows(
                [objective.model_dump(mode="json") for objective in objectives]
            )
            if inspection.issues:
                raise ValueError(
                    "Invalid initial OPPLAN: "
                    + "; ".join(issue.describe() for issue in inspection.issues)
                )
            name = self._engagement_name or state.get("engagement_name", "")
            profile = self._threat_profile or state.get("threat_profile", "")
            objective_rows = [objective.model_dump(mode="json") for objective in objectives]
            persist_error = _persist_opplan_to_backend(
                self._backend,
                state["workspace_path"],
                objective_rows,
                name,
                profile,
                revision=1,
                facts=[],
                strict=True,
                expected_revision=0,
            )
            if persist_error:
                raise RuntimeError(f"Initial OPPLAN was not saved: {persist_error}")
            return {
                "objectives": objective_rows,
                "plan_facts": [],
                "plan_revision": 1,
                "engagement_name": name,
                "threat_profile": profile,
            }
        finally:
            if token is not None:
                _run_sandbox_backend.reset(token)

    def _bind_run_sandbox(self, runtime):
        config = getattr(runtime, "config", None)
        if not config or not (config.get("configurable") or {}).get("sandbox_url"):
            return None
        from decepticon.backends import build_sandbox_backend, make_agent_backend

        return _run_sandbox_backend.set(make_agent_backend(build_sandbox_backend(config)))

    @override
    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] not in OPPLAN_TOOL_NAMES:
            return handler(request)
        token = self._bind_run_sandbox(request.runtime)
        try:
            return handler(request)
        finally:
            if token is not None:
                _run_sandbox_backend.reset(token)

    @override
    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] not in OPPLAN_TOOL_NAMES:
            return await handler(request)
        token = self._bind_run_sandbox(request.runtime)
        try:
            return await handler(request)
        finally:
            if token is not None:
                _run_sandbox_backend.reset(token)

    # ── wrap_model_call: inject OPPLAN context ────────────────────────

    @override
    def wrap_model_call(self, request, handler):
        """Inject OPPLAN system prompt + dynamic progress into system message."""
        return handler(self._inject_opplan_context(request))

    @override
    async def awrap_model_call(self, request, handler):
        """Async variant — identical logic."""
        return await handler(self._inject_opplan_context(request))

    def _inject_opplan_context(self, request):
        """Build request with OPPLAN context injected into system message.

        Splits the injection into TWO content blocks so the Anthropic prompt
        cache can reuse the static prefix across turns:

          - **Static block** — `OPPLAN_SYSTEM_PROMPT` (identical every turn)
            tagged with `cache_control: {"type": "ephemeral"}`. This anchors a
            cache breakpoint after every static system content (base prompt +
            engagement + skills + subagents + this static OPPLAN block).
          - **Dynamic block** — formatted objective status table (changes when
            objectives change status). No cache marker — recomputed every turn.

        AnthropicPromptCachingMiddleware additionally marks the LAST block
        (this dynamic one) per its own policy; Anthropic supports up to 4
        cache_control breakpoints so the two coexist without conflict. The
        static prefix cache hit avoids re-billing ~10K+ tokens of engagement
        context + skills catalog + subagent descriptions on every turn.
        """
        objectives = request.state.get("objectives", [])
        engagement = request.state.get("engagement_name", "")
        threat = request.state.get("threat_profile", "")

        static_block: dict[str, Any] = {
            "type": "text",
            "text": f"\n\n{OPPLAN_SYSTEM_PROMPT}",
            "cache_control": {"type": "ephemeral"},
        }
        injected_blocks: list[dict[str, Any]] = [static_block]

        if objectives:
            inspection = _inspect_objective_rows(objectives, request.state.get("plan_facts", []))
            dynamic_text = _format_opplan_status(
                objectives, engagement, threat, inspection.status_ready_ids
            )
            dynamic_text += f"\nPlan revision: {request.state.get('plan_revision', 0)}"
            if inspection.issues:
                details = "; ".join(issue.describe() for issue in inspection.issues)
                dynamic_text += f"\nGraph integrity warnings: {details}"
            injected_blocks.append({"type": "text", "text": f"\n\n{dynamic_text}"})

        if request.system_message is not None:
            new_content = [
                *request.system_message.content_blocks,
                *injected_blocks,
            ]
        else:
            new_content = injected_blocks

        new_system = SystemMessage(content=cast("list[str | dict[str, str]]", new_content))
        return request.override(system_message=new_system)

    # ── after_model: validate constraints ─────────────────────────────

    @override
    def after_model(self, state, runtime):
        """Reject parallel OPPLAN tool calls in the same model step.

        Mutating tools (commit/update/load_opplan) race on
        ``state.objectives`` because the field has no merge reducer; reads
        (get/list) gain nothing from parallelism but mixing them with writes
        muddies the contract. Apply one rule: at most one OPPLAN tool per
        LLM step. Each call gets a separate ToolMessage error so the LLM
        can re-issue them sequentially.
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        if not last_ai or not last_ai.tool_calls:
            return None

        opplan_calls = [tc for tc in last_ai.tool_calls if tc["name"] in OPPLAN_TOOL_NAMES]
        rejected: list[ToolMessage] = []
        if len(opplan_calls) > 1:
            names = ", ".join(sorted({tc["name"] for tc in opplan_calls[1:]}))
            rejected.extend(
                [
                    ToolMessage(
                        content=(
                            f"Error: parallel OPPLAN calls ({names}) rejected — "
                            "re-issue one at a time after the first completes."
                        ),
                        tool_call_id=tc["id"],
                        status="error",
                    )
                    for tc in opplan_calls[1:]
                ]
            )

        if any(tc["name"] in _PLAN_MUTATION_TOOLS for tc in opplan_calls):
            rejected.extend(
                ToolMessage(
                    content=(
                        "Error: task() cannot run in the same model step as an "
                        "OPPLAN mutation. Re-issue task() after the plan tool completes."
                    ),
                    tool_call_id=tc["id"],
                    status="error",
                )
                for tc in last_ai.tool_calls
                if tc["name"] == "task"
            )

        return {"messages": rejected} if rejected else None

    @override
    async def aafter_model(self, state, runtime):
        """Async variant delegates to sync."""
        return self.after_model(state, runtime)
