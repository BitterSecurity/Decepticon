---
name: kill-chain-analysis
description: "Kill chain analysis and attack path decision-making — findings analysis, attack vector selection, target prioritization, phase transitions."
allowed-tools: Read
metadata:
  subdomain: orchestration
  kind: analytic
  when_to_use: "analyze findings, select attack vector, prioritize targets, which technique, attack path, next attack, choose approach, alternative vector, blocked what next"
  tags: kill-chain, decision-making, attack-path, target-prioritization, technique-selection
  upstream_ref: "Lockheed Martin Cyber Kill Chain + Decepticon attack-path planner — analytic decision support, no direct attack technique"
---

# Kill Chain Analysis & Attack Path Decision-Making

## Decision Framework

When selecting the next action, evaluate in order:

1. **What does the OPPLAN say?** — Prioritized objectives drive decisions
2. **What do findings tell us?** — Previous phase results constrain options
3. **What's the risk/reward?** — Lower noise approaches first
4. **What's the OPSEC impact?** — Consult `opsec` skill before noisy actions

## Findings Analysis

### After Recon Phase — Selecting Attack Vectors

Read `recon/` outputs and categorize:

| Finding Type | Indicates | Next Action |
|-------------|-----------|-------------|
| Web apps with known CVEs | Web exploitation path | `exploit` → web techniques |
| AD services (88/389/636) | AD attack surface | `exploit` → AD techniques (after initial access) |
| Exposed credentials (OSINT) | Credential-based access | `exploit` → credential stuffing/spray |
| Cloud misconfigs (S3/blob) | Cloud attack path | `exploit` → cloud-specific techniques |
| VPN/remote access services | Network perimeter entry | `exploit` → VPN/RDP exploitation |
| Employee emails + breach data | Social engineering path | `exploit` → phishing (if in scope) |

### Attack Vector Prioritization

Rank available vectors by:

```
Score = (Success Probability × Impact) / Detection Risk

1. Valid credentials from OSINT        → High prob, High impact, Low noise
2. Known web CVE (public exploit)      → High prob, Med impact, Med noise
3. AD misconfiguration (no patch)      → Med prob,  High impact, Med noise
4. Password spray against O365         → Med prob,  High impact, High noise
5. Zero-day or custom exploit          → Low prob,  High impact, Low noise
```

Always prefer: **credentials > misconfigurations > known CVEs > brute force**

### After Exploitation — Deciding Post-Exploit Strategy

Once a foothold is established, analyze:

| Context | Decision |
|---------|----------|
| Low-privilege user on workstation | Prioritize: privesc → cred dump → lateral to server |
| Service account on server | Prioritize: cred dump (may have cached admin creds) → lateral |
| Domain user credentials | Prioritize: AD enumeration → Kerberoasting → DCSync path |
| Local admin on single host | Prioritize: cred dump → check for cached domain creds → lateral |
| Already domain admin | Prioritize: objective completion → evidence collection → reporting |

## Handling Blocked Objectives

### Failure Analysis Decision Tree

```
Objective BLOCKED
│
├── WHY did it fail?
│   ├── Defense mechanism (WAF/EDR/IDS)
│   │   → Consult defense-evasion skill → retry with evasion
│   │
│   ├── Missing prerequisite (need creds/access/info)
│   │   → Identify which objective provides it → update `blocked_by` through OPPLAN tools
│   │
│   ├── Target hardened / not vulnerable
│   │   → Check findings for alternative target → redirect attack
│   │
│   └── Tool failure / environment issue
│       → Retry with different tool or approach
│
├── Is there an ALTERNATIVE path?
│   ├── YES → Craft new delegation with adjusted approach
│   └── NO  → Mark BLOCKED, document reason, proceed to next objective
│
└── Should we REVISIT later?
    ├── YES (new intel may help) → Keep status BLOCKED, note in OPPLAN and lessons_learned.md
    └── NO (dead end) → Mark BLOCKED permanently
```

### Common Pivots

| Original Approach | Alternative When Blocked |
|-------------------|-------------------------|
| SQLi on web app | SSTI, deserialization, SSRF, or move to different web app |
| Kerberoasting | AS-REP roasting, ADCS abuse, password spray |
| LSASS dump blocked by EDR | nanodump, comsvcs.dll, MiniDumpWriteDump via syscall |
| WinRM blocked | PsExec, WMI, DCOM, RDP, SMB exec |
| Password spray lockout | Low-and-slow spray, single-password-multiple-users |

## MITRE ATT&CK Phase Mapping

Use this to map OPPLAN objective phases to ATT&CK tactics:

| OPPLAN Phase | ATT&CK Tactic | Sub-Agent |
|-------------|---------------|-----------|
| Recon | TA0043 Reconnaissance | `recon` |
| Initial Access | TA0001 Initial Access | `exploit` |
| Execution | TA0002 Execution | `exploit` |
| Persistence | TA0003 Persistence | `postexploit` |
| Privilege Escalation | TA0004 Privilege Escalation | `postexploit` |
| Defense Evasion | TA0005 Defense Evasion | `exploit` / `postexploit` |
| Credential Access | TA0006 Credential Access | `postexploit` |
| Discovery | TA0007 Discovery | `recon` / `postexploit` |
| Lateral Movement | TA0008 Lateral Movement | `postexploit` |
| Collection | TA0009 Collection | `postexploit` |
| Exfiltration | TA0010 Exfiltration | `postexploit` |

## Target Prioritization

When multiple targets are available, prioritize:

1. **Crown jewels** — Targets explicitly named in OPPLAN objectives
2. **Domain controllers** — Access = domain-wide compromise
3. **File servers** — Likely contain sensitive data for objectives
4. **Admin workstations** — Cached credentials for further access
5. **Application servers** — May contain database credentials, API keys
6. **Standard workstations** — Lower value, but may provide stepping stones
