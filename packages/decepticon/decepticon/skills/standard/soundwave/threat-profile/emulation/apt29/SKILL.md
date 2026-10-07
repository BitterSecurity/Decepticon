---
name: apt29
description: "APT29 (Cozy Bear / Midnight Blizzard, SVR) adversary-emulation playbook — malware-light cloud-identity espionage: no-MFA password spray, OAuth consent/token abuse, Golden SAML, mailbox collection over residential proxies. Use when emulating APT29 against an M365/Entra/AWS-identity estate. Triggers on: 'emulate APT29', 'Cozy Bear', 'Midnight Blizzard', 'NOBELIUM', 'OAuth abuse', 'cloud identity espionage', 'Golden SAML'."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "emulate APT29, Cozy Bear, Midnight Blizzard, NOBELIUM, The Dukes, SVR, cloud identity espionage, OAuth consent abuse, token theft, Golden SAML, M365 Entra espionage, supply chain"
  tags: adversary-emulation, apt29, cozy-bear, cloud-identity, oauth, espionage
  mitre_attack: T1078.004, T1528, T1550.001, T1098.001, T1606.002, T1114.002, T1071.001
---

# APT29 — Adversary Emulation Playbook

> Tier-3 Russian SVR espionage actor. Emulate **stealth cloud-identity tradecraft**, not
> malware: APT29's modern signature is compromising identity (no-MFA password spray,
> device-code/consent phishing), abusing OAuth applications and tokens for persistence, and
> collecting mail/documents over residential proxies with a near-zero endpoint footprint.
> Authorized red-team emulation only — every action runs under the engagement RoE.

## When to emulate APT29

- Target is a cloud-first / hybrid-identity estate (Microsoft 365 + Entra ID, Okta, AWS IAM
  Identity Center, Google Workspace) and the client wants their **identity blast radius**
  tested the way a top-tier espionage actor would.
- Government, diplomatic, defense, think-tank, technology, or MSP targets (see the
  industry → actor map in `../../references/apt-groups.md`).
- The objective is quiet, long-dwell collection — *not* smash-and-grab.

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "APT29-like (Cozy Bear / Midnight Blizzard)",
  "actor_aliases": ["Cozy Bear", "Midnight Blizzard", "NOBELIUM", "The Dukes", "UNC2452"],
  "group_id": "G0016",
  "tier": "tier-3",
  "sophistication": "nation-state",
  "motivation": "espionage",
  "initial_access": ["T1078.004", "T1110.003", "T1566.002", "T1195.002"],
  "key_ttps": ["T1528", "T1550.001", "T1098.001", "T1098.003", "T1606.002", "T1114.002", "T1071.001", "T1090.003", "T1070.004"],
  "tools": ["AADInternals", "ROADtools", "TokenTactics", "Sliver", "residential proxies"],
  "infrastructure": ["Engagement-owned OAuth apps (consent abuse)", "Residential proxy egress", "Low-and-slow Graph/API calls"],
  "recent_cti_delta": "2024-2026: malware-light cloud tradecraft — malicious OAuth app consent, device-code phishing, password spray against legacy/no-MFA tenants (Microsoft + HPE corporate breaches); SolarWinds-style CI/CD supply-chain remains in repertoire.",
  "confidence": "probable"
}
```

Prune to RoE: if social engineering is out of scope, drop `T1566.002`; if only one cloud is
in scope, drop the others. A phase whose techniques are all pruned drops its kill-chain row.

## Kill-chain emulation

Each row is a candidate OPPLAN objective. The orchestrator's OPPLAN-builder turns surviving
rows into an objective DAG; the **executing agent** loads the named skill.

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Recon | T1593 / T1589.002 | Map tenant, federation, employees, exposed apps/OWA, email format | recon → `/skills/standard/recon/osint/SKILL.md`, `/skills/standard/recon/cloud-recon/SKILL.md` |
| 2 | Initial Access | T1110.003 | Slow password spray against legacy/no-MFA cloud auth | exploit → `/skills/standard/exploit/web/ato-methodology/SKILL.md` |
| 3 | Initial Access (alt) | T1566.002 | Device-code / consent phishing (Teams/email lure) | phisher → `/skills/standard/phisher/SKILL.md` |
| 4 | Initial Access (alt) | T1195.002 | CI/CD or dependency supply-chain foothold | exploit → `/skills/standard/exploit/supplychain/dep-confusion/SKILL.md` |
| 5 | Credential/Token | T1528 / T1550.001 | Steal & replay OAuth app access tokens (skip password+MFA) | exploit → `/skills/standard/exploit/web/oauth/SKILL.md` |
| 6 | Persistence (cloud) | T1098.001 / T1098.003 | Add app credentials + high Graph roles; consent malicious OAuth app | cloud → `/skills/standard/cloud/azure-managed-identity/SKILL.md`, `/skills/standard/cloud/aws-iam-passrole-chain/SKILL.md` |
| 7 | Lateral (identity) | T1606.002 | Golden SAML / federation-trust token forgery | exploit → `/skills/standard/exploit/web/saml/SKILL.md` |
| 8 | Collection | T1114.002 | Mailbox / document collection via Graph (canary mailbox) | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md` |
| 9 | C2 | T1071.001 / T1090.003 | Sliver beacon over HTTPS via residential proxy | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 10 | Exfiltration | T1567.002 | Low-and-slow exfil of scoped collection set | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |

Defense evasion (T1070.004 log/file cleanup, blending with admin activity) is cross-cutting —
the shared `defense-evasion` / `opsec` skills are auto-injected into every operational agent.

## CONOPS kill_chain (copy into `conops.json`)

Collapse the table into the 5-phase `ObjectivePhase` model:

1. `recon` — tenant/identity/federation enumeration (rows 1).
2. `initial-access` — no-MFA password spray + consent phishing + optional supply-chain (2-4).
3. `post-exploit` — token theft, cloud role/cred persistence, Golden SAML, mailbox collection (5-8).
4. `c2` — Sliver over HTTPS via residential proxy (9).
5. `exfiltration` — scoped, throttled collection exfil (10).

## OPSEC & signature fidelity

- **Low and slow.** Spread the password spray over hours, one attempt per account; APT29 is
  patient. No bursty auth.
- **Egress from residential / reputable IP space**, not a datacenter VPS, to defeat IOC-based
  detection (mirror the real actor's residential-proxy obfuscation).
- **Prefer tokens over passwords.** Once an app token is held, stop touching passwords/MFA —
  that is the whole point of the OAuth tradecraft.
- **Blend with admin activity.** Use Graph/PowerShell the way a tenant admin would; avoid
  noisy endpoint tooling.
- **Clean up** consent grants and added credentials at engagement end (feeds `cleanup.json`).

## RoE / safety gates

- **Identity takeover is high blast-radius.** OAuth consent + added cloud roles can affect a
  production tenant globally — require explicit, written cloud-tenant authorization. Prefer a
  **dedicated test tenant** or a sandboxed app registration.
- **Phishing requires social-engineering authorization** and a lure-deconfliction pass
  (`/skills/standard/phisher/lure-deconfliction/SKILL.md`).
- Add an `EMERGENCY` abort trigger to `abort.json`: *"real (non-canary) user mailbox or
  document collected, or consent granted on a non-engagement OAuth app."*

## Deconfliction

- Name every engagement OAuth app `redteam-<engagement-id>-*` and record the app/client IDs
  in `deconfliction.json`.
- Pin a known deconfliction egress IP range + a marker User-Agent string so the identity team
  can separate the exercise from a real Midnight Blizzard sign-in.
- Pre-brief the identity/SOC team that no-MFA spray + consent grants are *expected*.

## Fidelity notes (deviations)

- Emulate consent/token abuse with an **engagement-owned** app registration — never publish a
  real malicious multi-tenant app.
- Use a **canary mailbox/SharePoint site** seeded with marked documents for the collection and
  exfil phases; do not collect real PII or classified mail.
- Sliver replaces APT29's bespoke implants (WINELOADER, etc.); fidelity is the C2 *channel
  shape* (HTTPS, jittered beacon, residential egress), not the binary.
