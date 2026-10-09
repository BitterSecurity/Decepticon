---
name: severity_calibration
description: CVSS-aligned severity calibration methodology for consistent vulnerability scoring
techniques:
  - cvss-scoring
  - impact-assessment
  - severity-consistency
---

# Severity Calibration Methodology

## Purpose

Consistent severity scoring across findings prevents inflation, maintains
credibility, and ensures triage teams allocate effort correctly. This skill
defines calibration anchors tied to CVSS v3.1/v4.0 and real-world impact.

## Calibration Anchors

### Critical (CVSS 9.0–10.0)
- Unauthenticated remote code execution
- Full database dump without authentication
- Authentication bypass granting admin access
- Supply-chain compromise affecting all users

### High (CVSS 7.0–8.9)
- Authenticated RCE requiring low-privilege account
- Stored XSS in admin panel with session hijack demonstrated
- IDOR exposing bulk PII with enumerable identifiers
- SQL injection with confirmed data extraction but limited DB permissions

### Medium (CVSS 4.0–6.9)
- Reflected XSS requiring user interaction
- CSRF on state-changing non-critical actions
- Information disclosure of internal paths, versions, or non-sensitive configs
- Rate-limiting absence on non-authentication endpoints

### Low (CVSS 0.1–3.9)
- Missing security headers without demonstrated exploit path
- Verbose error messages without sensitive data
- Self-XSS (attacker can only target themselves)
- Cookie without Secure flag on HTTPS-only site

## Common Calibration Errors

| Error | Direction | Correction |
|-------|-----------|------------|
| Scoring theoretical RCE without PoC | Over | Downgrade until PoC or lower confidence |
| Ignoring chained impact | Under | Score the chain, document each link |
| Assuming WAF blocks exploit | Over-mitigated | Test the WAF; document bypass or confirm block |
| Treating all XSS as High | Over | Differentiate stored/reflected/self, auth context |
| Rating info-disclosure as Critical | Over | Only critical if it directly enables a critical attack |

## Process

1. Score the finding using CVSS calculator with all metric groups.
2. Compare against calibration anchors above — does the score match the category?
3. Apply environmental adjustments (asset criticality, data sensitivity).
4. Document counterevidence (see `counterevidence` skill).
5. If the score feels wrong after calibration, trust the anchors over intuition and explain the delta.
