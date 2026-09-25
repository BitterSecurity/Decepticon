---
name: soundwave-workflow
description: "Soundwave planning agent workflow — interview via ask_user_question, then write RoE / CONOPS / Deconfliction in one continuous pass, hand off to decepticon."
metadata:
  when_to_use: "soundwave, planning, RoE, rules of engagement, threat profile, CONOPS, engagement plan, deconfliction"
  subdomain: workflow
---

# Soundwave Workflow

## Role

Generate the engagement's eight planning artifacts through a structured interview with the operator, then hand off to decepticon for execution. The eight artifacts are:

1. **RoE** — legal scope + boundaries
2. **Threat Profile** — MITRE-mapped adversary persona
3. **CONOPS** — threat model + kill chain
4. **Deconfliction Plan** — identifiers separating red-team from real-threat activity
5. **Contact Plan** — operator + escalation + abort recipients
6. **Data Handling Plan** — evidence retention + encryption + chain-of-custody
7. **Abort Plan** — halt triggers + AI-aware safety gates
8. **Cleanup Plan** — artifact inventory + removal commands

Soundwave does NOT execute offensive actions, and it does NOT generate the OPPLAN; the orchestrator (Decepticon) builds the OPPLAN from this bundle via its own `add_objective` tool.

## The Loop

### Phase 1 — Intake (Structured Interview, ask_user_question only)

Load `load_skill("/skills/standard/soundwave/structured-questions/SKILL.md")` and run the interview to extract:

- Target inventory (domains, IP ranges, applications, accounts in scope; explicit out-of-scope items).
- Restrictions (time windows, blackout periods, prohibited techniques like DoS or social engineering, data classes that must not be touched).
- Contacts (technical POC, escalation, deconfliction).
- Engagement goals (compromise objectives, evidence required, success criteria).
- Threat-actor emulation target (which adversary, which TTPs, which sophistication tier).

**Every question is one `ask_user_question` tool call** — including free-form fields (organization name, IP ranges, contacts). For those, supply 2–4 best-guess options + `allow_other=true` and let the operator type a custom answer via the Other fallback. Never solicit input via plain prose. The picker's return value is the operator's confirmation for that dimension — no separate "write the answer back and ask again" round-trip. **Question Budget: ≤8 total** (system prompt CRITICAL_RULES #12) — Scope and Success criteria are always asked; Threat model/Kill chain/Constraints get one merged question each; Contacts/Data sensitivity/Abort triggers/Persistence footprint default from schema + RoE/CONOPS content unless the operator's answers raise a flag a default can't cover.

### Phase 2 — Generate Planning Artifacts (continuous, no approval gates)

Once Phase 1 has resolved every dimension (see SOCRATIC_INTERVIEW → Stop Condition in the system prompt), write all eight documents back-to-back without pausing for operator approval between them. Sequential because each depends on the previous output, but there is no human checkpoint in between:

1. **RoE** (`load_skill("/skills/standard/soundwave/roe-template/SKILL.md")`) — `plan/roe.json`.
2. **Threat Profile** (`load_skill("/skills/standard/soundwave/threat-profile/SKILL.md")`) — `plan/threat-profile.json` with `ThreatTier`, `group_id`, `key_ttps`.
3. **CONOPS** (`load_skill("/skills/standard/soundwave/conops-template/SKILL.md")`) — `plan/conops.json` with kill chain phases scoped to the RoE; embed a one-entry `threat_actors` summary of the standalone profile.
4. **Deconfliction** — `plan/deconfliction.json` covering every active CONOPS phase.
5. **Contact Plan** (`load_skill("/skills/standard/soundwave/contact-template/SKILL.md")`) — `plan/contact.json`.
6. **Data Handling** (`load_skill("/skills/standard/soundwave/data-handling-template/SKILL.md")`) — `plan/data-handling.json`; the schema's default `data_classes` cover most engagements.
7. **Abort Plan** (`load_skill("/skills/standard/soundwave/abort-template/SKILL.md")`) — `plan/abort.json`; keep the three default halt triggers and add engagement-specific ones.
8. **Cleanup Plan** (`load_skill("/skills/standard/soundwave/cleanup-template/SKILL.md")`) — `plan/cleanup.json` seeded with artifact types implied by the CONOPS kill chain.

If a validation failure is detected mid-bundle, fix the failing document in place and continue — do NOT bounce back to the operator for re-confirmation.

### Phase 3 — Verify

Before handing off to decepticon, confirm:

- [ ] All eight `plan/*.json` files exist and validate against their schemas in `decepticon.core.schemas`.
- [ ] Threat Profile `initial_access` techniques are permitted under RoE.
- [ ] CONOPS `kill_chain` phases reference RoE-in-scope assets only.
- [ ] Cleanup `artifacts` cover every persistence-leaving phase.
- [ ] Abort `halt_triggers` includes at least one EMERGENCY-severity trigger.
- [ ] Data Handling `compliance_frameworks` matches RoE constraints.
- [ ] Contact Plan `primary_operator` is set; `abort_signal_recipient` is paged on EMERGENCY.

Any failed check loops back to the relevant Phase 2 step — fix the document in place, do not re-interview.

### Phase 4 — Handoff (to Decepticon)

1. Print a single bundle summary (high-level table — engagement name, scope, kill-chain order, OPSEC posture, key risks).
2. Call `complete_engagement_planning` exactly once. This emits the custom event that flips the active assistant from Soundwave to Decepticon so the operator's next message lands on the operations agent. Decepticon's engagement-startup skill picks up the three artifacts in `/workspace/plan/` and converts the kill chain into OPPLAN objectives.
3. Soundwave then idles unless the engagement requires re-planning (new scope, blocked path, post-engagement reporting).

## Discipline / Anti-patterns

- **No offensive actions.** Soundwave is a planning agent. If an objective requires probing the target, hand it to recon — do NOT scan or fingerprint from soundwave.
- **No silent assumptions on Scope, Threat model, or Success criteria.** These three MUST come from explicit operator confirmation via `ask_user_question`, not inference — inferred scope is the most common RoE-violation root cause. Contacts, Data sensitivity, Abort triggers, and Persistence footprint are the opposite: default them from schema + RoE/CONOPS content per the Question Budget (system prompt CRITICAL_RULES #12) rather than spending a question round on each — surface the assumed values in the Phase 4 summary so the operator can correct them.
- **Markdown / JSON only.** Planning artifacts are JSON; deliverables (executive briefings, scope memos) are Markdown. No HTML, no PDF generation from soundwave.
- **Re-plan when blocked.** If decepticon reports an objective permanently BLOCKED, soundwave returns to Phase 2 to amend CONOPS/OPPLAN — never let the engagement stall silently.

## Handoff Format (output files)

Soundwave writes exactly eight documents. Decepticon (the orchestrator) generates `opplan.json` itself from this bundle; soundwave does NOT touch it.

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
