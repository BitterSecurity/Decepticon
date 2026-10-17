---
name: adversary-emulation
description: "Actor-profile taxonomy, ATT&CK technique fidelity, technical prerequisites and detection interpretation for authorized adversary emulation."
allowed-tools: Bash Read Write
metadata:
  skillogy:
    version: 1
  subdomain: adversary-emulation
  when_to_use: "adversary emulation, emulate APT, threat actor, threat-informed, mimic adversary, APT29 APT28 APT33 APT34 APT41 Lazarus FIN7 Sandworm Volt Typhoon Scattered Spider Salt Typhoon Turla MuddyWater APT36 APT37 Mustang Panda Dark Caracal Patchwork Pink Sandstorm APT10 Kimsuky SideWinder, purple team, ATT&CK evaluation, TTP replay"
  tags: adversary-emulation, threat-intel, mitre-attack, apt, purple-team, ttp, threat-informed
  mitre_attack: T1566, T1078, T1059, T1071, T1021, T1486
---

# Adversary Emulation — index & methodology

**Adversary emulation** reproduces a *specific, named* threat actor's tactics,
techniques and procedures (TTPs) — drawn from real, attributed intelligence —
to test whether the target's people, process, and tooling detect and respond
the way they should. It is distinct from generic penetration testing
(opportunistic) and from *simulation* (abstract/random): emulation is
**threat-informed** — every action traces to something a real group has been
documented doing, mapped to [MITRE ATT&CK](https://attack.mitre.org/).

> **Authorized use only.** Emulate an actor's TTPs **only** within the engagement's
> Rules of Engagement and approved scope. Destructive techniques (Impact tactic:
> ransomware, wipers, ICS manipulation) are emulated as *non-destructive proofs*
> (e.g., a benign canary file, a dry-run) unless the RoE explicitly authorizes
> otherwise. The goal is to measure detection, not to cause damage.

## When to use this

- The operator (or `roe.json` threat profile) names a specific actor to emulate,
  or names a sector/region whose dominant threat is a known group.
- A purple-team / ATT&CK-evaluation engagement: run an actor's TTP chain while the
  blue cell measures detection (see `kill-chain-analysis` and the `blue_cell`).
- You want a *realistic, defensible* attack plan instead of an ad-hoc one.

## Profile interpretation

A profile groups attribution, targeting, dated campaigns, ATT&CK technique IDs,
tooling, implementation examples and detection notes. Industry, geography and
crown-jewel similarity suggest candidate profiles; they do not establish the
operator's chosen mission or authorize the actor's full campaign.

Intersect each selected technique with approved RoE. Out-of-scope techniques
remain forbidden; destructive effects require a separately approved simulation
or explicit authorization. Forbidden-destination checks still apply at tool time.
Technical outputs of one technique may establish another's prerequisites, but
the tactic taxonomy is not a mandatory execution sequence. Detection evidence
distinguishes observed, blocked, missed and untested behavior. The workflow owns
profile selection, objective sequencing, measurement collection and reporting.

This complements `soundwave/threat-profile` (which picks the actor at planning time)
and `kill-chain-analysis` (which scores detection across the chain).

## Actor catalog

| Profile (`load_skill <slug>`) | Aliases | Attribution | Motivation | Notable for |
| --- | --- | --- | --- | --- |
| `apt29-cozy-bear` | Midnight Blizzard, NOBELIUM, The Dukes | Russia (SVR) | Espionage | Stealthy cloud/identity intrusions; SolarWinds supply chain |
| `apt28-fancy-bear` | Forest Blizzard, Sofacy, STRONTIUM | Russia (GRU) | Espionage / influence | Credential phishing, election & defense targeting |
| `apt33-elfin` | Peach Sandstorm, HOLMIUM | Iran | Espionage (destructive links) | Aerospace & energy, Gulf-region targeting |
| `apt34-oilrig` | Helix Kitten, Hazel Sandstorm | Iran | Espionage | DNS-tunneling C2, Middle-East supply-chain access |
| `apt41-double-dragon` | Wicked Panda, BARIUM | China | Espionage **and** financial | Software supply-chain compromise; dual-use ops |
| `lazarus-group` | Hidden Cobra, Diamond Sleet | North Korea | Financial + destructive | Bank/crypto heists, WannaCry, supply chain |
| `fin7-carbanak` | Carbon Spider, Sangria Tempest | Financially motivated | Financial | POS/retail intrusions, Carbanak, ransomware affiliate |
| `sandworm-team` | Voodoo Bear, Seashell Blizzard | Russia (GRU) | Destructive / disruptive | NotPetya, Ukraine power-grid attacks, ICS |
| `volt-typhoon` | Vanguard Panda, Insidious Taurus | China | Pre-positioning | Living-off-the-land in US critical infrastructure |
| `scattered-spider` | UNC3944, Octo Tempest, Muddled Libra | Financially motivated | Financial / extortion | Help-desk social engineering, SIM-swap, MFA fatigue |
| `salt-typhoon` | Earth Estries, GhostEmperor, FamousSparrow | China | Espionage / pre-positioning | Edge-device exploitation, telecom targeting, DEMODEX rootkit |
| `turla` | Venomous Bear, Secret Blizzard, KRYPTON | Russia (FSB) | Espionage | Snake rootkit, satellite C2, hijacking other APTs' infra |
| `muddywater` | Mercury, Mango Sandstorm, Static Kitten | Iran (MOIS) | Espionage | PowerShell RATs, RMM tool abuse, Middle-East targeting |
| `apt36-transparent-tribe` | Transparent Tribe, Mythic Leopard, ProjectM | Pakistan | Espionage | CrimsonRAT, Android mobile malware, India-focused targeting |
| `apt37-reaper` | ScarCruft, Ricochet Chollima, InkySquid | North Korea | Espionage / surveillance | RoKRAT, zero-day browser exploits, defector surveillance |
| `mustang-panda` | Bronze President, Stately Taurus, RedDelta | China | Espionage | PlugX/DLL side-loading, USB propagation, SE Asia targeting |
| `dark-caracal` | — | Lebanon (GDGS) | Espionage / surveillance | Bandook RAT, multi-platform (Win/Mac/Android/Linux) |
| `patchwork` | Dropping Elephant, Chinastrats, Hangover | India | Espionage | BADNEWS RAT, copy-paste tradecraft, South Asia targeting |
| `pink-sandstorm` | Agrius, DEV-0227 | Iran | Destructive / espionage | Apostle wiper, destructive ops disguised as ransomware |
| `apt10-stone-panda` | Stone Panda, MenuPass, Red Apollo | China (MSS) | Espionage / IP theft | Cloud Hopper supply-chain, MSP targeting |
| `kimsuky` | Velvet Chollima, Emerald Sleet, THALLIUM | North Korea (RGB) | Espionage | BabyShark, credential phishing of think tanks/academia |
| `sidewinder` | Rattlesnake, T-APT-04, Razor Tiger | India | Espionage | LNK chains, .NET implants, Pakistan military targeting |

> Profiles are grounded in MITRE ATT&CK group pages + public advisories; each lists
> its sources. ATT&CK technique IDs are the source of truth — verify against
> <https://attack.mitre.org/groups/> if intel looks stale.
