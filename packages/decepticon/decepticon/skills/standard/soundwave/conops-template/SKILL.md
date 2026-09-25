---
name: conops-template
description: "Concept of Operations document creation — executive summary, threat actor profiling, attack narrative, kill chain design, communication plan, deconfliction."
allowed-tools: Read Write Edit
metadata:
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
- Before OPPLAN can be generated

## Prerequisites

Read `plan/roe.json` first — scope and boundaries constrain the CONOPS.

See `../references/schema-quick-reference.md` for the `CONOPS`, `ThreatActor`, `KillChainPhase`, and `DeconflictionPlan` schema fields.

## Workflow

### Step 1: Interview the User

**Budget: 2 questions max for CONOPS/Threat Profile** (soundwave.md CRITICAL_RULES → Question Budget). The remaining fields below are DEFAULTED or AGENT-DRAFTED, not asked — the tier table and RoE Constraint→Profile Implication table in `threat-profile/SKILL.md` already give deterministic defaults for motivation and initial access once the tier is picked, so re-asking them wastes a turn.

**Question 1 — Threat actor tier** (single-select, use `threat-profile` skill for detailed profiling):
   - a) Opportunistic external attacker (low)
   - b) Targeted cybercriminal (medium)
   - c) APT / nation-state (high)
   - d) Insider threat
   - e) Custom — describe

Derive **motivation** and **initial access vector** from the picked tier via `threat-profile/references/adversary-archetypes.md` (default) — do not ask separately. Only deviate if the operator's free-text answer (via `allow_other`) already states a motivation/vector explicitly.

**Question 2 — Success criteria** — the crown-jewel / measurable win condition. Required; no default (every engagement needs an explicit end-state).

**Agent-drafted, not asked:**
- **Attack narrative** — write the 2-3 sentence scenario yourself from RoE scope + tier + success criteria. This is the agent's job, not the operator's homework.
- **Ultimate objectives** — derive from success criteria; do not ask as a separate dimension.
- ~~Communication plan~~ — REMOVED. `CONOPS.communication_plan` is DEPRECATED (see SCHEMA_REFERENCE in soundwave.md); `ContactPlan` (contact-template skill) owns this now. Asking about it here duplicates a question and writes to a dead field.
- **Deconfliction method** — default to standard red-team markers/headers (see `deconfliction-template` reference) unless the operator's RoE/contact answers already flagged a SOC integration endpoint; do not spend a dedicated question on it.

### Step 2: Design Kill Chain

Based on RoE scope + threat profile, select applicable phases. See `references/kill-chain-templates.md`.

**Key rule**: Don't include phases outside RoE scope. Recon-only engagement → only `recon` phase.

### Step 3: Generate Documents

1. `plan/conops.json` — matching `CONOPS` schema
2. `plan/deconfliction.json` — matching `DeconflictionPlan` schema

### Step 4: Validate

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
5. **Deconfliction defaults to standard red-team markers/headers** unless a SOC integration endpoint was already flagged; `communication_plan` is DEPRECATED — write coordination details to `ContactPlan` instead
