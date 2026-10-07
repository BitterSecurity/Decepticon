# Objective Decomposition Rules

## Rule 1: One Objective = One Context Window

Every objective must be completable by a fresh agent instance in a single iteration. If you're unsure, it's too big — split it.

**Too big (split):**
- "Perform complete reconnaissance" → Split into passive, active, synthesis
- "Scan all ports on all targets" → Split by target group or port range

**Right size:**
- "Enumerate subdomains using passive sources"
- "Port scan top 1000 on 10.0.1.0/24"
- "Run nuclei on live web hosts"

## Rule 2: Dependency DAG

Use `blocked_by` for prerequisites, `any_of` for alternative successful paths,
and required facts for evidence gates. Independent objectives may run in
parallel, including objectives in the same workflow phase. A later technique
can require a specific finding rather than an entire earlier phase.

```
Passive inventory ──→ web-entry test ──┐
                 └──→ account-path test ├──→ access validation
                                       └──→ evidence report
```

## Rule 3: Mandatory Acceptance Criteria

Every objective MUST include these three types:

1. **Scope check:** "All targets verified against plan/roe.json in-scope list"
2. **OPSEC check:** At least one OPSEC criterion (rate limit, timing, UA)
3. **Output persistence:** "Results saved to <engagement>/recon/..." (or exploit/, post-exploit/) with specific path

## Rule 4: Verifiable Criteria Only

Every acceptance criterion must be mechanically checkable.

**Bad:** "Thorough reconnaissance achieved", "Good coverage", "Comprehensive results"
**Good:** "subfinder results saved to <engagement>/recon/subfinder.txt", "Scan rate <= 10 req/sec"

## Rule 5: MITRE ATT&CK Mapping

When an objective has a justified ATT&CK mapping, provide a current tactic ID
and technique list. The OPPLAN server checks active IDs and tactic membership
against Enterprise ATT&CK v19.2 and stores canonical descriptions:

| Activity | Technique |
|---|---|
| Passive DNS | T1596.001 |
| WHOIS | T1596.002 |
| CT logs | T1596.003 |
| Active port scan | T1595.001 |
| Vulnerability scan | T1595.002 |
| Web fuzzing | T1595.003 |
| OS fingerprinting | T1592.001 |
| Service version detection | T1592.002 |
| Search engines | T1593.002 |
| Exploit public app | T1190 |
| Phishing | T1566.001 |

## Rule 6: OPSEC Level Assignment

| OPSEC Level | When to Use | C2 Tier | Examples |
|---|---|---|---|
| loud | No evasion needed; testing detection capability | interactive | Full port scan with defaults, exploit PoC testing |
| standard | Basic OPSEC; modify default signatures | interactive | Custom user-agents, varied scan timing |
| careful | Active evasion; avoid known signatures | short-haul | LOLBins preferred, no disk-dropped tools |
| quiet | Minimal footprint; blend with normal traffic | long-haul | Living-off-the-land only, encrypted C2 |
| silent | Zero detection tolerance; abort if burned | long-haul | Custom tooling, domain fronting, covert channels |

Choose OPSEC level based on the engagement's detection posture goals, not just the action's noisiness. A "loud" scan in a detection-testing engagement is appropriate; the same scan in a stealth assessment should be "careful" or "quiet".

## Rule 7: Concessions (TIBER/CORIE)

For objectives that may block the kill chain, define pre-authorized assists:

**Examples:**
- "If initial access via web app fails after 5 attempts, white cell provides VPN credentials"
- "If lateral movement to DC is blocked, skip to credential dump on compromised host"
- "If C2 beacon is detected and burned, white cell resets defender alert and provides new C2 domain"

Concessions prevent the autonomous loop from getting stuck at a kill chain gate.

## Validation Checklist

Run through before finalizing the OPPLAN:

- [ ] Every objective fits in one context window
- [ ] Every dependency edge is justified and the graph has no cycle
- [ ] Independent and alternative paths are represented explicitly
- [ ] No objective targets out-of-scope assets
- [ ] Every objective has scope check criterion
- [ ] Every objective has OPSEC check criterion
- [ ] Every objective has output persistence criterion
- [ ] ATT&CK technique and tactic IDs are current and belong together where mapped
- [ ] Priority reflects relative urgency without implying dependency
- [ ] OPSEC levels assigned per the table above
- [ ] C2 tier matches OPSEC level
- [ ] Concessions defined for kill-chain-critical objectives
- [ ] `threat_profile` field summarizes threat actor in one sentence
