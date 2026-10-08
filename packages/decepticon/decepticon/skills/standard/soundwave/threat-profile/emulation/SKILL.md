---
name: emulation-overview
description: "Adversary-emulation playbook catalog — per-actor kill chains that turn an APT/eCrime threat profile into Decepticon CONOPS phases + OPPLAN objectives. Routing skill: pick the actor, seed plan/threat-profile.json, then map each kill-chain phase to the operational skill the executing agent runs. Triggers on: 'emulate', 'adversary emulation', 'APT playbook', 'threat actor playbook', 'emulation plan', 'attack flow'."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "emulate, adversary emulation, APT playbook, threat actor playbook, emulation plan, attack flow, kill chain for actor, which TTPs, how would APT29/Sandworm/Lazarus/Scattered Spider attack"
  tags: adversary-emulation, apt, ecrime, mitre-attack, kill-chain, planning
  upstream_ref: "Soundwave adversary-emulation playbook catalog — per-actor kill chains for CONOPS / OPPLAN generation"
---

# Adversary Emulation Playbook Catalog

This is a **planning** routing skill for Soundwave. Each leaf playbook below converts
a named threat actor into a concrete, RoE-bounded kill chain: a `ThreatProfile` seed, an
ordered CONOPS `kill_chain`, and a phase→technique→skill map that the orchestrator turns
into OPPLAN objectives.

> **These playbooks reference operational skills (`/skills/standard/ad/...`, `/skills/standard/cloud/...`, etc.)
> that the EXECUTING agents load — not Soundwave.** Soundwave is a planning agent; its
> `load_skill` allowlist is `/skills/standard/soundwave/`. The skill paths in each
> playbook tell the orchestrator which agent + skill each objective maps to. Soundwave
> only reads them to author `plan/threat-profile.json` and the `conops.json` kill chain.

## Playbooks

| Actor | Playbook | Tier | Profile | Skills it exercises |
|---|---|---|---|---|
| **APT29** (Cozy Bear / Midnight Blizzard) | `emulation/apt29/SKILL.md` | tier-3 | Cloud-identity espionage, OAuth abuse, supply chain | recon, cloud, web (oauth/saml), post-exploit |
| **Sandworm** (APT44 / Seashell Blizzard) | `emulation/sandworm/SKILL.md` | tier-3 | ICS/OT disruption, destructive ops, LOTL | recon, exploit/cve, ics-ot, post-exploit |
| **Scattered Spider** (UNC3944 / Octo Tempest) | `emulation/scattered-spider/SKILL.md` | tier-2 | Help-desk social engineering → cloud/SaaS → ransomware | phish, cloud, ad, post-exploit |
| **Volt Typhoon** (Vanguard Panda) | `emulation/volt-typhoon/SKILL.md` | tier-3 | Edge-device access, LOTL, long-dwell pre-positioning | recon, exploit/cve, ad, post-exploit |
| **Lazarus** (Hidden Cobra) | `emulation/lazarus/SKILL.md` | tier-3 | Financial/crypto/DeFi theft, supply-chain, social | osint, phish, contracts, web, post-exploit |
| **FIN7** (Carbon Spider / Sangria Tempest) | `emulation/fin7/SKILL.md` | tier-2 | Spearphishing → big-game-hunting ransomware | phish, ad, post-exploit, exploit |
| **LockBit / RaaS affiliate** | `emulation/lockbit/SKILL.md` | tier-2 | Generic ransomware affiliate kill chain | recon, exploit/cve, ad, post-exploit |

Use the loaded actor playbook and current evidence for attribution and TTP details. Skillogy does not expose sibling reference files; do not assume those cards were read.

## How to use a playbook (Soundwave Phase 2)

1. **Pick the actor** from the operator's intake answer and current evidence. One dominant actor per engagement.
2. **Load the leaf**: `load_skill("/skills/standard/soundwave/threat-profile/emulation/<actor>/SKILL.md")`.
3. **Copy the `ThreatProfile` seed** into `plan/threat-profile.json`, then prune any
   `key_ttps` / `initial_access` techniques the RoE forbids (Step 3 of the `threat-profile`
   skill). A pruned technique whose whole phase is now empty drops that kill-chain row.
4. **Lift the kill chain** into `conops.json` → `kill_chain` (one `KillChainPhase` per
   surviving phase), and embed the one-entry `threat_actors` summary.
5. **Carry the safety gates** from the playbook's *RoE / safety gates* section into
   `abort.json` (destructive / ICS / identity-takeover actors need at least one
   `EMERGENCY` trigger) and the deconfliction identifiers into `deconfliction.json`.
6. Hand off. The orchestrator's OPPLAN-builder reads `threat-profile.json` + `conops.json`
   and builds the objective DAG; each objective's executing agent loads the skill named
   in that kill-chain row.

## Playbook anatomy (every leaf has these)

- **ThreatProfile seed** — a valid `decepticon.core.schemas.ThreatProfile` JSON (drop your
  `engagement_name`).
- **Kill-chain emulation table** — `# | phase | MITRE | emulated action | executing agent → skill`.
- **CONOPS kill_chain** — the phase order to copy into `conops.json`.
- **OPSEC & signature fidelity** — what to mirror so the emulation *reads* like the actor.
- **RoE / safety gates** — actor-specific authorizations and abort triggers.
- **Deconfliction** — identifiers so blue team can separate the exercise from a real intrusion.
- **Fidelity notes (deviations)** — where the emulation intentionally diverges (e.g. canary
  data instead of real destruction) and why.

## Discipline

- **Emulate behavior, not malware.** Decepticon reproduces an actor's *TTPs and sequencing*
  with its own tooling (Sliver, NetExec, certipy, etc.) — it does not run the actor's real
  implants. Fidelity comes from technique order + OPSEC posture, not from sample reuse.
- **RoE wins every tie.** If the actor's signature move (spearphishing, deauth, ICS write,
  encryption-for-impact) isn't authorized, drop it — never "emulate harder" past scope.
- **One actor per profile.** Multi-actor scenarios pick the dominant emulation target;
  note the secondary in `recent_cti_delta`.
