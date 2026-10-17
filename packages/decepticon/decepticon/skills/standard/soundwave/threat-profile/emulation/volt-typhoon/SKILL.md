---
name: emulation-volt-typhoon
description: "Volt Typhoon (Vanguard Panda, PRC) adversary-emulation playbook — edge-device initial access, living-off-the-land-only operations, NTDS/credential theft, long-dwell pre-positioning toward critical infrastructure, multi-hop proxy egress. Use when emulating stealthy LOTL pre-positioning. Triggers on: 'emulate Volt Typhoon', 'Vanguard Panda', 'BRONZE SILHOUETTE', 'living off the land', 'edge device', 'pre-positioning', 'critical infrastructure persistence'."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "emulate Volt Typhoon, Vanguard Panda, BRONZE SILHOUETTE, Insidious Taurus, living off the land, LOTL, edge device exploitation, SOHO router proxy, long dwell, pre-positioning, critical infrastructure persistence, NTDS dump"
  tags: adversary-emulation, volt-typhoon, lotl, critical-infrastructure, edge-devices, espionage
  mitre_attack: T1190, T1078, T1059.001, T1003.003, T1070.001, T1090.003
---

# Volt Typhoon — Adversary Emulation Playbook

> Tier-3 PRC actor specializing in **undetected long-dwell pre-positioning** inside critical
> infrastructure. The defining trait is **living-off-the-land only**: almost no malware,
> built-in OS tooling for everything, credentials harvested from edge devices, log tampering,
> and egress proxied through compromised SOHO routers. The deliverable is proving *quiet,
> persistent access* — not data theft. Authorized red-team emulation only; runs under the RoE.

## When to emulate Volt Typhoon

- The client wants to test **detection of stealthy, malware-free intrusions** and dwell time —
  can the SOC catch an actor that only uses built-ins?
- Communications, energy, water, transportation, or other critical-infrastructure targets
  (see the industry → actor map in `../../references/apt-groups.md`).
- Topical: CISA BOD 26-02 (Feb 2026) prioritizes end-of-support edge-device risk — exactly
  Volt Typhoon's initial-access surface.

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "Volt Typhoon-like (Vanguard Panda)",
  "actor_aliases": ["Vanguard Panda", "BRONZE SILHOUETTE", "Insidious Taurus", "DEV-0391", "Voltzite"],
  "group_id": "G1017",
  "tier": "tier-3",
  "sophistication": "nation-state",
  "motivation": "espionage",
  "initial_access": ["T1190", "T1133", "T1078"],
  "key_ttps": ["T1059.001", "T1059.003", "T1003.003", "T1552.001", "T1018", "T1021.001", "T1070.001", "T1090.003"],
  "tools": ["Native LOLBins (netsh, wmic, ntdsutil, vssadmin, reg)", "Impacket", "FRP / Fast Reverse Proxy", "NetExec (low-noise modules)"],
  "infrastructure": ["Compromised edge appliance foothold", "Multi-hop proxy via engagement SOHO hop", "No persistent malware - valid accounts only"],
  "recent_cti_delta": "CISA AA24-038A: multi-year dwell in US critical infrastructure; KV-botnet of compromised SOHO routers for proxying; pre-positioning toward OT; edge-device (Fortinet/Ivanti/Citrix/router) initial access.",
  "confidence": "probable"
}
```

## Kill-chain emulation

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Recon | T1590 / T1595 | Identify internet-facing edge appliances (firewall/VPN/router) | recon → `/skills/standard/recon/passive-recon/SKILL.md`, `/skills/standard/recon/active-recon/SKILL.md` |
| 2 | Initial Access | T1190 | Exploit n-day/0-day on the edge appliance | exploit → `/skills/standard/exploit/web/cve/SKILL.md` |
| 3 | Initial Access (alt) | T1078 / T1133 | Reuse admin creds pulled from the device config | exploit → `/skills/standard/exploit/web/ato-methodology/SKILL.md` |
| 4 | C2 / Proxy | T1090.003 | Multi-hop proxy egress (FRP) through an engagement SOHO hop | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 5 | Discovery (LOTL) | T1018 / T1016 | Map the network with built-ins only (no scanners) | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md`; `/skills/standard/ad/netexec/SKILL.md` (low-noise) |
| 6 | Credential Access | T1003.003 / T1552.001 | NTDS dump via ntdsutil/vssadmin; creds from files/configs | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md`; `/skills/standard/ad/dcsync/SKILL.md` |
| 7 | Lateral | T1021.001 | RDP with valid accounts; no exploit tooling | post-exploit → `/skills/standard/post-exploit/lateral-movement/SKILL.md` |
| 8 | Pre-position | T1078 | Map OT/critical systems, document footholds — **no impact** | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |

Defense evasion is the headline behavior: T1070.001 (clear Windows event logs) and strict
LOTL are enforced by the shared `defense-evasion` / `opsec` skills auto-injected into every
operational agent.

## CONOPS kill_chain (copy into `conops.json`)

1. `recon` — edge-appliance + network identification (1).
2. `initial-access` — edge exploit / device-cred reuse (2-3).
3. `post-exploit` — LOTL discovery, NTDS/cred theft, RDP lateral, pre-positioning + log cleanup (5-8).
4. `c2` — FRP/Sliver multi-hop via SOHO proxy hop (4).
5. `exfiltration` — minimal/none; pre-positioning only. Add an espionage subset only if explicitly in scope.

## OPSEC & signature fidelity

- **LOTL or it isn't Volt Typhoon.** Built-ins only: `netsh`, `wmic`, `ntdsutil`, `vssadmin`,
  `reg`, `dnscmd`, PowerShell. No port scanners, no Cobalt/Metasploit, no dropped binaries.
- **Proxy egress** through a residential/SOHO hop, never a datacenter IP.
- **Clear logs** after each action; the whole exercise tests whether the SOC can detect a
  malware-free, log-minimized intruder.
- **Maximum patience.** Pace actions over days; success metric is dwell time before detection.

## RoE / safety gates

- The objective is **access, not impact** — default to no destructive or OT-write action.
- Edge-device exploitation can **brick appliances**; require device-write authorization and
  confirm a config backup exists before exploiting.
- Add an `EMERGENCY` abort trigger: *"any action against OT/safety systems"* — pre-positioning
  stops at the IT/OT boundary unless OT is explicitly authorized (then see the `sandworm`
  playbook's ICS gates).

## Deconfliction

- Record the engagement proxy hop(s), edge-device foothold, and NTDS-dump artifact in
  `deconfliction.json` + `cleanup.json`.
- Brief the network team that valid-account RDP + NTDS access are expected; the value is
  measuring time-to-detect, so keep the deconfliction list tight and SOC-blind where lawful.

## Fidelity notes (deviations)

- **No real SOHO botnet.** Emulate multi-hop proxying with a single engagement-owned hop +
  FRP/Sliver.
- LOTL tradecraft is reproduced faithfully and safely — this is the rare actor whose real
  TTPs are also the safe-to-emulate ones.
- Pre-positioning ends at documented footholds; the deliverable proves dwell + reachability,
  never live disruption.
