---
name: orchestration
description: "Decepticon orchestrator patterns — delegation, state management, adaptive re-planning, context handoff protocols."
allowed-tools: Read
metadata:
  subdomain: orchestration
  when_to_use: "delegate, orchestrate, next objective, blocked, re-plan, hand off, engagement state, status update"
  tags: orchestration, delegation, state-management, re-planning, context-handoff
  upstream_ref: "Decepticon orchestrator delegation / re-planning patterns — multi-agent control plane, no direct attack technique"
---

# Decepticon Orchestration Patterns

## Delegation Protocol

### Context Handoff — What Every Sub-Agent Needs
Every `task()` delegation MUST include:

1. **Objective** — What specifically to accomplish (from OPPLAN)
2. **Scope** — IN SCOPE targets + OUT OF SCOPE boundaries (from RoE)
3. **Context** — Relevant findings from previous phases
4. **Lessons** — Known gotchas, failed approaches, OPSEC warnings
5. **Acceptance Criteria** — How the sub-agent knows it's done
6. **Output Location** — Where to save results (e.g. `recon/`, `exploit/`)

### Delegation Template

Follow the live `task` tool schema. The OSS dispatcher accepts `description` and
`subagent_type`; the hosted dynamic dispatcher additionally requires `context`
and `model` and may accept `task_id`. Put the full handoff in `description` for
OSS, or split the task goal into `description` and the supporting facts into
`context` for the hosted dispatcher. Select `model` from its assignment manifest.
In the hosted dynamic dispatcher, `task_id` must be the OPPLAN objective ID
and `plan_revision` must match the current plan. These fields are checked at
dispatch; neither substitutes for RoE authorization.

```
task(
  description="""
  OBJECTIVE: {objective_id} — {title}
  PHASE: {phase}

  SCOPE:
  - IN: {in_scope_targets}
  - OUT: {out_of_scope_targets}

  CONTEXT FROM PREVIOUS PHASES:
  {relevant_findings_summary}

  LESSONS LEARNED:
  {known_gotchas}

  ACCEPTANCE CRITERIA:
  - [ ] {criterion_1}
  - [ ] {criterion_2}

  Save all results to {phase}/
  """,
  subagent_type="{agent_name}"
)
```

### Sub-Agent Selection Matrix

| Objective Phase | Sub-Agent | When to Use |
|----------------|-----------|-------------|
| Planning | `soundwave` | Missing roe.json/conops.json/deconfliction.json, or documents need updating |
| Recon | `recon` | Subdomain/port/service enumeration, OSINT, cloud/web recon |
| Exploitation | `exploit` | Initial access: SQLi, SSTI, AD attacks, credential exploitation |
| Post-Exploitation | `postexploit` | After foothold: cred dump, privesc, lateral movement, C2 |

### Independent Objectives

Independent objectives may be ready at the same time, but the hosted dynamic
dispatcher serializes specialist calls per engagement. Dispatch one, inspect
its result, then dispatch the next. Never dispatch an objective whose
`blocked_by`, `any_of`, or verified-fact prerequisite is unresolved. Separate OPPLAN mutations and `task()`
calls into different model responses. Check RoE for each actual action.
If recorded evidence is disproven or lost, call `revoke_plan_fact` with the
fact ID and reason before dispatching another objective. Reassess every
objective it blocks, including work that had previously completed.

## State Management

### Engagement State Files
```
./
├── plan/
│   ├── roe.json          # Immutable scope boundaries (read every iteration)
│   ├── conops.json       # Operation concept
│   ├── deconfliction.json # Deconfliction identifiers and procedures
│   └── opplan.json       # Objective tracker (update status after each sub-agent)
├── findings/            # Per-finding Markdown files, created lazily
├── lessons_learned.md    # Failed approaches + what worked
└── .ralph_state.json     # Loop iteration counter + completion flags
```

### State Update Protocol (After Each Sub-Agent Returns)
1. **Parse result** — What did the sub-agent actually observe, and where is its evidence? A returned task is not automatically a completed objective.
2. **Update objective state** — Call `get_objective` and then
   `update_objective` with `completed`, `blocked`, or `in-progress`; the OPPLAN
   middleware persists `plan/opplan.json` automatically.
3. **Record verified findings** — Add `findings/FIND-{NNN}.md` only when a real finding exists
4. **Append lessons_learned.md** — Record what worked, what failed, and why
5. **Check completion** — All objectives completed? → Generate summary

### Context Window Budget
- Read recent `findings/FIND-*.md` entries each iteration (keep only relevant excerpts)
- Summarize verbose sub-agent outputs before appending to findings
- Use files on disk as persistent memory — don't rely on conversation history

## After Recon Returns — Decision Tree

Review these questions after every recon task() completes.

```
1. Read recon/SUMMARY.md
   ├── Missing or empty? → Rule 13 crash protocol (retry once, then BLOCKED)
   └── Present → continue

2. Contains a credible in-scope path for exploitation?
   ├── YES → create or select an objective whose dependencies are satisfied,
   │         then dispatch a focused exploit task with the exact evidence.
   └── NO → record why, then consider a bounded follow-up or another ready objective.

3. RECON_BUDGET_EXHAUSTED with zero confirmed vulns?
   ├── Unvisited surface remains? → focused second recon turn on that surface
   └── No unvisited surface → update_objective(objective_id="<id>",
                               status="completed", outcome="no-finding",
                               evidence_refs=["/workspace/recon/SUMMARY.md"])
```

The OPPLAN dependency state does not authorize a new probe. Recheck the signed
RoE and any required operator approval before each delegation.

## Adaptive Re-planning

### When an Objective is BLOCKED
```
1. Document failure:
   - WHY it failed (specific error, defense mechanism, missing prerequisite)
   - WHAT was attempted (tools, techniques, targets)
   → Append to lessons_learned.md

2. Assess alternatives:
   - Different attack vector from findings?
   - Lower-risk approach?
   - Skip and return later after more intel?

3. Decision:
   IF alternative exists → delegate new task with adjusted approach
   IF prerequisite missing → add or update dependency links through OPPLAN tools
   IF no path forward → mark BLOCKED with explanation, move to next objective
```

### Re-ordering Objectives
The OPPLAN defines priority order among ready objectives. You may choose a
different ready objective when:
- A higher-priority objective depends on a lower-priority one
- New findings reveal a faster path to the same goal
- An objective is temporarily blocked and others are actionable

Never bypass an unresolved DAG prerequisite; document selection or re-planning
decisions in objective notes and lessons_learned.md.

## Response Format

### After Each Sub-Agent Completes
Report structured status:

| Objective | Phase | Sub-Agent | Result | Key Findings |
|-----------|-------|-----------|--------|-------------|
| `<server-issued-objective-id>` | Recon | recon | COMPLETED | 12 subdomains, AD on 10.0.0.5 |

### Decision Transparency
Before each delegation, briefly state:
- **Why** this objective is next (priority, dependency, re-plan reason)
- **Which** sub-agent and why
- **What** context you're passing

### Progress Summary
Maintain running status after each iteration:
```
Engagement: {name}
Progress: {completed}/{total} objectives
Current: <current objective title and ID> (Exploit phase)
Blocked: <blocked objective title and ID> (WAF blocking SQLi — will retry after credential access)
Next: <ready objective title and ID> (after its DAG prerequisites complete)
```

### Engagement Completion Report
When all objectives are done:
1. Full attack path (every hop, credential, escalation)
2. Credential inventory
3. Host access map
4. Recommendations for defensive improvements
