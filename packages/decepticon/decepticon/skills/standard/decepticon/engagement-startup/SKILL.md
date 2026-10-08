---
name: engagement-startup
description: Reference for resuming an authorized engagement and preparing bounded OPPLAN objectives.
allowed-tools: Read
metadata:
  subdomain: orchestration
  when_to_use: "engagement start resume workspace authorization opplan"
  tags: startup, workspace, resume
  upstream_ref: "Decepticon engagement context and OPPLAN middleware startup contract"
---

# Engagement startup reference

The runtime and injected OPPLAN middleware own startup state. This skill is optional reference material when a startup or resume question needs more detail; loading it is not a first-turn prerequisite.

Use the workspace root provided by engagement context. Read the system-managed `plan/roe.json` before target-facing work. If it is absent, stop target-facing work and request product setup; do not ask Soundwave to write or repair the RoE. Respect its machine-enforced targets, exclusions, actions, and time limits. A direct-operator-attestation Red run may start from that RoE and the operator's instruction without requiring CONOPS or deconfliction documents.

When planning documents exist, read the applicable ones. Missing optional documents do not block a direct Red run. Soundwave collaborates with the operator in Plan mode on Soundwave-owned documents; an agent cannot revise the RoE. Do not enumerate the shared `/workspace` root or invent a new engagement directory.

For an existing OPPLAN, inspect objectives, dependencies, evidence, and current revision with OPPLAN tools. For a new run, derive bounded objectives from confirmed scope and the operator's instruction. Create and update objectives only through the injected OPPLAN workflow and tools; do not edit `plan/opplan.json` directly. Dispatch only ready, authorized objectives and bind each task to its objective ID and revision. An OPPLAN status is never a substitute for RoE authorization.
