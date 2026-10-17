---
name: lockbit
description: "LockBit / generic RaaS-affiliate adversary-emulation playbook — broker/edge/RDP initial access, beacon, AD compromise to Domain Admin, defense evasion (Defender-disable via GPO, shadow-copy deletion), bulk exfil, then canary double-extortion encryption (Windows + ESXi). Reusable template for any ransomware affiliate (ALPHV, Akira, Black Basta). Triggers on: 'emulate LockBit', 'ransomware affiliate', 'RaaS', 'double extortion', 'StealBit', 'domain-wide ransomware', 'ESXi locker'."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "emulate LockBit, ransomware affiliate, RaaS, double extortion, StealBit, domain wide ransomware, ESXi locker, ALPHV, BlackCat, Akira, Black Basta, shadow copy deletion, GPO Defender disable, backup resilience test"
  tags: adversary-emulation, lockbit, ransomware, raas, double-extortion, ecrime
  mitre_attack: T1190, T1486, T1490, T1562.001, T1567.002, T1021.001
---

# LockBit / RaaS Affiliate — Adversary Emulation Playbook

> Tier-2 ransomware-affiliate kill chain. Modeled on LockBit (most prolific RaaS of 2022-2023,
> disrupted by Operation Cronos in Feb 2024) but written as the **generic double-extortion
> template** — swap `actor_name`/`group_id` to retarget ALPHV/BlackCat, Akira, or Black Basta.
> The flow: cheap initial access (broker/edge/RDP) → AD compromise to Domain Admin → disable
> defenses + delete backups → bulk exfil → encrypt Windows + ESXi. Encryption and recovery
> sabotage are **canary/lab-only**. Authorized red-team emulation only.

## When to emulate a RaaS affiliate

- The client's top concern is **ransomware resilience**: time-to-Domain-Admin, EDR survival,
  backup/shadow-copy protection, ESXi exposure, and exfil detection.
- Any sector — RaaS is opportunistic and cross-industry (see the industry → actor map in
  `../../references/apt-groups.md`).

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "LockBit-like (RaaS affiliate)",
  "actor_aliases": ["LockBit 3.0 / Black", "RaaS affiliate", "(retarget: ALPHV / Akira / Black Basta)"],
  "group_id": "",
  "tier": "tier-2",
  "sophistication": "high",
  "motivation": "financial",
  "initial_access": ["T1190", "T1133", "T1566", "T1078"],
  "key_ttps": ["T1059.001", "T1003.001", "T1558.003", "T1484.001", "T1562.001", "T1490", "T1021.001", "T1567.002", "T1486"],
  "tools": ["Initial-access-broker creds", "Sliver / Cobalt-style beacon", "NetExec", "Impacket / PsExec", "rclone (exfil)", "canary encryptor (lab)"],
  "infrastructure": ["RDP/VPN edge foothold", "GPO-based deployment", "Engagement-owned exfil bucket"],
  "recent_cti_delta": "LockBit: StealBit/MEGA exfil, Windows + VMware ESXi lockers, GPO-pushed deployment; affiliate model persists post-Operation-Cronos. Template applies to ALPHV/BlackCat (Rust, ESXi), Akira, Black Basta.",
  "confidence": "probable"
}
```

## Kill-chain emulation

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Initial Access | T1190 / T1133 / T1078 | Broker creds / edge exploit / RDP-VPN logon | exploit → `/skills/standard/exploit/web/cve/SKILL.md`, `/skills/standard/exploit/web/ato-methodology/SKILL.md` |
| 2 | Initial Access (alt) | T1566 | Phishing loader | phisher → `/skills/standard/phisher/SKILL.md` |
| 3 | C2 | T1071.001 | Beacon for hands-on-keyboard ops | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 4 | Discovery / AD | T1018 / T1083 | Enumerate hosts, shares, AD attack paths | ad → `/skills/standard/ad/bloodhound-query/SKILL.md` |
| 5 | Credential Access | T1003.001 / T1558.003 | LSASS dump; Kerberoast | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md`; `/skills/standard/ad/kerberoasting/SKILL.md` |
| 6 | Priv Esc to DA | T1003.006 | DCSync to domain dominance | ad → `/skills/standard/ad/dcsync/SKILL.md` |
| 7 | Defense Evasion **(GATED)** | T1484.001 / T1562.001 / T1490 | GPO disable Defender; delete shadow copies (lab) | post-exploit → `/skills/standard/post-exploit/privilege-escalation/SKILL.md` (shared `defense-evasion` auto-loaded) |
| 8 | Lateral | T1021.001 / T1570 | PsExec/GPO push across the estate | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md`; `/skills/standard/ad/netexec/SKILL.md` |
| 9 | Exfiltration | T1567.002 | Bulk exfil (StealBit/rclone) of the canary data set | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |
| 10 | Impact **(CANARY)** | T1486 | Deploy canary encryptor (Windows + ESXi) via GPO/PsExec | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |

## CONOPS kill_chain (copy into `conops.json`)

1. `recon` — quick AD/host/share discovery (affiliates often buy access, so recon is light) (4).
2. `initial-access` — broker creds / edge exploit / RDP / phishing (1-2).
3. `post-exploit` — beacon, cred access, DA via DCSync, **gated** defense evasion + backup sabotage, lateral push (3-8).
4. `c2` — Sliver (3).
5. `exfiltration` — bulk exfil then **canary** double-extortion encryption (9-10).

## OPSEC & signature fidelity

- **Fast and loud at the end.** Affiliates exfil first (double extortion), then deploy
  domain-wide in one push — mirror that sequencing so backup/EDR/segmentation are exercised.
- **GPO/PsExec mass deployment** is the fidelity-defining move; the test is whether one
  Domain-Admin compromise really equals estate-wide encryption.
- Hit **ESXi** as well as Windows — RaaS crews target hypervisors for maximum impact.

## RoE / safety gates

- Defense-evasion (Defender-disable via GPO), **shadow-copy/backup deletion**, mass deployment,
  and encryption are **destructive** — lab/canary only, with explicit authorization. Add an
  `EMERGENCY` abort: *"production endpoint protection disabled, production backups/shadow copies
  deleted, real data exfiltrated, or a production host encrypted."*
- Exfil destination is an **engagement-controlled** bucket seeded with **canary** data only.

## Deconfliction

- Record broker/edge foothold, beacon, GPO changes (and their rollback), exfil destination, and
  the canary encryptor hash in `deconfliction.json` + `cleanup.json`.
- Agree the defense-evasion + deployment window with the SOC; the deliverable is the
  time-to-DA, backup-resilience, and exfil-detection findings — not a real outage.

## Fidelity notes (deviations)

- **No real LockBit/ALPHV locker.** The impact step runs a canary encryptor against a
  lab/canary host set; StealBit is replaced by rclone to an engagement bucket.
- This playbook is the reusable RaaS template: to emulate ALPHV/BlackCat (Rust, ESXi-first),
  Akira, or Black Basta, change `actor_name`/`actor_aliases`, leave `group_id` empty (MITRE
  tracks these as software, not groups — see `../../references/apt-groups.md` crosswalk), and
  adjust the initial-access row to the crew's preferred vector.
