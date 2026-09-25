---
name: roe-template
description: "Rules of Engagement document creation — scope definition, prohibited/permitted actions, testing windows, escalation contacts, incident procedures."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "create RoE, define scope, engagement boundaries, start new engagement"
  tags: roe, scope, engagement, authorization, legal
  upstream_ref: "Soundwave Rules of Engagement template — scope / window / escalation / incident procedures"
---

# Rules of Engagement (RoE) Generator

The RoE is the **legally binding** foundation of every red team engagement. All other documents build on it.

## When to Use

- Starting a new engagement
- User says "create RoE", "define scope", "set boundaries"
- Before any other planning document can be created

## Workflow

### Step 1: Interview the User

Drive each dimension through one `ask_user_question` call (per CRITICAL_RULES #8 — every operator-facing question goes through the tool). **Budget: 6 questions max for RoE** (soundwave.md CRITICAL_RULES → Question Budget). Combine related fields into a single free-form answer instead of asking one field at a time — the picker returns raw text either way, so the agent parses multiple values out of one answer.

**Identity & Scope**
1. Engagement name + client organization (ONE combined free-form question, `allow_other=true` with a sensible guessed pair as the option)
2. Engagement type — single-select: `external` / `internal` / `hybrid` / `assumed-breach` / `physical`
3. Start date / end date / testing window with timezone (free-form, `allow_other=true` — suggest defaults like "Mon-Fri 09:00-18:00 client TZ")
4. In-scope targets AND out-of-scope targets (ONE combined free-form question — "What's in scope, and what's explicitly excluded?", `allow_other=true`)

**Boundaries & Escalation**
5. Special permitted actions — phishing, password spraying, raw-socket scans (multi-select). Additional prohibited actions beyond the schema defaults are NOT a separate question — default silently to the Generation Rules #1 deny-list and only add a custom prohibition if the operator volunteers one unprompted; the summary in Phase 3 surfaces it for correction.
6. Escalation contacts (ONE combined free-form question asking for both slots at once — "Who are your primary contacts? (client lead + red team lead, with name/role/channel for each)", minimum 2) AND authorization reference / contract # folded into the same answer as a trailing field (`allow_other=true`)

If the operator's opening message already answers a dimension (e.g. they paste a CIDR range and say "external pentest"), extract it directly — do not re-ask it as one of the 6.

### Step 2: Generate plan/roe.json

Use the `RoE` schema from `decepticon.core.schemas`. Write to the engagement directory.

See `references/roe-example.json` for a complete example and `../references/schema-quick-reference.md` for all required fields and valid values.

### Step 3: Validate

Run through the checklist in `references/validation-checklist.md` before presenting to user.

## Generation Rules

1. **Always include default prohibited actions** — DoS, unauthorized social engineering, unauthorized physical access, real data exfiltration, production data modification
2. **Scope must be specific** — CIDR notation for IPs, wildcard notation for domains
3. **Testing window must include timezone**
4. **At least 2 escalation contacts** required
5. **Authorization reference must not be empty**

## Output

Write `plan/roe.json` to the engagement directory, then present a human-readable summary to the user for confirmation.
