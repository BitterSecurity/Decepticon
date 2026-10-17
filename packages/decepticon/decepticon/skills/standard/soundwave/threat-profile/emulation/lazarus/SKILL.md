---
name: lazarus
description: "Lazarus Group (Hidden Cobra, DPRK RGB) adversary-emulation playbook — financially-motivated crypto/DeFi theft and supply-chain intrusion: fake-job social engineering, trojanized apps, wallet/key theft, and on-chain DeFi/bridge exploitation (testnet/fork only). Use when emulating DPRK financial actors against a crypto/exchange/DeFi target. Triggers on: 'emulate Lazarus', 'Hidden Cobra', 'DPRK crypto', 'AppleJeus', '3CX supply chain', 'DeFi bridge attack', 'crypto theft'."
allowed-tools: Read Write Edit
metadata:
  skillogy:
    version: 1
  subdomain: planning
  when_to_use: "emulate Lazarus, Hidden Cobra, ZINC, Diamond Sleet, DPRK, cryptocurrency theft, DeFi protocol attack, bridge exploit, AppleJeus, 3CX supply chain, fake job social engineering, wallet key theft, exchange compromise"
  tags: adversary-emulation, lazarus, dprk, cryptocurrency, defi, supply-chain
  mitre_attack: T1566.003, T1195.002, T1574.002, T1555, T1657, T1071.001
---

# Lazarus Group — Adversary Emulation Playbook

> Tier-3 DPRK (RGB) actor whose modern mission is **revenue generation** — North Korea-linked
> actors have stolen well over $3B in crypto. Two converging tracks: (a) enterprise intrusion
> via fake-job social engineering, trojanized trading apps (AppleJeus), and software
> supply-chain (3CX); (b) **on-chain DeFi/bridge exploitation** to drain protocols. Authorized
> red-team emulation only; on-chain steps run on **testnet or a mainnet fork** — never real funds.

## When to emulate Lazarus

- Target is a cryptocurrency exchange, DeFi protocol, cross-chain bridge, wallet provider, or
  a fintech/blockchain firm and the client wants the full DPRK money-theft kill chain tested.
- The engagement spans both the **enterprise estate** (devs, build pipeline, cloud) and the
  **smart-contract surface** — Decepticon's contract-audit lane is what makes this actor a
  good fit (see `../../references/apt-groups.md`).

## ThreatProfile seed (`plan/threat-profile.json`)

```json
{
  "engagement_name": "<fill>",
  "actor_name": "Lazarus-like (Hidden Cobra)",
  "actor_aliases": ["Hidden Cobra", "ZINC", "Diamond Sleet", "Labyrinth Chollima", "NICKEL ACADEMY"],
  "group_id": "G0032",
  "tier": "tier-3",
  "sophistication": "nation-state",
  "motivation": "financial",
  "initial_access": ["T1566.003", "T1195.002", "T1199", "T1204.002"],
  "key_ttps": ["T1574.002", "T1027", "T1059.006", "T1555", "T1552.001", "T1657", "T1071.001", "T1567.002"],
  "tools": ["Engagement-owned trojanized app + Sliver", "Foundry / Anvil (forked mainnet PoC)", "canary wallets", "NetExec"],
  "infrastructure": ["Fake-recruiter persona + lure docs", "Supply-chain build-step foothold", "Testnet/fork RPC endpoints"],
  "recent_cti_delta": "$3B+ crypto stolen; targets entire blockchain ecosystems (cross-chain bridges, DeFi, identity providers); Operation Dream Job fake-job social engineering vs developers; 3CX double software supply chain; AppleJeus trojanized trading apps.",
  "confidence": "probable"
}
```

## Kill-chain emulation

| # | Phase | MITRE | Emulated action | Executing agent → skill |
|---|-------|-------|-----------------|-------------------------|
| 1 | Recon | T1591 / T1589 | Profile devs, the DeFi protocol, and the bridge/contract surface | recon → `/skills/standard/recon/osint/SKILL.md`, `/skills/standard/osint/SKILL.md` |
| 2 | Initial Access | T1566.003 | Fake-job / DeFi-collab social engineering with a lure doc | phisher → `/skills/standard/phisher/SKILL.md` |
| 3 | Initial Access (alt) | T1195.002 / T1199 | Trojanized dependency or build-step (3CX pattern) | exploit → `/skills/standard/exploit/supplychain/dep-confusion/SKILL.md`; `/skills/standard/supply-chain/SKILL.md` |
| 4 | Execution | T1204.002 / T1574.002 | Trojanized app + DLL side-load into a trusted process | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 5 | Credential / Key theft | T1555 / T1552.001 | Steal wallet keys, seed phrases, cloud creds, signing keys | post-exploit → `/skills/standard/post-exploit/credential-access/SKILL.md` |
| 6 | Web/API (exchange) | T1190 | Exchange web/API abuse (authz, IDOR, JWT) | exploit → `/skills/standard/exploit/web/SKILL.md` (route to `idor` / `jwt` / `api`) |
| 7 | On-chain (bridge) | — (DeFi) | Drain a cross-chain bridge via logic/validator flaw (fork) | contracts → `/skills/standard/contracts/bridge-exploit/SKILL.md` |
| 8 | On-chain (sig) | — (DeFi) | Signature replay / ecrecover abuse on the protocol (fork) | contracts → `/skills/standard/contracts/signature-replay/SKILL.md` |
| 9 | On-chain (authz) | — (DeFi) | Missing-modifier / wrong-owner privileged call (fork) | contracts → `/skills/standard/contracts/access-control/SKILL.md` |
| 10 | C2 | T1071.001 | Sliver HTTPS beacon for the enterprise foothold | post-exploit → `/skills/standard/post-exploit/c2-sliver/SKILL.md` |
| 11 | Exfil / Theft | T1657 / T1567.002 | Simulated fund movement to a canary address (testnet/fork) | post-exploit → `/skills/standard/post-exploit/reporting/SKILL.md` |

## CONOPS kill_chain (copy into `conops.json`)

1. `recon` — devs + protocol + bridge/contract surface (1).
2. `initial-access` — fake-job social engineering / trojanized supply chain (2-3).
3. `post-exploit` — trojan execution, wallet-key/cred theft, exchange web/API abuse, **on-chain DeFi exploitation on a fork** (4-9).
4. `c2` — Sliver (10).
5. `exfiltration` — simulated theft to canary address on testnet/fork (11).

## OPSEC & signature fidelity

- **Patient grooming.** Operation Dream Job runs for weeks — the social-engineering relationship
  is built slowly with benign files first.
- **Obfuscated, side-loaded execution** into trusted processes (mirror the DLL side-load chain).
- **On-chain moves are irreversible on mainnet** — fidelity comes from a *working PoC on a
  forked mainnet*, not from touching production funds.

## RoE / safety gates

- **On-chain exploitation MUST run on testnet or a forked-mainnet (Anvil) RPC.** Never sign a
  real mainnet transaction. Add an `EMERGENCY` abort: *"a transaction signed against a
  production/mainnet RPC or a real wallet key used."*
- Developer-targeted social engineering requires authorization + lure-deconfliction
  (`/skills/standard/phisher/lure-deconfliction/SKILL.md`).
- Key/seed theft is simulated against **canary wallets** seeded for the exercise.

## Deconfliction

- Record fork/testnet RPC URLs, canary wallet addresses, and the trojanized-app hash in
  `deconfliction.json` + `cleanup.json`.
- Mark the recruiter persona and lure documents so they can be distinguished from a real
  Dream Job approach.

## Fidelity notes (deviations)

- **No real AppleJeus/3CX samples** — emulate the trojanized-app + supply-chain *delivery
  pattern* with an engagement-owned dropper + Sliver.
- DeFi exploitation reproduces the real bug classes (bridge validator bypass, signature
  replay, access-control) with Foundry PoCs on a fork; the proof is a drained canary balance
  on the fork, never a real protocol.
- Theft is demonstrated as a fund move to a canary address on testnet/fork; the deliverable
  distinguishes "drained on fork" from "production funds" (never the latter).
