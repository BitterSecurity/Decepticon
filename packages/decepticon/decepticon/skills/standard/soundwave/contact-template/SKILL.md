---
name: contact-template
description: "Contact / communications plan generator — primary operator, escalation chain, abort signal recipient, external SOC endpoint, blackout windows."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "create contact plan, communications plan, escalation chain, operator contacts, SOC notification, blackout window"
  tags: contact, communications, escalation, soc, deconfliction, blackout
  upstream_ref: "Soundwave contact / communications plan template — operator, escalation, SOC endpoint, blackouts"
---

# Contact / Communications Plan Generator

Slim version of the traditional white-cell/blue-cell/red-cell matrix tailored for autonomous-AI engagements. The agent itself is the "red cell"; only humans staff the white / blue cells. Full multi-cell rosters belong in an external comms doc, not here.

## When to Use

- After the system-provided RoE is available (its escalation contacts may seed the primary operator)
- User says "create contact plan", "communications", "who do we notify", "escalation chain"

## Workflow

### Step 1: Identify Primary Operator

This is the single human responsible for the engagement on the operator side. Resolve from the RoE `escalation_contacts`:

- If exactly one contact has the role "engagement lead" or "primary operator" — use that
- Otherwise ask via `ask_user_question` who the primary is

Fields: `name`, `role`, `channel` (resolvable — Signal, email, PagerDuty service-key, Slack channel), `availability`

### Step 2: Build Escalation Chain

Ordered list of fallbacks. The agent walks this chain when the primary is unreachable past the abort plan's response window (default 15 minutes).

Typical chain:
1. Primary operator
2. Engagement owner (lead pentester / red team manager)
3. Client SOC lead (only contacted on deconfliction events)

### Step 3: Abort Signal Recipient

The contact paged on EMERGENCY-severity abort triggers. Usually = primary operator but can differ (e.g., engagements with a dedicated on-call rotation).

Optional — leave `None` only if the engagement explicitly opts out of EMERGENCY paging (rare).

### Step 4: External SOC Endpoint (Optional)

If the client has an integration endpoint for deconfliction (HTTP webhook, Splunk HEC token, internal ticketing API), record the URL here. The agent POSTs before active scanning begins so the SOC can pre-allowlist red-team activity.

Empty = no external integration; deconfliction happens via the primary operator only.

### Step 5: Blackout Windows

ISO 8601 datetime ranges during which the agent must not run **active** operations (passive recon — DNS, public WHOIS — is usually OK). Format: `"<start>/<duration-or-end>"`, e.g.:

- `"2026-05-22T22:00:00+09:00/PT8H"` — 8 hours starting May 22 22:00 KST
- `"2026-05-25T00:00:00Z/2026-05-26T00:00:00Z"` — full day UTC

Common blackouts:
- Production change-management freeze windows
- Client business-critical events (earnings, product launch)
- Holidays / weekends (if RoE forbids weekend testing)

## Validation Checklist

Before writing `plan/contact.json`:

- [ ] `primary_operator` is set with a resolvable channel
- [ ] Escalation chain is ordered (no duplicates, primary listed only once if at all)
- [ ] `abort_signal_recipient` is set OR `None` with an explicit reason
- [ ] Blackout windows are valid ISO 8601 ranges

## Anti-patterns

- Listing the primary operator twice (once as `primary_operator`, once in chain) — redundant
- Using non-resolvable channels ("call John") — the agent can't dial; require a concrete `tel:` / `signal:` / `mailto:` style
- Blackout windows without timezone — agent will misinterpret in UTC

## Output

Write to `plan/contact.json` validating against `decepticon.core.schemas.ContactPlan`.
