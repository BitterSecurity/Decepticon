---
name: lure-deconfliction
description: Mandatory out-of-band handshake with the blue-team contact BEFORE any phishing campaign sends. Skipping this is a critical RoE violation - the engagement has no legal coverage for unannounced phishing of real employees.
allowed-tools: Bash Read Write
metadata:
  skillogy:
    version: 1
  subdomain: deconfliction
  when_to_use: send phishing campaign, launch lure, evilginx2 lure activation, oauth device-code attack
  tags: deconfliction, blue-team, opsec, legal
  mitre_attack: T1566
---

# Lure Deconfliction

> Before the first email leaves the wire, the customer's blue-team
> contact MUST acknowledge the campaign metadata. This is the legal
> coverage for the engagement - without it, you're a malicious
> phisher.

## Why this is mandatory

A phishing campaign that surprises the blue team produces:

- Helpdesk ticket volume (real users reporting suspicious mail).
- Detection rules firing on lookalike domains, RoE-out-of-scope.
- Escalation to leadership / external counsel.
- Potential SOC overtime that the engagement is supposed to test
  *with awareness*, not *as a surprise*.

It also exposes the engagement to legal risk: phishing without
written authorization is a crime in most jurisdictions. The RoE +
deconfliction ack is the documentation that protects the operator.

## Inputs you need

From `plan/roe.json`:

```json
{
  "escalation_contacts": {
    "blue_team_contact": {
      "name": "...",
      "channel": "signal:@user / email: addr / phone: +...",
      "available": "Mon-Fri 09:00-18:00 KST",
      "ack_method": "signal_message | email_reply | voice_confirm"
    }
  },
  "engagement_id": "RT-2026-0142"
}
```

## Required acknowledgment metadata

The blue-team acknowledgment must cover the campaign metadata:

```
Subject: [DECEPTICON RT-2026-0142] Phishing campaign deconfliction

Engagement: RT-2026-0142
Campaign id: <gophish-campaign-id-or-evilginx-phishlet>
Send window: 2026-05-27 14:00 - 16:00 KST
Target count: <N> users
Target population: <description, e.g. "all @engineering of acme.com">
Pretext class: <internal_it_password_reset | external_invoice | shared_doc | ...>
Lure URL: https://login.acme-portal.example/
Lure domain registration: 2026-05-26 (lookalike of acme.com)
Opt-out URL (in every lure): https://decepticon.example/optout/RT-2026-0142
```

## Required acknowledgment evidence

Before sending, `plan/phisher/deconfliction-log.md` must identify the
contact, configured acknowledgment method, approved send window, and
acknowledgment timestamp / message reference. For `voice_confirm`, the
operator must have completed the call out-of-band and recorded the
confirmation before campaign send-time.

```markdown
## Campaign RT-2026-0142 / gophish-id-12

- Sent: 2026-05-27 12:31:04 UTC via signal_message
- Recipient: <contact>
- Ack received: 2026-05-27 12:48:11 UTC
- Ack message id: <signal-message-id>
- Ack text: "Acknowledged, send window 14:00-16:00 KST approved."
```

Persist as a Finding node in the knowledge graph with
`finding_type: "deconfliction_ack"`, the ack message id, and the
timestamp. This is the audit trail for the engagement deliverable.

## Send authorization constraints

- **Missing acknowledgment**: the campaign DOES NOT send, including
  when the contact is unreachable or the acknowledgment deadline passed.
- **Contact denies the campaign**: the campaign DOES NOT send; retain
  the denial as evidence.
- **Changed campaign scope or send window**: the campaign DOES NOT send
  until the blue-team contact acknowledges the revised metadata.

## ZFP

Two-method evidence for the ack:

1. The actual ack message from the channel.
2. The Finding node with the message id in Neo4j.

Both must exist before the campaign send-step proceeds.
