---
name: seven-question-gate
description: 7-question gate run before promoting a finding to FINDING + opening a report. Kills weak/non-impactful findings before they reach the report stage and damage validity ratio.
metadata:
  when_to_use: "bug bounty triage gate validate finding before report n/a duplicate"
  mitre_attack: T1497
  subdomain: verification
---

# 7-Question Gate

Run this gate **after `validate_finding` returns success but BEFORE
adding the finding to the report**. Any "no" → kill the finding,
don't write a report. This saves bounty validity-ratio and engagement-
report quality.

## The 7 questions

### 1. Is the asset in scope?

Check the engagement's `scope.md` (recon/decepticon's RoE doc):
- In-scope domain list
- In-scope IP/CIDR list
- Out-of-scope explicit exclusions (test envs, staging, third-party
  CDNs/CDN-managed subdomains where the org doesn't own the
  underlying machine)

If asset is not on the in-scope list OR on the out-of-scope list:
**kill**. Do not write the report.

### 2. Is there real-world impact?

Theoretical bugs without demonstrable impact get N/A on every BB
program. Concrete impact statements include:
- "An attacker can read victim user X's PII (email, name, DOB, SSN)"
- "An attacker can post on behalf of victim user X"
- "An attacker can transfer funds from victim user X's account"
- "An attacker can persist code on the production server"
- "An attacker can pivot to internal network 10.0.0.0/8"

If the finding's impact is "configuration is non-default" or "the
manual recommends X but the deployment does Y" without concrete
attacker-reachable harm: **kill**.

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

If the PoC stops short of impact demonstration: **kill** or queue
for re-verification with a better PoC.

### 4. Is the impact **above the program's severity floor**?

Many BB programs explicitly out-of-scope:
- CSRF on logout endpoint
- Self-XSS (requires victim to inject own payload)
- Missing security headers
- Information disclosure of public-by-design info
- Rate-limit issues w/o demonstrated abuse
- Subdomain takeover candidates where ownership can't be proven
- Clickjacking without authenticated state change

Read the program's "out of scope" / "won't fix" / "informational
only" list. If the finding falls in those: **kill** or escalate
to a chain that crosses the floor.

### 5. Can the operator reproduce it from your PoC alone?

A triager will not have your engagement context. The PoC must work
standalone:
- All required prerequisites stated explicitly (account
  registration, specific user role, specific test data)
- Exact URL / parameter / cookie values
- Browser version if it matters
- Date/time stamp if the bug is recent and the program may patch
  in between

If the PoC requires "you also need state X that I had set up": **kill**
or rewrite the PoC to include the setup.

### 6. Is it a known duplicate?

Check before submitting:
- Run `gh search` / Hacktivity search for the same vuln class on the
  same domain
- Read CHANGELOG / recent disclosures
- Check `confirmed-findings.md` and `rejected-hypotheses.md` notepad
  files in the current engagement

If the report likely duplicates a known disclosure: **kill** unless
your variant has materially different impact or affects a different
component.

### 7. Does the title sell the impact in one line?

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

If you can't write a one-line title that names {vuln class, target,
impact, severity}: **kill** and re-think whether the impact is real.

## Decision

If all 7 are "yes" → proceed to report. Confidence: high.

If any are "no" → kill the finding. Mark it in
`notepad/rejected-hypotheses.md` with the question number that
failed and a one-line reason. Do NOT submit.

## Why this gate matters

Bug-bounty programs track **validity ratio** (valid reports ÷ total
submissions). Low validity → lower triage priority + lower long-term
reward tier. The 7-Question Gate is the difference between a
researcher with 90% validity (high earner) and one with 30% validity
(eventually banned).

For internal engagements, this gate is the difference between a
report consumed by stakeholders and a report that sits in a Jira
backlog forever.

## Integration

Verifier agent loads this skill before finalizing a validated finding. Specifically:

```
1. validate_finding(...) returns success
2. load_skill("/skills/verifier/seven-question-gate/SKILL.md")
3. Walk through Q1-Q7 — write each answer to the finding's KG node:
     kg_add_node(kind="vulnerability", key=<finding>,
                 props={"gate_q1_scope": "yes",
                        "gate_q2_impact": "yes",
                        "gate_q3_poc_proves_impact": "yes",
                        ...})
4. If all Q1-Q7 = yes → return a passing verdict with evidence
5. If any = no → return a failing verdict naming the question and reason
```

This makes the gate auditable — every finding has a written record of
which question pass/failed before it reaches the report stage.

## Anti-patterns

| Anti-pattern | What goes wrong |
|---|---|
| Skip the gate "because validate_finding passed" | False positives reach the report; validity ratio drops |
| Answer "yes" to Q4 without reading the program's out-of-scope list | Submission gets N/A'd as known-out-of-scope |
| Skip Q7 "title sells impact" | Triagers downgrade based on bad first-impression |
| Run the gate AFTER report draft is written | Wasted effort writing reports that get killed |
| Apply this only to High/Critical | All findings benefit; Low findings w/ bad titles also damage validity |

## Cross-references
- Verifier agent prompt: `decepticon/agents/prompts/verifier.md`
- Verifier router: `skills/verifier/SKILL.md`
- Bounty report skill: `skills/verifier/bounty-report/SKILL.md`
- Operator's external `triage-validation` skill (Decepticon-external) for the broader gate methodology
