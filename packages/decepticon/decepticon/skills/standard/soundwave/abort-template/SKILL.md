---
name: abort-template
description: "Abort / crisis plan generator — halt triggers, response actions, AI-aware safety gates (hallucination threshold, destructive-action gate, output validation)."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "create abort plan, emergency halt, crisis procedure, AI safety gate, hallucination guard, destructive action gate"
  tags: abort, halt, crisis, safety, ai-safety, owasp-agentic
  upstream_ref: "Soundwave abort / crisis template — halt triggers, AI-safety gates, hallucination guardrail"
---

# Abort / Crisis Plan Generator

The abort plan defines **when the agent stops**. Without it, the autonomous loop runs through real-incident alerts, scope violations, and production-data exposures as if they were normal operations.

AI-specific gates address the failure modes documented in **OWASP Top 10 for Agentic Applications (2025.12)** — hallucination, prompt injection, unverified destructive actions.

## When to Use

- After CONOPS + Threat Profile + Data Handling are written (the kill chain shapes which triggers are relevant)
- User says "create abort plan", "halt triggers", "emergency stop", "safety gates"

## Workflow

### Step 1: Keep the Three Default Triggers

The `AbortPlan` schema seeds three default halt triggers. Keep all three; do not remove unless the engagement is explicitly read-only:

1. `emergency` — Real-incident alert from blue team / SOC
2. `critical` — Production data observed in collected evidence
3. `critical` — Scope boundary violation detected

### Step 2: Add Engagement-Specific Triggers

Based on the interview answers, add custom `AbortTrigger` entries. Common additions:

| Engagement signal | Severity | Response |
|---|---|---|
| Tier-3+ adversary emulation against critical infrastructure | `emergency` | Halt + operator-page + 1hr cooldown |
| Compliance-bound data class encountered (GDPR / HIPAA) | `critical` | Halt; redact; await operator |
| Network outage or latency spike on target | `warning` | Pause active scanning; resume after 5min |
| LLM cost-per-objective threshold exceeded | `warning` | Pause; ask operator to approve budget |
| C2 beacon lost for > 30 minutes during active phase | `warning` | Pause; re-establish C2 before continuing |

### Step 3: Set AI Safety Gates

- `hallucination_threshold` — default 3 (after 3 unverified-success claims, force evidence-collection mode). Lower (2) for high-stakes engagements; never set 0.
- `destructive_action_gate` — default True. Set False only for read-only / recon-only engagements where no `rm`, `drop`, `format`, `delete-policy` commands will run.
- `output_validation` — default `"verify-evidence-hash"`. Override to `"second-tool-confirm"` for engagements where evidence cannot be hashed (e.g. real-time API responses).

### Step 4: Set Abort Signal Channel + Recovery Procedure

- `abort_signal_channel` — operator's out-of-band channel for forcing halt (HTTP endpoint, file marker, ask_user_question). Leave empty only if Ctrl+C-via-CLI is the only abort path.
- `recovery_procedure` — default text covers the standard flow (snapshot → export evidence → run cleanup → operator approval). Override for engagements with custom recovery semantics.

## Validation Checklist

Before writing `plan/abort.json`:

- [ ] At least one `emergency`-severity trigger exists
- [ ] All custom triggers have non-empty `condition` and `response_action`
- [ ] `hallucination_threshold` ≥ 1
- [ ] `output_validation` matches one of the documented methods
- [ ] `abort_signal_channel` is set OR explicitly marked "CLI Ctrl+C only"

## Anti-patterns

- Removing the EMERGENCY default — guarantees the agent runs through a SOC real-incident alert
- Setting `destructive_action_gate=False` for engagements that include post-exploit or cleanup phases
- Conflating WARNING and CRITICAL — WARNING pauses one step; CRITICAL halts the active objective

## Output

Write to `plan/abort.json` validating against `decepticon.core.schemas.AbortPlan`.

## Document schema (derived from the validator's model)

`plan/abort.json` must validate against `AbortPlan`. Fields not listed here are not part of the model and are ignored by the validator.

| field | type | required | allowed values / nested keys |
| --- | --- | --- | --- |
| `engagement_name` | str | yes |  |
| `halt_triggers` | list[AbortTrigger] | no | each object: `condition`, `severity`, `response_action`, `auto_halt` |
| `abort_signal_channel` | str | no |  |
| `recovery_procedure` | str | no |  |
| `hallucination_threshold` | int | no | >= 1 |
| `destructive_action_gate` | bool | no |  |
| `output_validation` | str | no |  |
| `version` | str | no |  |
| `last_updated` | str | no |  |

`AbortTrigger` object:

| field | type | required | allowed values |
| --- | --- | --- | --- |
| `condition` | str | yes |  |
| `severity` | TriggerSeverity | yes | one of `info`, `warning`, `critical`, `emergency` |
| `response_action` | str | yes |  |
| `auto_halt` | bool | no |  |

