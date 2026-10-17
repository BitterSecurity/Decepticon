---
name: seven-question-gate
description: Seven-question finding assessment covering technical evidence, scope, program eligibility, duplicate evidence, reproducibility, and title quality as separate dimensions.
metadata:
  skillogy:
    version: 1
  when_to_use: "bug bounty triage gate validate finding before report n/a duplicate"
  mitre_attack: T1497
  subdomain: verification
---

# Seven-Question Assessment Reference

These questions assess different dimensions of a finding. Technical validity
depends on independent reproduction and evidence; scope, program eligibility,
duplicate status, and presentation quality are recorded separately. The active
workflow owns sequencing, report completion, and submission disposition. A
negative answer is not by itself a technical rejection.

## The 7 questions

### 1. Is the asset in scope?

Check the engagement's `scope.md` (recon/decepticon's RoE doc):
- In-scope domain list
- In-scope IP/CIDR list
- Out-of-scope explicit exclusions (test envs, staging, third-party
  CDNs/CDN-managed subdomains where the org doesn't own the
  underlying machine)

An asset outside approved scope must not receive target-facing testing. Record
the scope constraint and policy source separately from any existing technical
evidence; scope exclusion does not refute that evidence.

### 2. Is there real-world impact?

Distinguish demonstrated harm from hypothetical consequences. Concrete impact
statements include:
- "An attacker can read victim user X's PII (email, name, DOB, SSN)"
- "An attacker can post on behalf of victim user X"
- "An attacker can transfer funds from victim user X's account"
- "An attacker can persist code on the production server"
- "An attacker can pivot to internal network 10.0.0.0/8"

If the finding's impact is "configuration is non-default" or "the
manual recommends X but the deployment does Y" without concrete
attacker-reachable harm, record the observed behavior and impact limitation
without claiming an unsupported vulnerability or severity.

### 3. Does the PoC actually prove the impact?

The `validate_finding` result is necessary but not sufficient. The
**PoC must demonstrate the IMPACT**, not just trigger the vector.

Examples:
- IDOR PoC must show ATTACKER session reading VICTIM data — not
  just "request returned 200 OK"
- XSS PoC must execute attacker-controlled JS in victim's browser
  context — not just "alert(1) reflected in HTML source"
- SQLi PoC must extract real data — not just "single quote → 500"
- SSRF PoC must reach an internal-only target — not just "external
  fetch worked"

If the PoC stops short of the claimed impact, that impact remains unverified.
Describe the missing proof and preserve any narrower demonstrated result. A
false-positive verdict requires evidence refuting the technical claim.

### 4. Is the impact **above the program's severity floor**?

Many BB programs explicitly out-of-scope:
- CSRF on logout endpoint
- Self-XSS (requires victim to inject own payload)
- Missing security headers
- Information disclosure of public-by-design info
- Rate-limit issues w/o demonstrated abuse
- Subdomain takeover candidates where ownership can't be proven
- Clickjacking without authenticated state change

Compare against the supplied program's "out of scope" / "won't fix" /
"informational only" list and cite the policy source. A finding below the floor
may remain technically verified. Record eligibility separately; the floor does
not justify changing CVSS metrics or expanding testing to raise severity.

### 5. Can the operator reproduce it from your PoC alone?

A triager will not have your engagement context. The PoC must work
standalone:
- All required prerequisites stated explicitly (account
  registration, specific user role, specific test data)
- Exact URL / parameter / cookie values
- Browser version if it matters
- Date/time stamp if the bug is recent and the program may patch
  in between

If the PoC requires "you also need state X that I had set up", document that
setup and the missing reproduction evidence. Incomplete instructions are a
report-quality defect; insufficient independent reproduction leaves the claim
unverified rather than automatically refuted.

### 6. Is it a known duplicate?

Compare supplied prior reports, CHANGELOG entries, recent disclosures, and
local `confirmed-findings.md` / `rejected-hypotheses.md` records by mechanism,
component/version, privileges, and supported impact. Cite the matching evidence;
missing history means unknown novelty.

A duplicate remains a technical finding. Record exact overlap or a materially
different variant separately from verification status. This assessment does not
authorize external account access or submission.

### 7. Does the title state the supported impact in one line?

Bad titles:
- "SSRF on /webhook"
- "JWT issue"
- "Mass assignment"

Good titles:
- "SSRF on /webhook → AWS instance-metadata cred extract → full
  account takeover"
- "JWT alg=none bypass → admin impersonation of any user"
- "Mass assignment on PATCH /api/users/me → self-promote to
  is_admin=true"

A useful title names {vuln class, target, impact, severity} to the extent
supported by evidence. Revise a weak title without changing the technical
verdict; do not invent impact to strengthen the wording.

## Assessment Record

Record each answer with its evidence or policy source and uncertainty. Keep the
following dimensions distinct:

| Dimension | Interpretation |
|---|---|
| Technical validity | Independent positive/control evidence supports, refutes, or leaves the claimed behavior unresolved. |
| Scope and eligibility | Approved asset/technique boundaries and supplied program criteria; missing policy remains uncertain. |
| Novelty | Exact overlap, supported variant, or unknown based on available prior reports. |
| Report quality | Reproduction prerequisites, evidence references, and accurate title/impact wording. |

A question-level record may use the existing `gate_q1_scope`, `gate_q2_impact`,
and `gate_q3_poc_proves_impact` properties, with corresponding entries for Q4–Q7.
Each entry needs a reason and source reference. Do not collapse the answers into
an all-or-nothing technical verdict or discard evidence for a business exclusion.

## Common Assessment Errors

| Error | Consequence |
|---|---|
| Treat validator success as sufficient proof of impact | An oracle may establish only a narrower behavior. |
| Infer eligibility without the supplied policy | Technical validity becomes confused with program acceptance. |
| Treat unknown disclosure history as unique | Novelty is overstated without evidence. |
| Reject a technically proven issue for a duplicate or weak title | Valid evidence is lost for an unrelated disposition. |
| Apply evidence scrutiny only to High/Critical findings | Lower-severity claims can remain unsupported. |

## Cross-references
- Verifier agent prompt: `decepticon/agents/prompts/verifier.md`
- Verifier router: `skills/verifier/SKILL.md`
- Bounty report skill: `skills/verifier/bounty-report/SKILL.md`
- Operator's external `triage-validation` skill (Decepticon-external) for the broader gate methodology
