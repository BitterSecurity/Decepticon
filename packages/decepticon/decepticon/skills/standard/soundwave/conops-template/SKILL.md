---
name: conops-template
description: "Concept of Operations document creation — executive summary, threat actor profiling, attack narrative, kill chain design, communication plan, deconfliction."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "create CONOPS, design operation, threat model, plan attack"
  tags: conops, kill-chain, threat-model, operation-design
  upstream_ref: "Soundwave CONOPS template — Concept of Operations document generator"
---

# Concept of Operations (CONOPS) Generator

The CONOPS bridges the legal RoE and the tactical OPPLAN. It must be **readable by a CEO** while containing **enough detail for operators**.

## When to Use

- After `plan/roe.json` exists
- User says "create CONOPS", "design the operation", "build threat model"
- When the operator wants to design or revise a CONOPS. A direct Red run may build OPPLAN from the system RoE without this optional document.

## Prerequisites

Read `plan/roe.json` first — scope and boundaries constrain the CONOPS.

Use the inline schema tables below for `CONOPS`, `ThreatActor`, and `KillChainPhase` fields. The deconfliction-template skill describes `DeconflictionPlan`. Skillogy returns skill bodies without sibling reference files.

## Workflow

### Threat model and narrative

Use the tier table and RoE Constraint→Profile Implication table in `threat-profile/SKILL.md` to propose a threat profile. Ask the operator about material choices such as motivation, initial access, operation sequence, and success criteria when these are not already confirmed.

Use the confirmed **motivation**, **initial access vector**, and measurable
**success criteria**. Do not invent the engagement's end-state. Draft the attack
narrative from RoE scope, threat profile, and success criteria; derive objectives
from that same context. Coordination belongs in `ContactPlan`;
`CONOPS.communication_plan` is deprecated. Proposed deconfliction markers belong
in `DeconflictionPlan` and must not be described as configured without evidence.

### Kill-chain design

Based on RoE scope + threat profile, select applicable phases using the active planning schema; do not depend on a sibling reference file that Skillogy cannot return.

**Key rule**: Don't include phases outside RoE scope. Recon-only engagement → only `recon` phase.

### Output

1. Create or revise `plan/conops.json` with the matching `CONOPS` schema.
2. Keep the embedded `threat_actors` summary consistent with the standalone threat profile when present. Related document revisions and bundle publication follow the selected planning workflow.

### Validation

- Executive summary contains no jargon or tool names
- Kill chain phases align with RoE scope
- All MITRE ATT&CK technique IDs are valid
- Timeline has concrete date ranges
- At least 2 success criteria defined

## Generation Rules

1. **Executive summary = non-technical** — no tool names, no jargon
2. **Threat actor TTPs must reference MITRE ATT&CK IDs**
3. **Kill chain scoped to RoE** — no exploitation phase in recon-only engagement
4. **Timeline uses absolute dates** — never relative
5. **Deconfliction markers are proposals until confirmed**; `communication_plan` is DEPRECATED — write coordination details to `ContactPlan` instead

## Document schema (derived from the validator's model)

`plan/conops.json` must validate against `CONOPS`. Fields not listed here are not part of the model and are ignored by the validator.

| field | type | required | allowed values / nested keys |
| --- | --- | --- | --- |
| `engagement_name` | str | yes |  |
| `executive_summary` | str | yes |  |
| `threat_actors` | list[ThreatActor] | no | each object: `name`, `sophistication`, `motivation`, `initial_access`, `ttps` |
| `attack_narrative` | str | no |  |
| `kill_chain` | list[KillChainPhase] | no | each object: `phase`, `description`, `success_criteria`, `tools` |
| `methodology` | str | no |  |
| `communication_plan` | str | no |  |
| `phases_timeline` | dict[str, str] | no |  |
| `success_criteria` | list[str] | no |  |

`KillChainPhase` object:

| field | type | required | allowed values |
| --- | --- | --- | --- |
| `phase` | ObjectivePhase | yes | one of `recon`, `initial-access`, `post-exploit`, `c2`, `exfiltration`, `reporting` |
| `description` | str | yes |  |
| `success_criteria` | str | no |  |
| `tools` | list[str] | no |  |

`ThreatActor` object:

| field | type | required | allowed values |
| --- | --- | --- | --- |
| `name` | str | yes |  |
| `sophistication` | str | yes |  |
| `motivation` | str | yes |  |
| `initial_access` | list[str] | no |  |
| `ttps` | list[str] | no |  |
