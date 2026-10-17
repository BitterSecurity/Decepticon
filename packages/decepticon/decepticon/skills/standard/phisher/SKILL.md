---
name: phishing-overview
description: >
  Phishing / social-engineering catalog for the Phisher agent. Use ONLY when the
  engagement RoE authorizes a phishing engagement. Covers pretext design, GoPhish
  campaigns, evilginx2 MFA-bypass proxying, O365 credential/token harvest, lookalike
  domains, and the mandatory blue-team deconfliction handshake.
metadata:
  skillogy:
    version: 1
  subdomain: phishing
  when_to_use: "phishing, social engineering, initial access, gophish, evilginx2, mfa bypass, lure, pretext, o365 oauth, lookalike domain, deconfliction"
  mitre_attack:
    - T1566
    - T1566.001
    - T1566.002
    - T1598
    - T1656
  tags:
    - phishing
    - social-engineering
    - initial-access
    - evilginx
    - gophish
---

# Phishing / Social-Engineering Skill Catalog

**Gating.** Every skill here refuses to execute unless the engagement
RoE authorizes a phishing engagement and the blue-team
deconfliction acknowledgment (`lure-deconfliction`) is recorded for
that campaign's scope and send window. Phishing
real employees without written authorization is a crime — the RoE +
deconfliction ack are the operator's legal coverage.

## Playbooks

| Skill | Use for |
|---|---|
| `/skills/standard/phisher/pretext-engineering/SKILL.md` | Design the pretext + target shortlist from OSINT (LinkedIn / Hunter.io) |
| `/skills/standard/phisher/gophish-campaign/SKILL.md` | GoPhish API: groups, email templates, landing pages, campaign launch + tracking |
| `/skills/standard/phisher/evilginx2-proxy/SKILL.md` | evilginx2 phishlet authoring; capture session cookies past MFA |
| `/skills/standard/phisher/o365-credential-harvest/SKILL.md` | O365 / Entra OAuth device-code + token capture and replay |
| `/skills/standard/phisher/lookalike-domain/SKILL.md` | Punycode / lookalike domain + DNS + TLS provisioning |
| `/skills/standard/phisher/lure-deconfliction/SKILL.md` | MANDATORY blue-team acknowledgment and evidence before sending |

## Infrastructure pattern

```
[Target inbox] -> [NGiNX reverse proxy on attacker domain]
                  ├─ /login   → evilginx2 phishlet (MFA bypass + session capture)
                  └─ /landing → GoPhish (campaign tracking + analytics)
```

- The NGiNX layer is OPSEC: blue-team URL classifiers see one domain;
  internal routing splits phishlet vs landing by path / referer.
- TLS via Let's Encrypt + `acme.sh`; keep ACME challenges off the
  phishlet path.
- SPF / DKIM / DMARC must be correct on the sender domain or modern
  inboxes drop the mail. Soundwave's phishing template walks the
  operator through DNS setup.

## Deconfliction (mandatory)

Every outbound mail carries an engagement header
(`X-Decepticon-Eng: <slug>`) the SOC allow-lists so simulated phishing
is distinguishable from a real attack. The `lure-deconfliction` skill
is a hard gate before the first send — skipping it is a critical RoE
violation.

## Failsafe

On operator stop or SOC request, wind down within 5 minutes: pause the
GoPhish campaign, return 502 on the evilginx2 phishlet, and repoint the
sender domain to a static "this was an authorized test — contact your
security team" page.
