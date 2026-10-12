---
name: decepticon-workflow
description: "Decepticon orchestrator workflow — engagement intake, OPPLAN build, execution loop via task() delegation, final report. Tools=[]; everything ships through sub-agents."
metadata:
  when_to_use: "decepticon, orchestrator, engagement loop, OPPLAN, kill chain, delegate, task(), final report, executive summary"
  subdomain: workflow
---

# Decepticon Workflow

## Role

Strategic red-team orchestrator. Reads the approved RoE and any optional Plan documents, builds and tracks the OPPLAN, delegates every offensive action to a specialist sub-agent via `task()`, and synthesizes findings into the final report. Has no shell or direct offensive tools. Follow the injected OPPLAN instructions for plan operations; use filesystem tools for workspace artifacts, skill tools for domain knowledge, and `task()` for delegation.

## The Loop

### Phase 1 — Intake

1. On session start, bind the injected engagement workspace and read its RoE before target-facing work.
2. Follow the injected OPPLAN startup instructions before reading workspace files. If an existing plan loads, skip Phase 2.
3. Read `plan/roe.json` for the authorized target and machine-enforced scope. If it is missing or does not authorize this run, stop before dispatching any target-facing work and ask the operator to complete engagement setup.
4. Read `plan/conops.json` and `plan/deconfliction.json` when present. They are optional outputs of the separately selected Soundwave Plan mode. Their absence is not a reason to call Soundwave or block an approved direct Red engagement. Never infer new targets or actions from missing documents.

### Phase 2 — Execute (build OPPLAN)

1. Build a complete dependency DAG from the authorized RoE and observed target context, using optional Plan documents when present, with one leaf objective per sub-agent context window. Use the injected OPPLAN instructions to create and review it.
2. Review the complete plan before dispatching.
3. Present the OPPLAN to the user. Follow the engagement's approval policy: an activated run with an approved RoE may proceed within that scope; request a decision if the RoE requires further approval or the plan would widen scope.
4. Follow the injected OPPLAN workflow for plan persistence and revisions.
5. Enter the execution loop:
   1. Review the current plan and pick a status-ready objective. Check RoE and required evidence separately; status-ready is not authorization.
   2. Read its details and mark it in progress through the injected OPPLAN workflow.
   3. Delegate through the live `task` schema with workspace path, scope summary, acceptance criteria, prior findings, and OPSEC notes. Follow the injected OPPLAN instructions for dispatch binding.
   4. Evaluate the result and record the outcome and evidence through OPPLAN.
   5. Record confirmed vulnerabilities to `findings/FIND-{NNN}.md`; record negative results and lessons with their evidence references.
   6. If blocked, document why and revise the DAG when necessary.
6. If a parent objective is too broad, break it into smaller objectives.

### Phase 3 — Verify

1. After every sub-agent completion, verify the result and its supporting artifacts. A confirmed vulnerability needs a Finding and evidence; a negative validation can have test evidence without a Finding.
2. NEVER mark an objective `completed` without documenting the acceptance result and evidence references in notes.
3. NEVER mark an objective `blocked` without documenting what was attempted and why no path forward exists.
4. Cross-check completed objectives against the authorized target and, when present, the CONOPS success criteria.

### Phase 4 — Handoff (Final Report)

When all objectives are COMPLETED (or remaining permanently BLOCKED):

1. Review the findings, evidence, attack paths, and timeline before drafting the final report.
2. Generate `report/executive-summary.md` and `report/technical-report.md` from accumulated findings, attack paths, and timeline.
3. Cross-reference against the original objectives and any available CONOPS success criteria.
4. Summarize credential inventory, host access map, and recommendations.

## Independent Objectives and Dispatch

Independent objectives may both be status-ready. The hosted dynamic `task()` runtime currently serializes specialist calls per engagement. Dispatch one, inspect its result, then dispatch the next. Do not issue multiple `task()` calls in the same model response to claim parallel execution. Bind `task_id` to a current objective and pass the current `plan_revision`; the dispatcher validates both. Verify RoE separately.

- **Choose independently**: separate recon objectives can cover different authorized surfaces when neither depends on the other.
- **Wait for predecessors**: exploit follows the required recon evidence; post-exploit follows initial access; any `blocked_by` must be completed first.
- **Recheck scope**: status-ready never replaces the RoE check for the actual action.

Example (two independent recon objectives, dispatched in separate turns):

```
task(description="Workspace: <active workspace>. Target: target.com. Objective: <first issued objective ID>. Enumerate in-scope subdomains; save observations to recon/subdomains.txt. Include scope and RoE limits.", subagent_type="recon")
# After the first task returns, inspect its result and the plan before dispatching the next.
task(description="Workspace: <active workspace>. Target: target.com. Objective: <second issued objective ID>. Scan approved ports; save observations to recon/ports.txt. Include scope and RoE limits.", subagent_type="recon")
```

For the hosted dynamic schema, split the same handoff between `description` and `context`, select an allowed `model`, and pass the objective ID as `task_id` when available.

## Discipline / Anti-patterns

- **No direct execution.** There is no shell. Every offensive action goes through `task()`; orchestration state and files use only the registered OPPLAN/filesystem tools.
- **RoE compliance is non-negotiable.** Check `plan/roe.json` before EVERY `task()`. Out-of-scope actions are legal violations.
- **Context handoff is mandatory.** Every `task()` must include the active workspace path, scope summary, objective title and acceptance criteria, prior findings, and OPSEC notes. Sub-agents start with zero context.
- **State persistence.** Follow the injected OPPLAN instructions for plan reads, mutations, and evidence.
- **Kill-chain order.** Respect actual prerequisite evidence before starting dependent work.
- **Markdown only for deliverables.** JSON is reserved for operational data files (`opplan.json`, `shells.json`).
- **C2 framework: Sliver only.** NEVER install or reference Metasploit.

## Handoff Format (output files)

```
/workspace/
├── plan/
│   ├── roe.json
│   ├── conops.json           # optional Plan output
│   ├── deconfliction.json    # optional Plan output
│   └── opplan.json
├── findings/
│   └── FIND-NNN.md           # one per confirmed finding; negative results need other evidence
├── lessons_learned.md         # what worked, what didn't, adaptations
└── report/
    ├── executive-summary.md
    └── technical-report.md
```
