---
name: threat-profile
description: "Threat actor profiling for adversary emulation — APT group research, sophistication tiers, MITRE ATT&CK mapping, initial access vectors, custom archetypes."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "threat modeling, adversary emulation, APT simulation, threat actor selection, who should we emulate, what kind of attacker"
  tags: threat-modeling, apt, adversary-emulation, mitre-attack
  upstream_ref: "Soundwave engagement-planning template — threat actor profiling for adversary emulation"
---

# Threat Profile Builder

Threat profiling defines **who** the red team is emulating. Without a clear profile, the engagement devolves into arbitrary tool usage instead of realistic adversary simulation.

## When to Use

- During CONOPS creation when selecting a threat actor
- User asks about threat actors, APT groups, or adversary emulation
- Need to map engagement scope to realistic attacker behaviors

## Workflow

### Step 1: Determine Tier

Ask the user which tier fits. If they're unsure, recommend based on engagement type:

| Tier | Actor Type | Sophistication | Best For |
|------|-----------|---------------|----------|
| 1 | Opportunistic attacker | Low | Vulnerability assessment, external scan |
| 2 | Targeted cybercriminal | Medium | Penetration test, focused engagement |
| 3 | APT / nation-state | High | Full red team, advanced simulation |
| 4 | Insider threat | Varies | Internal assessment, assumed breach |

### Step 2: Build the Profile

Gather or derive these fields from the operator's context and current evidence. For a **named actor**, optionally load the matching emulation playbook (`emulation/<actor>/SKILL.md`, indexed by `emulation/SKILL.md`) when that specialist detail is useful. Skillogy returns skill bodies, not sibling reference files:

1. **Name/Alias** — Known group or custom archetype
2. **Sophistication** — low / medium / high / nation-state
3. **Motivation** — financial, espionage, disruption, hacktivism
4. **Initial Access** — MITRE technique IDs for how they get in
5. **Key TTPs** — Top 5-10 MITRE ATT&CK techniques
6. **Tools & Infrastructure** — Realistic toolset for this actor

### Step 3: Validate Against RoE

The profile must be consistent with what the RoE allows. There's no point emulating spearphishing if social engineering isn't authorized.

| RoE Constraint | Profile Implication |
|---|---|
| External only, no social engineering | Focus on T1190, T1595, T1133 |
| Phishing authorized | Include T1566, T1598 |
| Internal assumed breach | Start from T1078 (Valid Accounts) |
| Full red team | Full kill chain TTPs |

### Step 4: Output

Generate **two** payloads:

**(a) Standalone `plan/threat-profile.json`** — full `ThreatProfile` schema:

```json
{
  "engagement_name": "...",
  "actor_name": "APT29-like (Cozy Bear)",
  "actor_aliases": ["Cozy Bear", "The Dukes"],
  "group_id": "G0050",
  "tier": "tier-3",
  "sophistication": "nation-state",
  "motivation": "espionage",
  "initial_access": ["T1195.002", "T1566.001", "T1078"],
  "key_ttps": ["T1059.001", "T1053.005", "T1071.001", "T1048.003", "T1550.001"],
  "tools": ["Cobalt Strike", "Mimikatz", "WMI Event Subscription"],
  "infrastructure": ["Compromised SaaS", "Domain fronting via CDN"],
  "recent_cti_delta": "Q1 2026 reports show shift toward OAuth abuse and CI/CD supply-chain",
  "confidence": "probable"
}
```

`tier` is the StrEnum value: `"tier-1"` (opportunistic), `"tier-2"` (targeted cybercrime), `"tier-3"` (APT / nation-state), `"tier-4"` (insider). Map to `sophistication` informally — `tier-3` ↔ "nation-state", `tier-2` ↔ "high", `tier-1` ↔ "low/medium".

**(b) Embedded summary** — one-entry `threat_actors` list inside `conops.json` for backward-compat (the legacy `ThreatActor` shape: `name` + `sophistication` + `motivation` + `initial_access` + `ttps`). Skip `tier`/`group_id`/`tools`/`infrastructure` here — those live only in the standalone profile.

### Step 5: Named-actor kill chain (optional but recommended)

When the operator picked a known actor, don't hand-build the TTP sequence — load the
emulation catalog and copy the per-actor plan:

```text
load_skill("/skills/standard/soundwave/threat-profile/emulation/SKILL.md")   # routing table
load_skill("/skills/standard/soundwave/threat-profile/emulation/apt29/SKILL.md")  # e.g. APT29
```

Each playbook gives you (a) the `ThreatProfile` seed for `plan/threat-profile.json`, (b) the
ordered `kill_chain` to copy into `conops.json` (mapped to the 5 `ObjectivePhase` buckets),
and (c) the actor-specific RoE/safety gates to carry into `abort.json` + `deconfliction.json`.
Prune any technique the RoE forbids (Step 3) before writing. Available: `apt29`, `sandworm`,
`scattered-spider`, `volt-typhoon`, `lazarus`, `fin7`, `lockbit`.
