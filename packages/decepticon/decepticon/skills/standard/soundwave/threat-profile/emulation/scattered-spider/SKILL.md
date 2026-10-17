---
name: emulation-scattered-spider
description: "Scattered Spider (UNC3944 / Octo Tempest) adversary-emulation playbook — help-desk vishing → MFA takeover → cloud/SaaS/identity privilege expansion → RMM persistence → data-theft extortion. Use when emulating identity-first social-engineering eCrime against a help-desk/IdP estate. Triggers on: 'emulate Scattered Spider', 'UNC3944', 'Octo Tempest', '0ktapus', 'help desk social engineering', 'MFA fatigue', 'SIM swap', 'identity attack'."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "emulate Scattered Spider, UNC3944, Octo Tempest, Muddled Libra, Roasted 0ktapus, help desk social engineering, vishing, MFA fatigue, SIM swap, Okta abuse, cloud identity takeover, RMM persistence, ransomware affiliate"
  tags: adversary-emulation, scattered-spider, unc3944, social-engineering, identity, ecrime
  mitre_attack: T1656, T1598, T1621, T1078.004, T1219, T1486
---

# Scattered Spider — Adversary Emulation Playbook

> Tier-2 native-English-speaking eCrime collective. Their edge is **social engineering**:
> impersonating employees to the IT help desk to drive password resets and MFA transfers,
> then pivoting fast through the identity provider (Okta / Entra ID) into cloud and SaaS,
> deploying legitimate RMM for persistence, and finishing with data-theft extortion /
> ransomware. Authorized red-team emulation only — every action runs under the engagement RoE.

## When to emulate Scattered Spider

- The client wants their **human + identity perimeter** tested: help-desk verification
  process, IdP admin controls, MFA resilience, RMM allow-listing.
- Telecom, BPO/CRM, technology, gaming, hospitality, retail, financial, or MSP targets.
- The scenario is fast and brazen extortion — the opposite of APT29's low-and-slow.

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "Scattered Spider-like (UNC3944 / Octo Tempest)",
  "actor_aliases": ["UNC3944", "Octo Tempest", "Muddled Libra", "Roasted 0ktapus", "Scatter Swine"],
  "group_id": "G1015",
  "tier": "tier-2",
  "sophistication": "high",
  "motivation": "financial",
  "initial_access": ["T1656", "T1598", "T1566.004", "T1078.004"],
  "key_ttps": ["T1621", "T1556.006", "T1098.005", "T1078.004", "T1219", "T1213", "T1486", "T1657"],
  "tools": ["Authorized test persona (vishing)", "AnyDesk / ConnectWise (engagement-owned)", "Sliver", "NetExec", "Impacket"],
  "infrastructure": ["Spoofed/marked caller-ID test line", "Look-alike SSO phishing page", "Attacker-registered MFA device (engagement-controlled)", "Rogue VM in victim vSphere/Azure"],
  "recent_cti_delta": "2023-2025: help-desk impersonation + MFA fatigue + SIM swap; registers its own MFA, creates rogue VMs in vSphere/Azure; pivoted to impersonating employees against third-party IT; ransomware affiliate ALPHV -> RansomHub -> DragonForce.",
  "confidence": "probable"
}
```

## Kill-chain emulation

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Recon (PII) | T1589 | Harvest employee names, roles, phone numbers, help-desk reset process | recon → `/skills/standard/recon/osint/SKILL.md`, `/skills/standard/osint/SKILL.md` |
| 2 | Initial Access | T1656 / T1598 / T1566.004 | Vish the help desk as a target employee → password reset + MFA transfer | phisher → `/skills/standard/phisher/SKILL.md` |
| 3 | MFA bypass | T1621 / T1556.006 | MFA-fatigue push OR register attacker-controlled MFA device | phisher/exploit → `/skills/standard/phisher/SKILL.md`, `/skills/standard/exploit/web/oauth/SKILL.md` |
| 4 | Cloud foothold | T1078.004 | Sign in to Okta/Entra/AWS as the reset account | cloud → `/skills/standard/cloud/aws-iam-enum/SKILL.md`, `/skills/standard/cloud/azure-managed-identity/SKILL.md` |
| 5 | Scope expansion | T1098.003 / T1098.005 | Self-assign apps in Okta; add roles/credentials; passrole chains | cloud → `/skills/standard/cloud/aws-iam-passrole-chain/SKILL.md` |
| 6 | Persistence | T1219 | Deploy engagement-owned RMM (AnyDesk/ConnectWise); create rogue VM | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md`, `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 7 | On-prem pivot (hybrid) | T1558.003 / T1003 | BloodHound the AD; Kerberoast; dump creds | ad → `/skills/standard/ad/bloodhound-query/SKILL.md`, `/skills/standard/ad/kerberoasting/SKILL.md` |
| 8 | Collection | T1213 | Mine SharePoint/Confluence/Slack for secrets + PII (canary) | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md` |
| 9 | Exfiltration | T1567.002 | Stage + exfil the scoped/canary data set | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |
| 10 | Impact **(CANARY)** | T1486 / T1657 | Demonstrate ransomware/financial-theft capability on canary only | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |

## CONOPS kill_chain (copy into `conops.json`)

1. `recon` — OSINT/PII + help-desk reset-process recon (1).
2. `initial-access` — vishing → password reset + MFA takeover → cloud login (2-4).
3. `post-exploit` — Okta/IAM scope expansion, RMM persistence, optional on-prem AD pivot, SaaS collection (5-8).
4. `c2` — Sliver / RMM session (within row 6).
5. `exfiltration` — data theft → extortion demonstration on canary (9-10).

## OPSEC & signature fidelity

- **Brazen, not stealthy.** Scattered Spider moves in minutes-to-hours and leans on
  *legitimate* identity + RMM, so the detection signature is behavioral: a help-desk reset
  anomaly, a new MFA device, an unexpected RMM install, a new VM. Mirror that pattern so the
  blue team's detections are exercised.
- **Live off the victim's own tooling** (vSphere, Azure portal) to create infrastructure.
- Persistence = own MFA token + RMM, not malware implants.

## RoE / safety gates

- **Social engineering of real staff/help desk requires explicit, written authorization**
  plus HR/legal sign-off and a lure-deconfliction pass
  (`/skills/standard/phisher/lure-deconfliction/SKILL.md`). Name exactly which staff/roles are
  in scope.
- **No real SIM swaps** against personal numbers (illegal). Simulate the SIM-swap effect with
  an authorized engagement-controlled test number only.
- Ransomware/financial-theft impact is **canary-only**; add an `EMERGENCY` abort trigger:
  *"real customer data exfiltrated, real funds moved, or production system encrypted."*

## Deconfliction

- Brief a single deconfliction POC (NOT the help desk under test) and use a marked test
  persona + caller-ID so the activity can be separated from a real Octo Tempest call post-hoc.
- Record the attacker-registered MFA device, RMM install IDs, and any rogue VM in
  `deconfliction.json` + `cleanup.json`.
- Time-box vishing windows; never call outside the agreed hours.

## Fidelity notes (deviations)

- The vishing is performed by an **authorized tester** against a **scoped** help desk; the
  point is to measure the reset/verification process, not to deceive arbitrary staff.
- MFA registration uses an **engagement-controlled** device; RMM is an **engagement-owned**
  AnyDesk/ConnectWise tenant.
- Impact is a canary marker proving the access would allow encryption/theft — never a real locker.
