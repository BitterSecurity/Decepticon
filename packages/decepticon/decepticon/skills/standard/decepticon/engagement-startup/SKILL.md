---
name: engagement-startup
description: "Mandatory first-turn startup procedure — checks for existing engagements, resume/new selection, workspace initialization."
allowed-tools: Read
metadata:
  subdomain: orchestration
  when_to_use: "agent startup, first message, session start"
  tags: startup, engagement-selection, workspace-init, resume
  upstream_ref: "Decepticon orchestrator first-turn bootstrap — workspace + engagement selection, no direct attack technique"
---

# Engagement Startup Procedure

**Execute this procedure on every session start, before any other action.**

## Step 1: Bind the Active Workspace

The launcher normally injects the workspace root. Use that exact root when it is
present in the engagement context; otherwise use `/workspace`.

Before calling `read_file`, `write_file`, `edit_file`, `ls`, `glob`, or `grep`,
call:

```
load_opplan(workspace_path="<active workspace root>")
```

This call has two outcomes:

- Existing `plan/opplan.json`: objectives and engagement metadata are loaded.
- Missing `plan/opplan.json`: the workspace is still bound so planning files
  can be created. This is the expected new-engagement path, not a fatal error.

Do not probe the filesystem before this call.

## Step 2: Inspect Planning State

Read the active workspace's RoE first:

```
read_file("<active workspace root>/plan/roe.json")
```

If the RoE is missing, stop target-facing work. Delegate to Soundwave to
recover the authorization boundary:

```
task(
    description="Workspace: <active workspace root>. Restore missing planning documents; identify operator input needed for signed scope and RoE.",
    subagent_type="soundwave",
)
```

If the hosted dynamic `task` schema is active, also provide its required `context`
and `model` fields, selecting an allowed model from the assignment manifest.

If `authorization.source` is `direct_operator_attestation`, the operator chose
Red mode and confirmed the stored target scope directly. Treat this RoE as the
authorization boundary. CONOPS and Deconfliction are optional in this path;
do not delegate to Soundwave because they are absent. Build the OPPLAN from
the confirmed targets and the operator's instruction. Never expand scope beyond
`machine_enforcement` rules.

Otherwise read `plan/conops.json` and `plan/deconfliction.json`. If either is
missing, delegate to Soundwave to recover the signed planning documents before
target-facing work.

The launcher already selected the engagement. Do not enumerate the shared
`/workspace` root, invent another workspace directory, or ask the operator to
select the engagement again.

## Step 3A: Resume an Existing OPPLAN

When `load_opplan` loaded objectives:

1. Read relevant files under `findings/`.
2. Summarize objectives completed / total, current phase, latest evidence, and
   the next pending objective.
3. Resume the active run within its signed scope and RoE. Ask the operator only when the authorization has expired, changed, or requires a new decision.

## Step 3B: Build a New OPPLAN

When no OPPLAN exists and the applicable authorization documents are present:

1. For a signed plan, read CONOPS goals and dependencies. For direct Red mode,
   derive bounded objectives from the confirmed targets and operator instruction.
2. Create one bounded objective per sub-agent context window. Submit the whole
   dependency DAG with `commit_opplan(objectives=[...], facts=[...],
   expected_revision=0)`. Omit IDs on new objectives; the server issues UUIDs.
   Reference new dependencies by zero-based position in `objectives`. A missing
   plan starts at revision 0. Declare future
   facts with a producer objective and `verified=false`; use `blocked_by` for
   all-of predecessors, `any_of` for alternatives, and `required_fact_ids` for
   evidence gates. Do not impose phase order unless the actual dependencies
   require it.
3. Present the complete OPPLAN. If the engagement approval policy requires a plan decision, ask before dispatch; otherwise an activated run may proceed within the approved RoE.
4. Enter the execution loop after the applicable authorization. OPPLAN mutations persist
   automatically; there is no separate save tool.

## Constraints

- The orchestrator has no shell. Never call `bash` from this workflow.
- Delegate C2 reachability or other execution checks to the appropriate
  specialist after creating an objective.
- Use only registered tool names; do not invent `enumerate_skills`,
  `save_opplan`, or other aliases.
