---
name: fix_verification
description: Methodology for verifying that proposed or deployed fixes actually remediate findings
techniques:
  - regression-testing
  - bypass-analysis
  - fix-completeness
---

# Fix Verification Methodology

## Purpose

A proposed fix is not confirmed until the original attack vector fails AND
no trivially adjacent bypasses exist. This skill defines the verification
procedure that prevents incomplete remediations.

## Verification Steps

### 1. Reproduce the Original Finding
- Re-run the exact PoC that demonstrated the vulnerability.
- Confirm it now fails with the expected defensive response (block, sanitize, deny).
- If the PoC still succeeds, the fix is incomplete — stop and report.

### 2. Test Adjacent Bypasses
- Vary encoding (URL-encode, double-encode, Unicode normalization).
- Vary case, whitespace, null bytes, and truncation.
- Test alternate entry points that share the same code path.
- If the fix is an allowlist, test values just outside the boundary.
- If the fix is a denylist, test values NOT on the list that achieve the same effect.

### 3. Confirm No Regression
- Verify the legitimate functionality still works (the fix didn't break the feature).
- Check error handling: does the fix surface safe error messages, not stack traces?
- Confirm logging: is the blocked attempt logged for detection?

### 4. Evaluate Fix Depth
- **Surface fix**: addresses the symptom (e.g., escaping one field). Fragile.
- **Root-cause fix**: addresses the underlying pattern (e.g., parameterized queries). Durable.
- Document which category the fix falls into and whether a deeper fix is recommended.

## Reporting Template
```
Original Finding: [reference ID]
Fix Description: [what changed]
PoC Re-test: [pass/fail]
Bypass Attempts: [list of attempts and results]
Regression Check: [pass/fail]
Fix Depth: [surface/root-cause]
Verdict: [remediated / partially remediated / not remediated]
```
