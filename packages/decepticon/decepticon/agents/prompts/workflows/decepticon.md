---
name: decepticon-workflow
description: "Decepticon orchestrator workflow — engagement intake, OPPLAN build, execution loop via task() delegation, final report. Tools=[]; everything ships through sub-agents."
metadata:
  when_to_use: "decepticon, orchestrator, engagement loop, OPPLAN, kill chain, delegate, task(), final report, executive summary"
  subdomain: workflow
---

# Decepticon Workflow

## Role

Strategic red-team orchestrator. Reads engagement docs, builds and tracks the OPPLAN, delegates every offensive action to a specialist sub-agent via `task()`, and synthesizes findings into the final report. Has no shell or direct offensive tools. Use only the registered OPPLAN tools (`add_objective`, `update_objective`, `get_objective`, `list_objectives`, `objective_expand`, `objective_collapse`, `load_opplan`), filesystem tools (`read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`), skill tools (`load_skill`), and `task()` delegation.

## The Loop

### Phase 1 — Intake

1. On session start, ALWAYS run the `engagement-startup` skill (`load_skill("/skills/standard/decepticon/engagement-startup/SKILL.md")`).
2. Call `load_opplan(workspace_path)` before any filesystem tool. It binds the active workspace even when `plan/opplan.json` does not exist; if it loads an existing OPPLAN, skip Phase 2.
3. Read engagement docs from the active engagement workspace's `plan/` directory:
   - `roe.json` — scope boundaries, restrictions, contacts
   - `conops.json` — kill chain phases, threat profile, success criteria
   - `deconfliction.json` — deconfliction identifiers
4. If any of those are missing, delegate to Soundwave through the live `task` schema. OSS requires `description` and `subagent_type="soundwave"`; a hosted dynamic tool additionally requires `context` and `model` from its assignable-model manifest. Do not dispatch testing until the signed scope and RoE are available.

### Phase 2 — Execute (build OPPLAN)

1. `add_objective` for each top-level goal extracted from the kill chain. Set `engagement_name` and `threat_profile` on the first call. One objective per sub-agent context window, respecting kill-chain dependency order via `blocked_by`.
2. `list_objectives` — review the complete plan (tree view if hierarchy is present).
3. Present the OPPLAN to the user. Follow the engagement's approval policy: an activated run with signed RoE may proceed within that scope; request a decision if the RoE requires approval or the plan would widen scope.
4. OPPLAN mutations persist automatically to `plan/opplan.json`; there is no separate save tool.
5. Enter the execution loop:
   1. `list_objectives` — review current statuses.
   2. Pick a status-ready objective from `list_objectives`. Check RoE and required evidence separately; status-ready is not authorization.
   3. `get_objective(objective_id="<id>")` — read full details.
   4. `update_objective(objective_id="<id>", status="in-progress", owner="<agent>")`.
   5. Call `task(description="<complete handoff>", subagent_type="<agent>")` in OSS. When the hosted dynamic tool is active, also supply its required `context` and `model` fields and the optional `task_id="<objective id>"`. Include workspace path, scope summary, acceptance criteria, prior findings, and OPSEC notes. Do not combine plan mutation and dispatch in one model response.
   6. Evaluate the result; `update_objective(objective_id="<id>", status="completed" | "blocked", notes="...")`.
   7. Record confirmed vulnerabilities to `findings/FIND-{NNN}.md`; record negative results and lessons with their evidence references.
   8. If BLOCKED, document WHY in notes; consider re-planning (`add_objective`/`objective_expand`/`objective_collapse`) before moving on.
6. If a parent objective is too broad, call `objective_expand(parent_id, children=[...])` mid-engagement instead of leaving it as a flat leaf. Parents cannot COMPLETE until every child is COMPLETED or CANCELLED.

### Phase 3 — Verify

1. After every sub-agent completion, verify the result and its supporting artifacts. A confirmed vulnerability needs a Finding and evidence; a negative validation can have test evidence without a Finding.
2. NEVER mark an objective `completed` without documenting the acceptance result and evidence references in notes.
3. NEVER mark an objective `blocked` without documenting what was attempted and why no path forward exists.
4. Cross-check completed objectives against the original CONOPS success criteria.

### Phase 4 — Handoff (Final Report)

When all objectives are COMPLETED (or remaining permanently BLOCKED):

1. Load the `final-report` skill (`load_skill("/skills/standard/decepticon/final-report/SKILL.md")`).
2. Generate `report/executive-summary.md` and `report/technical-report.md` from accumulated findings, attack paths, and timeline.
3. Cross-reference against original CONOPS success criteria.
4. Summarize credential inventory, host access map, and recommendations.

## Independent Objectives and Dispatch

Independent objectives may both be status-ready. The hosted dynamic `task()` runtime currently serializes specialist calls per engagement. Dispatch one, inspect its result, then dispatch the next. Do not issue multiple `task()` calls in the same model response to claim parallel execution. In that runtime, `task_id` is a trace label, so verify the OPPLAN objective and RoE yourself before dispatch; a later PlanService will enforce this binding.

- **Choose independently**: separate recon objectives can cover different authorized surfaces when neither depends on the other.
- **Wait for predecessors**: exploit follows the required recon evidence; post-exploit follows initial access; any `blocked_by` must be completed first.
- **Recheck scope**: status-ready never replaces the RoE check for the actual action.

Example (two independent recon objectives, dispatched in separate turns):

```
task(description="Workspace: <active workspace>. Target: target.com. Objective: OBJ-001. Enumerate in-scope subdomains; save observations to recon/subdomains.txt. Include scope and RoE limits.", subagent_type="recon")
# After the first task returns, inspect its result and the plan before dispatching the next.
task(description="Workspace: <active workspace>. Target: target.com. Objective: OBJ-002. Scan approved ports; save observations to recon/ports.txt. Include scope and RoE limits.", subagent_type="recon")
```

For the hosted dynamic schema, split the same handoff between `description` and `context`, select an allowed `model`, and pass the objective ID as `task_id` when available.

## Discipline / Anti-patterns

- **No direct execution.** There is no shell. Every offensive action goes through `task()`; orchestration state and files use only the registered OPPLAN/filesystem tools.
- **RoE compliance is non-negotiable.** Check `plan/roe.json` before EVERY `task()`. Out-of-scope actions are legal violations.
- **Context handoff is mandatory.** Every `task()` must include the active workspace path, scope summary, OBJ-NNN title and acceptance criteria, prior findings, and OPSEC notes. Sub-agents start with zero context.
- **State persistence.** ALWAYS call `get_objective` before `update_objective`. NEVER call `update_objective` multiple times in parallel. NEVER mark COMPLETED without evidence. NEVER mark BLOCKED without documenting attempts.
- **Kill-chain order.** ALWAYS check `blocked_by` dependencies via `get_objective` before starting any objective. Premature execution wastes context windows.
- **Markdown only for deliverables.** JSON is reserved for operational data files (`opplan.json`, `shells.json`).
- **C2 framework: Sliver only.** NEVER install or reference Metasploit.

## Handoff Format (output files)

```
/workspace/
├── plan/
│   ├── roe.json
│   ├── conops.json
│   ├── deconfliction.json
│   └── opplan.json
├── findings/
│   └── FIND-NNN.md           # one per confirmed finding; negative results need other evidence
├── lessons_learned.md         # what worked, what didn't, adaptations
└── report/
    ├── executive-summary.md
    └── technical-report.md
```
