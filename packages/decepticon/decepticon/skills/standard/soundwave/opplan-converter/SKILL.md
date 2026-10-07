---
name: opplan-converter
description: "Convert engagement documents into machine-readable OPPLAN for the ralph loop — objective decomposition, acceptance criteria, MITRE mapping, priority ordering."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "create OPPLAN, generate objectives, set up the loop, convert plan to tasks, make it runnable"
  tags: opplan, objectives, ralph-loop, automation
  upstream_ref: "Soundwave OPPLAN converter — engagement document → machine-readable objectives for the ralph loop"
---

# OPPLAN Converter — CONOPS to Ralph Loop Format

The OPPLAN is the **direct analogue of ralph's prd.json** — it's the file the autonomous loop reads each iteration to decide what to do next. Each objective must be completable by one agent in one context window.

## When to Use

- After both `plan/roe.json` and `plan/conops.json` exist
- User wants to convert planning docs into executable tasks
- Starting the autonomous red team loop

## Prerequisites

Read both `plan/roe.json` and `plan/conops.json` first. The RoE constrains what's allowed; the CONOPS defines the kill chain and threat profile.

See `../references/schema-quick-reference.md` for the `OPPLAN` and `Objective` schema fields, valid status values, and enum types.

## Workflow

### Step 1: Extract Kill Chain from CONOPS

Read the CONOPS kill chain phases. Only create objectives for authorized phases.

### Step 2: Decompose into Objectives

Each objective must follow the **one context window** rule — if an agent can't complete it in a single session, it's too big. Split it.

See `references/objective-templates.md` for recon-phase templates and `references/objective-rules.md` for the complete decomposition rules.

Omit `id` for new objectives. The OPPLAN server issues an opaque UUID for each
node. Within one `commit_opplan` call, reference new dependencies by their
zero-based positions in the submitted `objectives` array.

**Phase → Sub-Agent Routing:**

| Workflow phase | Typical sub-agent |
|----------------|-------------------|
| recon | recon |
| initial-access | exploit |
| post-exploit | postexploit |
| c2 | postexploit |
| exfiltration | postexploit |
| reporting | analyst |

The phase describes the workflow and does not determine the ATT&CK tactic.
Set `attack_tactic_id` and `mitre` only after checking the current catalog.

### Step 3: Write Acceptance Criteria

Every objective MUST have three mandatory criteria types:

1. **Scope check** — "All targets verified against plan/roe.json in-scope list"
2. **OPSEC check** — At least one OPSEC-related criterion (rate limit, timing, etc.)
3. **Output persistence** — "Results saved to <engagement>/recon/..." (or exploit/, post-exploit/) with specific file path

Beyond these, add criteria specific to what the objective accomplishes. Every criterion must be mechanically verifiable — no vague statements like "good coverage."

### Step 4: Assign Metadata

For each objective:
- **priority** — Relative scheduling priority; dependencies define execution order
- **mitre** — Current Enterprise ATT&CK technique IDs under `attack_tactic_id`
- **opsec** — OPSEC level: loud, standard, careful, quiet, silent
- **opsec_notes** — Specific OPSEC constraints for this objective
- **c2_tier** — C2 tier matching OPSEC level: interactive, short-haul, long-haul
- **concessions** — Pre-authorized assists if objective is blocked (TIBER/CORIE concept)
- **blocked_by** — Existing objective IDs or positions of new prerequisites
- **any_of** — Alternative prerequisite groups for parallel branches

### Step 5: Generate OPPLAN

Use `commit_opplan` with the complete DAG, `expected_revision=0`, and the
engagement name and threat profile. The server returns issued UUIDs by
submitted position. Read the committed plan with `load_opplan` or
`list_objectives`, then present it for user approval. For replans, retain
existing IDs and submit the complete graph with its current revision.

### Step 6: Validate

See `references/objective-rules.md` validation checklist before finalizing.

## Output

Present a summary table showing all objectives with phase, priority, title, OPSEC level, and MITRE mapping. Wait for user approval before starting execution.
