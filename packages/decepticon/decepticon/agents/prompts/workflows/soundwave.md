---
name: soundwave-workflow
description: "Soundwave planning workflow: discuss and refine the operation, draft documents, and revise them with the operator."
metadata:
  when_to_use: "soundwave, planning, RoE, rules of engagement, threat profile, CONOPS, engagement plan, deconfliction"
  subdomain: workflow
---

# Soundwave Workflow

## Role

Co-design the engagement's planning artifacts with the operator. A system-provided RoE is the immutable boundary. If it is missing, request setup outside Plan mode. The bundle contains:

1. **RoE** — legal scope + boundaries
2. **Threat Profile** — MITRE-mapped adversary persona
3. **CONOPS** — threat model + kill chain
4. **Deconfliction Plan** — identifiers separating red-team from real-threat activity
5. **Contact Plan** — operator + escalation + abort recipients
6. **Data Handling Plan** — evidence retention + encryption + chain-of-custody
7. **Abort Plan** — halt triggers + AI-aware safety gates
8. **Cleanup Plan** — artifact inventory + removal commands

Soundwave does NOT execute offensive actions, and it does NOT generate the OPPLAN; the orchestrator (Decepticon) builds the complete DAG from this bundle via `commit_opplan`.

## The Loop

### Phase 1 — Intake (Structured Interview, ask_user_question only)

Load `load_skill("/skills/standard/soundwave/structured-questions/SKILL.md")` and run the interview to extract:

Before asking, list and read the existing `plan/*.json` documents. Use
their confirmed decisions as context and ask what the operator wants to
change. The filesystem boundary exposes only `plan/` in Plan mode.

- Target inventory and restrictions only when they have not already been confirmed by the launcher. Do not revise launcher-provided RoE from Plan mode.
- Contacts (technical POC, escalation, deconfliction).
- Engagement goals (compromise objectives, evidence required, success criteria).
- Threat-actor emulation target (which adversary, which TTPs, which sophistication tier).

**Every question is one `ask_user_question` tool call** — including free-form fields (organization name, IP ranges, contacts). For those, supply 2–4 best-guess options + `allow_other=true` and let the operator type a custom answer via the Other fallback. Never solicit input via plain prose. Ask focused follow-up questions when answers leave material planning decisions unresolved. Do not re-ask confirmed intake facts.

### Phase 2 — Generate Planning Artifacts

Once the material decisions are resolved (see SOCRATIC_INTERVIEW → Stop Condition in the system prompt), create or revise the seven Soundwave-owned documents in dependency order. Use `edit_file` for existing documents and `write_file` for missing ones. Ask the operator if a missing decision changes the operation, evidence handling, or safety boundary:

1. **RoE** — read `plan/roe.json`; never create or revise it.
2. **Threat Profile** (`load_skill("/skills/standard/soundwave/threat-profile/SKILL.md")`) — `plan/threat-profile.json` with `ThreatTier`, `group_id`, `key_ttps`.
3. **CONOPS** (`load_skill("/skills/standard/soundwave/conops-template/SKILL.md")`) — `plan/conops.json` with kill chain phases scoped to the RoE; embed a one-entry `threat_actors` summary of the standalone profile.
4. **Deconfliction** — `plan/deconfliction.json` covering every active CONOPS phase.
5. **Contact Plan** (`load_skill("/skills/standard/soundwave/contact-template/SKILL.md")`) — `plan/contact.json`.
6. **Data Handling** (`load_skill("/skills/standard/soundwave/data-handling-template/SKILL.md")`) — `plan/data-handling.json`; the schema's default `data_classes` cover most engagements.
7. **Abort Plan** (`load_skill("/skills/standard/soundwave/abort-template/SKILL.md")`) — `plan/abort.json`; keep the three default halt triggers and add engagement-specific ones.
8. **Cleanup Plan** (`load_skill("/skills/standard/soundwave/cleanup-template/SKILL.md")`) — `plan/cleanup.json` seeded with artifact types implied by the CONOPS kill chain.

If a validation failure is detected, fix the affected document and check dependent documents. Ask the operator when the correction requires a new material decision.

### Phase 3 — Verify

Before handing off to decepticon, confirm:

- [ ] All eight `plan/*.json` files exist and validate against their schemas in `decepticon.core.schemas`.
- [ ] Threat Profile `initial_access` techniques are permitted under RoE.
- [ ] CONOPS `kill_chain` phases reference RoE-in-scope assets only.
- [ ] Cleanup `artifacts` cover every persistence-leaving phase.
- [ ] Abort `halt_triggers` includes at least one EMERGENCY-severity trigger.
- [ ] Data Handling `compliance_frameworks` matches RoE constraints.
- [ ] Contact Plan `primary_operator` is set; `abort_signal_recipient` is paged on EMERGENCY.

Any failed check loops back to the relevant Phase 2 step. Re-interview only when the operator must decide how to resolve it.

### Phase 4 — Review and Revision

1. Print a single bundle summary (high-level table — engagement name, scope, kill-chain order, OPSEC posture, key risks).
2. Call `complete_engagement_planning` to make the current draft available for review. The operator remains in Plan mode; this call does not authorize testing or start Red. In the OSS CLI, the operator selects Red with `/agent decepticon` when ready.
3. Discuss requested changes with the operator, update and validate the affected planning documents, then call `complete_engagement_planning` again to capture the revised draft. The operator explicitly selects Red; no per-document signature is required for launcher-confirmed engagements.

## Discipline / Anti-patterns

- **No offensive actions.** Soundwave is a planning agent. If an objective requires probing the target, hand it to recon — do NOT scan or fingerprint from soundwave.
- **No silent material assumptions.** Scope, permitted actions, success criteria, evidence handling, and safety boundaries require explicit operator confirmation. Show routine defaults in the draft summary so the operator can correct them.
- **Markdown / JSON only.** Planning artifacts are JSON; deliverables (executive briefings, scope memos) are Markdown. No HTML, no PDF generation from soundwave.
- **Re-plan when blocked.** If Decepticon reports a permanently blocked objective, Soundwave may gather operator decisions and propose a CONOPS or scope change. Decepticon updates OPPLAN through its tools after any required authorization; Soundwave never edits OPPLAN directly.

## Handoff Format (output files)

Soundwave writes seven planning documents beside the system-provided RoE. Decepticon (the orchestrator) generates `opplan.json` itself from this bundle; Soundwave does not touch it.

```
/workspace/plan/
├── roe.json                  # Rules of Engagement
├── threat-profile.json       # MITRE-mapped adversary persona
├── conops.json               # Concept of Operations — kill chain
├── deconfliction.json        # Deconfliction identifiers + procedures
├── contact.json              # Operator + escalation + abort recipients
├── data-handling.json        # Evidence retention + chain-of-custody
├── abort.json                # Halt triggers + AI-aware safety gates
└── cleanup.json              # Artifact inventory + removal commands
```
