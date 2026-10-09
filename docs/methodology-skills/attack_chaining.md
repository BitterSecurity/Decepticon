---
name: attack_chaining
description: Methodology for combining lower-severity findings into higher-impact attack chains
techniques:
  - finding-composition
  - impact-escalation
  - attack-path-modeling
---

# Attack Chaining Methodology

## Purpose

Individual findings may appear low-severity in isolation but become critical
when combined. This skill defines how to identify, document, and score
multi-step attack chains that demonstrate realistic threat scenarios.

## Identifying Chain Candidates

### Signals That Findings Chain
- Finding A produces output that Finding B consumes (data flow link).
- Finding A lowers a barrier that Finding B requires (privilege link).
- Finding A provides reconnaissance that makes Finding B feasible (info link).
- Two findings affect the same trust boundary from different angles.

### Common Chain Patterns
| Step 1 | Step 2 | Combined Impact |
|--------|--------|-----------------|
| Info disclosure (endpoints/versions) | Known CVE exploit | Targeted RCE |
| IDOR on user profile | Email in profile → password reset | Account takeover |
| Open redirect | OAuth callback manipulation | Token theft |
| SSRF to internal metadata | Cloud credential extraction | Lateral movement |
| Low-priv stored XSS | Admin visits page → session hijack | Privilege escalation |

## Building the Chain

### 1. Map the Attack Path
Draw each step: entry point → vulnerability → intermediate state → next vulnerability → final impact.
Every transition must be demonstrable, not assumed.

### 2. Validate Each Link
- Confirm each finding independently before chaining.
- Test the transitions: does the output of step N actually work as input to step N+1?
- Note any timing constraints, race conditions, or user interaction required.

### 3. Score the Chain
- The chain severity equals the **impact of the final state**, not the sum of individual scores.
- Adjust attack complexity upward for each additional step (more steps = harder to execute).
- Document the minimum privilege level required at the start of the chain.

### 4. Document for Triage
```
Chain Title: [descriptive name]
Steps: [ordered list with finding references]
Entry Point: [where the attacker starts]
Final Impact: [what the attacker achieves]
Complexity: [low/medium/high — based on step count and preconditions]
Combined Severity: [CVSS score of the end state with adjusted complexity]
Each Link Verified: [yes/no per transition]
```

## Anti-Patterns
- **Fantasy chains**: assuming steps work without testing transitions.
- **Severity stacking**: adding CVSS scores together (9.0 + 4.0 ≠ 13.0).
- **Ignoring preconditions**: omitting that step 3 requires a valid session from step 1.
- **Over-chaining**: more than 4–5 steps usually means the scenario is unrealistic for a real attacker.
