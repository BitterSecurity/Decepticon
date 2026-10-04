---
name: final-report
description: "Final engagement report generation — executive summary, technical report, findings aggregation, attack path narrative, detection gap matrix, remediation roadmap."
allowed-tools: Read Write
metadata:
  subdomain: orchestration
  when_to_use: "final report, generate report, engagement complete, all objectives done, executive summary, technical report"
  tags: report, final, executive-summary, technical-report, remediation, detection-gap
  upstream_ref: "Decepticon final-engagement report template — deliverable generation, no direct attack technique"
  skillogy:
    version: 1
    requires: [finding-report]
---

# Final Engagement Report Generation

**Execute when all OPPLAN objectives are in `completed`, `blocked`, or `cancelled` status. Output is two Markdown documents in `report/`.**

## Report Generation Workflow

```
1. Read all findings/*.md              (parse YAML frontmatter for metadata)
2. Read all findings/attack-paths/PATH-*.md
3. Read timeline.jsonl                 (activity log)
4. Read plan/roe.json + plan/conops.json + plan/deconfliction.json  (engagement context)
5. Read plan/opplan.json               (objective status summary)
6. Generate report/executive-summary.md
7. Generate report/technical-report.md
8. Do not create a legacy findings.md summary; summaries belong in report/
```

### Step 1: Initialize Output Directory

```
bash(command="mkdir -p /workspace/report", description="Create the report output directory")
```

### Step 2: Aggregate Findings

Read every `findings/FIND-*.md` (canonical name `findings/FIND-{NNN}.md`; the
`id` frontmatter field matches the filename) and extract frontmatter fields:

```
for each FIND-NNN.md:
  - id, title, severity, cwe, cvss_score, cvss_vector, vrt,
    target (affected_target), affected_component, phase,
    technique (ATT&CK ID), result_kind, verification_status, report_status
```

Sorting rules:
- Primary: severity (`critical` → `high` → `medium` → `low` → `informational`)
- Secondary: CVSS score descending
- De-duplicate findings that appear across multiple objectives (keep highest-severity instance)
- Flag `verification_status: unverified` findings — they require a disclaimer note in the report

### Step 3: Generate Executive Summary

Write to `report/executive-summary.md` using the template below. Use plain business language throughout — zero technical jargon.

---

```markdown
# Executive Summary
## [Engagement Name] — Penetration Test Results

**Prepared for**: [Client Name]
**Engagement Period**: [Start Date] – [End Date]
**Conducted by**: Decepticon Autonomous Red Team
**Classification**: CONFIDENTIAL

---

## Engagement Overview

[2–3 sentences: what was tested, why, what methodology was used.
Example: "Between [dates], [client] engaged the red team to assess the
security posture of [scope]. The assessment simulated a [threat actor
archetype from conops.json] targeting [primary objective from roe.json].
Testing followed the Penetration Testing Execution Standard (PTES) and
MITRE ATT&CK framework."]

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Total Findings | [N] |
| Critical | [N] |
| High | [N] |
| Medium | [N] |
| Low / Informational | [N] |
| Objectives Completed | [N] / [total] |
| Objectives Blocked | [N] / [total] |
| Attack Success Rate | [%] |
| Detection Rate | [%] (findings detected by existing controls) |

---

## Top 3 Critical Findings

### 1. [FIND-ID]: [Title]
[1 paragraph, business impact focus. No CVE numbers, no tool names.
Example: "Attackers with internet access can gain full administrative
control of the company's customer database without any valid credentials.
This would allow theft of all customer records, potential regulatory
fines under GDPR, and complete loss of customer trust."]

### 2. [FIND-ID]: [Title]
[1 paragraph, business impact focus]

### 3. [FIND-ID]: [Title]
[1 paragraph, business impact focus]

*[If fewer than 3 critical findings, use highest-severity findings available.]*

---

## Overall Risk Rating

**[CRITICAL / HIGH / MEDIUM / LOW]**

[2–3 sentences justifying the rating in business terms. Consider: ease of
exploitation, potential business impact, data sensitivity, regulatory exposure.
Example: "The overall risk is rated CRITICAL because an unauthenticated
attacker on the internet achieved domain administrator access within [X]
minutes, with no detection by existing security controls. This represents
a complete failure of the network perimeter and would result in total
compromise of all company systems."]

---

## Strategic Recommendations

1. **[Recommendation 1 — address most critical finding]** — [1 sentence, business language, e.g. "Immediately restrict internet access to the database server and require multi-factor authentication for all administrative accounts."]
2. **[Recommendation 2]** — [1 sentence]
3. **[Recommendation 3 — detection/monitoring gap]** — [1 sentence]
4. **[Recommendation 4 — process/architecture]** — [1 sentence]
5. **[Recommendation 5 — longer-term posture improvement]** — [1 sentence]

---

*Full technical details, evidence, and step-by-step remediation instructions are provided in the Technical Report.*
```

---

### Step 4: Generate Technical Report

Write to `report/technical-report.md` using the template below.

---

```markdown
# Technical Report
## [Engagement Name] — Penetration Test

**Prepared for**: [Client Name]
**Engagement Period**: [Start Date] – [End Date]
**Classification**: CONFIDENTIAL — RESTRICTED DISTRIBUTION
**Document Version**: 1.0

---

## Table of Contents

1. Scope & Methodology
2. Findings Summary
3. Findings Detail
4. Attack Path Narratives
5. Detection Gap Analysis
6. Activity Timeline
7. Remediation Roadmap
8. MITRE ATT&CK Coverage Map
9. Appendix

---

## 1. Scope & Methodology

### In-Scope Targets
[List from roe.json: IP ranges, domains, applications, cloud accounts]

### Out-of-Scope
[List from roe.json: excluded systems, forbidden techniques]

### Rules of Engagement
[Key constraints from roe.json: testing window, notification requirements, prohibited actions]

### Threat Actor Profile
[From conops.json: adversary archetype, assumed initial access, objectives, kill chain]

### Tools Used
[List primary tools used across all objectives — derive from findings/FIND-*.md technique fields]

### MITRE ATT&CK Coverage
Tactics covered: [list TA00xx IDs tested]
Total techniques tested: [N]

### Testing Window
[Start datetime] – [End datetime] ([N] days)

---

## 2. Findings Summary

### All Findings

| ID / Canonical file | Title | Severity | CVSS | Target | Phase | ATT&CK | Result | Verification |
|----|-------|----------|------|--------|-------|--------|--------|------------|
| [FIND-001](../findings/FIND-001.md) | ... | Critical | 9.8 | ... | initial-access | T1190 | vulnerability | verified |
| ... | | | | | | | | |

*Sorted by severity (Critical → High → Medium → Low → Informational), then CVSS descending.*

> **Note on unverified findings**: Findings marked `verification_status: unverified` were observed
> but not fully confirmed due to [testing constraints / time / scope]. These should be
> independently validated before remediation prioritization.

---

## 3. Findings Detail

### Critical Findings

#### [FIND-ID]: [Title]
**Severity**: Critical | **CVSS**: [score or not scored] `[justified vector]` (v[3.1]) | **Verification**: [verified/unverified]
**CWE**: [CWE-ID] | **VRT**: [category/sub-category/variant]
**Target**: [host/service/URL]
**Phase**: [kill chain phase]
**ATT&CK Technique**: [[T-ID](https://attack.mitre.org/techniques/T-ID/)] — [Technique Name]

**Description**
[Technical description of the vulnerability or misconfiguration]

**Evidence**
[Tool output, screenshot reference, command output excerpt]

**Business Impact**
[What an attacker can do with this; data at risk; regulatory implications]

**Remediation**
[Specific, actionable fix with version numbers, configuration values, or code snippets]

**References**
[CVE IDs, vendor advisories, CIS benchmarks, OWASP references]

---

[Repeat for each Critical finding]

### High Findings

[Same template, repeat for each High finding]

### Medium Findings

[Same template, repeat for each Medium finding]

### Low / Informational Findings

[Condensed format acceptable: ID, title, description, remediation — 2–3 sentences each]

---

## 4. Attack Path Narratives

*Derived from `findings/attack-paths/PATH-*.md`. Each narrative tells the story of how
the attacker moved from initial access to objective completion.*

### [PATH-ID]: [Attack Path Title]

**Objective**: [Which OPPLAN objective this path achieved]
**Duration**: [Time from first action to objective completion]
**Entry Point**: [Initial foothold]
**Final Impact**: [What was achieved]

**Narrative**

[Story-form description: "The attacker began by..." Use plain language that a developer
can follow. Include key commands, lateral movement hops, privilege escalation steps.
Reference specific FIND-IDs inline where findings were exploited.]

**Attack Chain Summary**

| Step | Action | Technique | Finding | Tool |
|------|--------|-----------|---------|------|
| 1 | External recon | T1595 | FIND-001 | nmap |
| 2 | ... | | | |

---

[Repeat for each attack path]

---

## 5. Detection Gap Analysis

*For each finding: was it detected by existing security controls?*

| # | Finding | Phase | ATT&CK | Detected | Control | Gap |
|---|---------|-------|--------|----------|---------|-----|
| 1 | FIND-001 | recon | T1595 | No | IDS | No alert for port scan |
| 2 | FIND-003 | initial-access | T1190 | Partial | WAF | Blocked payload but not logged |
| 3 | FIND-005 | post-exploit | T1003 | Yes | EDR | Alert in 2 min, SIEM correlation |

**Detection Rate**: [N]/[total] ([%]) fully detected
**Mean Time to Detect**: [N] minutes (for detected events only)
**Blind Spots**: [List phases/techniques with zero detection coverage]

### Control Effectiveness Summary

| Control | Findings Covered | Detection Rate | Notes |
|---------|-----------------|----------------|-------|
| IDS/IPS | [N] | [%] | [e.g., "Signature-based only — evasion trivial"] |
| WAF | [N] | [%] | |
| EDR | [N] | [%] | |
| SIEM | [N] | [%] | |

---

## 6. Activity Timeline

*Derived from `timeline.jsonl`. Chronologically ordered.*

| Timestamp (UTC) | Objective | Action | Target | Result |
|-----------------|-----------|--------|--------|--------|
| [ISO 8601] | OBJ-001 | Port scan | 10.0.0.0/24 | 12 hosts discovered |
| ... | | | | |

---

## 7. Remediation Roadmap

### Immediate (0–7 days) — Critical & High Findings

| # | Finding | Action | Owner | Effort |
|---|---------|--------|-------|--------|
| 1 | FIND-001 | [Specific action with version/config details] | [Team] | [Xh] |

### Short-term (30 days) — Medium Findings & Detection Gaps

| # | Finding | Action | Owner | Effort |
|---|---------|--------|-------|--------|
| 1 | FIND-002 | [Specific action] | [Team] | [Xh] |

### Long-term (90+ days) — Architecture & Process Improvements

| # | Finding | Action | Owner | Effort |
|---|---------|--------|-------|--------|
| 1 | — | [Architecture change or process improvement derived from detection gaps] | [Team] | [Xw] |

---

## 8. MITRE ATT&CK Coverage Map

*All techniques tested during this engagement with detection status.*

| Tactic | Technique ID | Technique Name | Tested | Finding | Detected |
|--------|-------------|----------------|--------|---------|----------|
| Reconnaissance | T1595 | Active Scanning | Yes | FIND-001 | No |
| Initial Access | T1190 | Exploit Public-Facing Application | Yes | FIND-003 | Partial |
| ... | | | | | |

**Tactics Coverage**: [N] of 14 ATT&CK tactics exercised
**Techniques Tested**: [N] total

---

## 9. Appendix

### A. Tool Inventory

| Tool | Version | Purpose | Phase Used |
|------|---------|---------|------------|
| nmap | [ver] | Port/service scanning | Recon |
| ... | | | |

### B. Engagement Team

| Role | Agent / Operator |
|------|-----------------|
| Red Team Orchestrator | Decepticon |
| Recon Agent | Decepticon Recon |
| Exploit Agent | Decepticon Exploit |
| Post-Exploit Agent | Decepticon PostExploit |

### C. Glossary

| Term | Definition |
|------|------------|
| ATT&CK | MITRE Adversarial Tactics, Techniques & Common Knowledge framework |
| CVSS | Common Vulnerability Scoring System — standardized severity metric (0–10) |
| Lateral Movement | Technique of moving between systems after initial access to reach objectives |
| Privilege Escalation | Gaining higher system permissions than initially obtained |
| C2 | Command & Control — infrastructure used to maintain access to compromised systems |
| OSINT | Open-Source Intelligence — information gathered from public sources |
| EDR | Endpoint Detection & Response — security tool monitoring host-level activity |
| WAF | Web Application Firewall — proxy that filters malicious web traffic |
| SIEM | Security Information & Event Management — log aggregation and alerting platform |
| Kill Chain | Sequential phases of a cyberattack (recon → access → exploit → objective) |
```

---

## Findings Aggregation Rules

| Rule | Detail |
|------|--------|
| Sort order | Critical → High → Medium → Low → Informational, then justified CVSS descending |
| Canonical link | Link directly to `findings/FIND-NNN.md`; do not create per-finding copies under `report/` |
| Verified | Only `result_kind: vulnerability` with `verification_status: verified` can be listed as a confirmed vulnerability |
| Unverified | Preserve the claim and its testing limit, with an explicit disclaimer |
| False positive | Count the tested surface and rebuttal in coverage; exclude it from vulnerability totals |
| Negative results | Include meaningful `result_kind: negative_result` findings as coverage evidence, not vulnerabilities |
| Missing CVSS | State "not scored"; never infer a score from a severity label |
| Empty attack paths | If no PATH-*.md files exist, omit Section 4 and note why |
| Empty timeline | If timeline.jsonl is empty or missing, omit Section 6 and note why |

## Finding lifecycle

The only per-finding document is `findings/FIND-NNN.md`. The finding author
writes observed facts with `report_status: draft`; an independent
`finding_verifier` updates `verification_status` and its rationale after a
positive reproduction and equivalent negative control; `finding_reporter`
completes the same document in place using deterministic `cvss_score` when a
CVSS 3.1 vector is justified. `report_status: complete` means the document was
checked and is ready to read, not that the issue is verified or fixed.

The technical report's findings table must cite the canonical path for every
finding. For `false_positive` and `negative_result` entries, report what was
tested and the evidence that ruled out the claim. Do not hide unsuccessful
work; do not count it as compromise. For `unverified` entries, describe the
missing proof and next discriminating test. When the reporter could not finish,
link the draft and label it explicitly instead of inventing a complete report.

## Quality Checklist

Before writing the final files, verify:

- [ ] Every finding table row links to the existing `findings/FIND-NNN.md`
- [ ] Every listed confirmed vulnerability has `result_kind: vulnerability` and `verification_status: verified`
- [ ] The score, when present, matches the CVSS vector and deterministic tool result
- [ ] No numeric score is inferred from the severity label
- [ ] Negative results and false positives are counted only as tested coverage
- [ ] Detection gap matrix covers all findings (no FIND-ID omitted)
- [ ] Remediation roadmap has specific, actionable items
- [ ] Executive summary contains zero technical jargon
- [ ] MITRE ATT&CK IDs, when present, were validated against a source of truth
- [ ] Timeline is in chronological order
- [ ] All referenced file paths exist in the workspace
- [ ] Overall risk rating matches the highest-severity verified finding
