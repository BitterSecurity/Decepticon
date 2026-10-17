---
name: sandworm
description: "Sandworm (APT44 / Seashell Blizzard, GRU Unit 74455) adversary-emulation playbook — IT→OT intrusion ending in ICS manipulation or destructive impact, executed with living-off-the-land Windows tooling. SAFETY-CRITICAL: destructive and ICS-write steps are canary/lab-only and gated on explicit OT authorization. Use when emulating Sandworm against an ICS/OT or critical-infrastructure estate. Triggers on: 'emulate Sandworm', 'APT44', 'Seashell Blizzard', 'Voodoo Bear', 'ICS attack', 'OT destructive', 'Industroyer', 'NotPetya'."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "emulate Sandworm, APT44, Seashell Blizzard, Voodoo Bear, Telebots, GRU 74455, ICS OT attack, destructive wiper, Industroyer, NotPetya, critical infrastructure disruption, IT to OT pivot"
  tags: adversary-emulation, sandworm, apt44, ics-ot, destructive, critical-infrastructure
  mitre_attack: T1190, T1059.003, T1485, T1561.002, T0831, T0816
---

# Sandworm — Adversary Emulation Playbook

> Tier-3 Russian GRU (Unit 74455) sabotage actor. Sandworm's signature is a patient,
> living-off-the-land IT intrusion that **crosses the IT/OT boundary and ends in physical or
> data destruction** (Ukraine grid via Industroyer; NotPetya pseudo-ransomware wiper).
> **This is the highest-risk emulation in the catalog.** Every ICS-write and destructive step
> is canary/lab-only and gated on explicit operator + OT-engineer authorization.

## When to emulate Sandworm

- Target operates **ICS/OT** or critical infrastructure (energy, water, manufacturing,
  transportation) and the client wants the IT→OT kill chain and destructive-impact resilience
  tested — typically as a tabletop-plus or against a dedicated OT lab segment.
- The client's threat model includes destructive/disruptive nation-state actors (see the
  industry → actor map in `../../references/apt-groups.md`).

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "Sandworm-like (APT44 / Seashell Blizzard)",
  "actor_aliases": ["Voodoo Bear", "Telebots", "IRON VIKING", "Seashell Blizzard", "APT44"],
  "group_id": "G0034",
  "tier": "tier-3",
  "sophistication": "nation-state",
  "motivation": "disruption",
  "initial_access": ["T1190", "T1566.001", "T1195.002", "T1133"],
  "key_ttps": ["T1059.003", "T1003.001", "T1021.002", "T1570", "T1485", "T1561.002", "T1486", "T0831", "T0816", "T0855"],
  "tools": ["NetExec", "Impacket", "Sliver", "pymodbus / python-snap7 (read-first)", "marked canary wiper (lab only)"],
  "infrastructure": ["Compromised edge/VPN foothold", "Engineering-workstation jump host (IT->OT pivot)", "Sliver HTTPS C2"],
  "recent_cti_delta": "2022 Industroyer2 against the Ukrainian power grid; sustained destructive operations vs Ukraine/NATO; LOTL with native Windows tools (vssadmin, wbadmin, bcdedit) before detonation.",
  "confidence": "probable"
}
```

## Kill-chain emulation

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Recon | T1590 / T1595 | Map external surface + IT/OT (Purdue) boundary; fingerprint ICS protocols | recon → `/skills/standard/recon/active-recon/SKILL.md`; route ICS via `/skills/standard/exploit/ics-ot/SKILL.md` |
| 2 | Initial Access | T1190 | Exploit edge/VPN/public-facing app | exploit → `/skills/standard/exploit/web/cve/SKILL.md` |
| 3 | Initial Access (alt) | T1566.001 | Spearphishing attachment to IT staff | phisher → `/skills/standard/phisher/SKILL.md` |
| 4 | Initial Access (alt) | T1195.002 | Trojanized software-update / supply chain (NotPetya pattern) | exploit → `/skills/standard/exploit/supplychain/dep-confusion/SKILL.md` |
| 5 | Credential Access | T1003.001 | LSASS dump on IT hosts (LOTL) | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md` |
| 6 | Lateral (IT) | T1021.002 / T1570 | SMB admin-share spread + lateral tool transfer | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md`; `/skills/standard/ad/netexec/SKILL.md` |
| 7 | IT→OT pivot | T1021 | Reach engineering workstation / OT jump host across the boundary | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md` |
| 8 | ICS Impact **(GATED)** | T0855 / T0831 / T0816 | Read PLC state; **authorized single benign write** to a lab test point | exploit → `/skills/standard/exploit/ics-ot/modbus/SKILL.md` (or `s7comm` / `dnp3` / `bacnet`) |
| 9 | Destructive **(CANARY)** | T1485 / T1561.002 / T1486 | Marked canary wipe / pseudo-ransom on a lab host only | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` (evidence) |
| 10 | C2 | T1071.001 | Sliver HTTPS beacon for the IT-side foothold | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |

## CONOPS kill_chain (copy into `conops.json`)

1. `recon` — external surface + Purdue/IT-OT mapping + ICS fingerprint (row 1).
2. `initial-access` — edge exploit / spearphish / supply chain (2-4).
3. `post-exploit` — LSASS, IT lateral, IT→OT pivot, **gated** ICS read/write, **canary** destruction (5-9).
4. `c2` — Sliver HTTPS (10).
5. `exfiltration` — optional espionage subset (engineering docs, PLC logic) if in scope.

## OPSEC & signature fidelity

- **Quiet until detonation.** Sandworm stages with native Windows tools (cmd, vssadmin,
  wbadmin, bcdedit) and only becomes loud at the impact phase — mirror that: blend during
  staging, then a single controlled, authorized impact event.
- **Mirror the IT→OT pivot.** The fidelity-defining behavior is crossing the boundary via an
  engineering workstation, not the wiper binary.
- ICS reconnaissance is read-only and OPSEC-safe; the noise/risk is entirely in the write.

## RoE / safety gates — READ BEFORE PLANNING

- **ICS writes move physical actuators.** Default to **read-only** enumeration. Any write/
  control-class action requires explicit written OT-write authorization in the RoE and an
  **OT safety engineer on the contact plan**, per `/skills/standard/exploit/ics-ot/SKILL.md`
  ("SAFETY FIRST").
- **Destruction is lab/canary only.** Never run a real wiper or encryptor against production.
  Use a marked canary file set on an isolated lab host.
- `abort.json` MUST carry an `EMERGENCY` trigger (see `/skills/standard/soundwave/abort-template/SKILL.md`):
  *"Tier-3 destructive emulation against critical infrastructure — halt + operator-page +
  1hr cooldown"* — plus *"any ICS write outside the named lab test point."*
- Schedule a maintenance window with physical safety standby before any OT step.

## Deconfliction

- Notify the OT/SOC team and name the exact ICS test device(s) and coil/register addresses in
  `deconfliction.json`; nothing outside that list may be written.
- Tag the canary wiper artifact and Sliver implant with the engagement ID; record the lab
  host inventory in `cleanup.json`.

## Fidelity notes (deviations)

- **No real Industroyer/BlackEnergy/NotPetya samples.** Emulate the *sequence* — IT foothold →
  LOTL lateral → engineering-workstation pivot → ICS command — with pymodbus/python-snap7
  (read-first) and Sliver.
- The "manipulation of control" step is a single authorized benign write to a lab test point
  (e.g., toggle a non-safety-critical coil) **with the OT engineer present**, or fully
  simulated when no lab is available.
- The destructive finale is a canary-only proof that the access *would* allow impact — the
  deliverable distinguishes "impact demonstrated on canary" from "production impact" (never the latter).
