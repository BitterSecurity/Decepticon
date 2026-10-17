---
name: fin7
description: "FIN7 (Carbon Spider / Sangria Tempest) adversary-emulation playbook — revenue-targeted spearphishing with phone follow-up, EDR-evasion tradecraft, AD compromise, and big-game-hunting ransomware. Use when emulating a high-end financially-motivated crew that graduated from POS theft to ransomware. Triggers on: 'emulate FIN7', 'Carbanak', 'Carbon Spider', 'Sangria Tempest', 'big game hunting', 'EDR evasion', 'AvNeutralizer'."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "emulate FIN7, Carbanak, Carbon Spider, Sangria Tempest, GOLD NIAGARA, big game hunting ransomware, EDR evasion, AvNeutralizer, spearphishing with phone follow-up, revenue targeting"
  tags: adversary-emulation, fin7, ecrime, ransomware, edr-evasion, phishing
  mitre_attack: T1566.001, T1059.001, T1562.001, T1003.001, T1486, T1567.002
---

# FIN7 — Adversary Emulation Playbook

> Tier-2 financially-motivated crew that evolved from point-of-sale theft to **big-game-hunting
> ransomware** (REvil / DarkSide / Black Basta links). Signature traits: revenue-selected
> targets, convincing business-themed spearphishing reinforced by **phone calls**, and mature
> **EDR-evasion** tooling (AvNeutralizer / AuKill). Authorized red-team emulation only.

## When to emulate FIN7

- The client wants a realistic **ransomware-precursor** test: phishing resilience, EDR
  tamper-resistance, AD attack-path hardening, and backup/recovery readiness.
- Retail, hospitality, financial, software, medical, or other high-revenue targets (see the
  industry → actor map in `../../references/apt-groups.md`).

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "FIN7-like (Carbon Spider / Sangria Tempest)",
  "actor_aliases": ["Carbon Spider", "Sangria Tempest", "GOLD NIAGARA", "ELBRUS", "ITG14"],
  "group_id": "G0046",
  "tier": "tier-2",
  "sophistication": "high",
  "motivation": "financial",
  "initial_access": ["T1566.001", "T1566.002", "T1204.002"],
  "key_ttps": ["T1059.001", "T1547.001", "T1053.005", "T1562.001", "T1003.001", "T1558.003", "T1021.001", "T1486", "T1567.002"],
  "tools": ["Phishing kit + phone follow-up", "Sliver", "NetExec", "Impacket", "EDR-test/BYOVD (authorized)", "canary encryptor"],
  "infrastructure": ["Business-themed lure domains", "Sliver HTTPS C2", "Engagement-owned exfil bucket"],
  "recent_cti_delta": "Since 2020 shifted to big-game-hunting ransomware (REvil/DarkSide; Black Basta TTP overlap); AvNeutralizer/AuKill EDR-killer; targets shortlisted by revenue via Crunchbase/D&B/ZoomInfo, ransom sized to revenue.",
  "confidence": "probable"
}
```

## Kill-chain emulation

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Recon | T1591 | Revenue-based target + staff shortlist (Crunchbase/D&B/ZoomInfo) | recon → `/skills/standard/recon/osint/SKILL.md`, `/skills/standard/osint/SKILL.md` |
| 2 | Initial Access | T1566.001 | Business-themed spearphish attachment + phone follow-up | phisher → `/skills/standard/phisher/SKILL.md` |
| 3 | Execution / C2 | T1204.002 / T1059.001 | Macro → loader → Sliver beacon | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 4 | Persistence | T1547.001 / T1053.005 | Run key + scheduled task | post-exploit → `/skills/standard/post-exploit/privilege-escalation/SKILL.md` |
| 5 | Defense Evasion | T1562.001 | EDR tamper / BYOVD (AvNeutralizer pattern, authorized) | post-exploit → `/skills/standard/post-exploit/privilege-escalation/SKILL.md` (shared `defense-evasion` auto-loaded) |
| 6 | Credential Access | T1003.001 | LSASS dump | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md` |
| 7 | Discovery / AD | T1558.003 | BloodHound the domain; Kerberoast | ad → `/skills/standard/ad/bloodhound-query/SKILL.md`, `/skills/standard/ad/kerberoasting/SKILL.md` |
| 8 | Lateral | T1021.001 / T1570 | RDP/SMB + lateral tool transfer | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md`; `/skills/standard/ad/netexec/SKILL.md` |
| 9 | Priv Esc to DA | T1003.006 | DCSync to domain dominance | ad → `/skills/standard/ad/dcsync/SKILL.md` |
| 10 | Exfiltration | T1567.002 | Stage + exfil to engagement bucket | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |
| 11 | Impact **(CANARY)** | T1486 | Demonstrate ransomware capability on canary only | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |

## CONOPS kill_chain (copy into `conops.json`)

1. `recon` — revenue-based target + staff selection (1).
2. `initial-access` — spearphish attachment + phone follow-up (2).
3. `post-exploit` — loader exec, EDR tamper, LSASS, AD recon/Kerberoast, lateral, DA via DCSync (3-9).
4. `c2` — Sliver (within 3).
5. `exfiltration` — data theft + canary ransomware impact (10-11).

## OPSEC & signature fidelity

- **Phone-reinforced phishing** is the fidelity-defining move — pair the email lure with a
  follow-up call (authorized) to mirror FIN7's hands-on approach.
- **EDR evasion is the headline.** The test is whether the EDR survives a BYOVD/tamper attempt;
  mirror AvNeutralizer's intent (disable endpoint telemetry) using an authorized test driver.
- Dwell days-to-weeks; double extortion (exfil before encrypt).

## RoE / safety gates

- Phishing + phone pretext require explicit authorization + lure-deconfliction
  (`/skills/standard/phisher/lure-deconfliction/SKILL.md`); name in-scope staff.
- **EDR tampering / BYOVD can destabilize endpoints** — authorize it, run on a lab/canary host
  first, and confirm rollback. Add an `EMERGENCY` abort: *"production endpoint protection
  disabled outside the authorized test host, or production data encrypted/exfiltrated."*
- Ransomware impact is **canary-only**.

## Deconfliction

- Record lure domains, the Sliver implant, EDR-tamper test host, and exfil destination in
  `deconfliction.json` + `cleanup.json`.
- Agree an EDR-tamper window with the SOC; the DA-path and ransomware-readiness findings are
  the deliverable, not a surprise outage.

## Fidelity notes (deviations)

- **No real Carbanak/AvNeutralizer binaries** — emulate the loader chain with Sliver and the
  EDR-tamper with an authorized test, or simulate when no lab host is available.
- Impact is a canary marker proving DA + deploy capability; the deliverable distinguishes
  "ransomware-ready (canary proven)" from "production encryption" (never the latter).
