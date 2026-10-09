---
name: counterevidence
description: Methodology for evaluating and documenting counterevidence in findings
techniques:
  - evidence-evaluation
  - false-positive-prevention
---

# Counterevidence Methodology

## Purpose

Every security finding must include a **counterevidence** section — the strongest
case AGAINST the finding being real or having the stated severity. This prevents
false positives, builds trust in reports, and demonstrates intellectual honesty.

## When to Document Counterevidence

- Before filing ANY finding
- When upgrading severity
- When chaining findings
- When reporting to clients/stakeholders

## Framework

### 1. Challenge the Root Cause
- Could the observed behavior be a feature, not a bug?
- Is there a security control you didn't test that might prevent exploitation?
- Could the input validation be present at a different layer?

### 2. Challenge the Impact
- What's the actual blast radius? Is it limited by other controls?
- Does the vulnerability require unlikely preconditions?
- Is the affected data actually sensitive in this context?

### 3. Challenge Exploitability
- Does the PoC work in the production environment, not just local/staging?
- Are there WAF/IDS rules that would block the exploit?
- Is authentication required? How privileged must the attacker be?

### 4. Document Honestly
```
Counterevidence: [strongest case against]
Confidence: [high/medium/low]
Confidence Rationale: [why this confidence level]
Severity Change Conditions: [what would raise/lower severity]
```

## Examples

### Good Counterevidence
> "The SQL injection payload only works in the search parameter, and the database
> user has SELECT-only permissions. However, UNION-based extraction of user
> credentials was confirmed, and the password hashes use MD5 without salt."

### Bad Counterevidence
> "None" / "N/A" / "This is definitely a real vulnerability"

## Confidence Calibration

- **High**: PoC confirmed in target environment, impact demonstrated, no mitigating controls found
- **Medium**: PoC works but impact is theoretical, OR mitigating controls exist but are bypassable
- **Low**: Behavior observed but exploitation unconfirmed, OR significant uncertainty about impact
