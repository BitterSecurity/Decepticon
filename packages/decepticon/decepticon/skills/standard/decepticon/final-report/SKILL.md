---
name: final-report
description: "Final engagement report generation — executive summary, technical report, findings aggregation, attack path narrative, detection gap matrix, remediation roadmap."
allowed-tools: Read Write
metadata:
  subdomain: orchestration
  when_to_use: "final report, generate report, engagement complete, all objectives done, executive summary, technical report"
  tags: report, final, executive-summary, technical-report, remediation, detection-gap
  upstream_ref: "Decepticon final-engagement report template — deliverable generation, no direct attack technique"
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
    technique (ATT&CK ID), status, confidence
```

Sorting rules:
- Primary: severity (`critical` → `high` → `medium` → `low` → `informational`)
- Secondary: CVSS score descending
- De-duplicate findings that appear across multiple objectives (keep highest-severity instance)
- Flag `confidence: unverified` findings — they require a disclaimer note in the report

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

| ID | Title | Severity | CVSS | Target | Phase | ATT&CK | Status | Confidence |
|----|-------|----------|------|--------|-------|--------|--------|------------|
| FIND-001 | ... | Critical | 9.8 | ... | initial-access | T1190 | Verified | verified |
| ... | | | | | | | | |

*Sorted by severity (Critical → High → Medium → Low → Informational), then CVSS descending.*

> **Note on unverified findings**: Findings marked `confidence: unverified` were observed
> but not fully confirmed due to [testing constraints / time / scope]. These should be
> independently validated before remediation prioritization.

---

## 3. Findings Detail

### Critical Findings

#### [FIND-ID]: [Title]
**Severity**: Critical | **CVSS**: [score] `[vector]` (v[4.0]) | **Confidence**: [verified/unverified]
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
| Sort order | Critical → High → Medium → Low → Informational, then CVSS descending |
| De-duplication | If a finding appears in multiple objectives, keep one entry at the highest severity observed |
| Unverified findings | Add disclaimer: "This finding was not fully confirmed. Validate before actioning." |
| Missing CVSS | If no CVSS in frontmatter, assign based on severity: Critical=9.0, High=7.5, Medium=5.0, Low=2.5 |
| Empty attack paths | If no PATH-*.md files exist, omit Section 4 and note: "No complete attack paths were documented." |
| Empty timeline | If timeline.jsonl is empty or missing, omit Section 6 and note accordingly |

## Deliverable-Tier Finding Promotion

Operational findings (`findings/FIND-NNN.md` per
`skills/shared/finding-protocol/SKILL.md`) carry the minimum
fields a sub-agent needs to make a decision: id, severity, title,
agent, objective_id, discovered_at, evidence_pointer, plus
Description / Evidence / Next sections. The orchestrator's
final-report pass PROMOTES each operational finding into a
deliverable-tier finding document (`report/<severity><NN>-<slug>.md`,
e.g. `report/critical01-struts-rce.md`) with the
heavyweight schema below — fields the orchestrator can compute from
engagement context that sub-agents could not infer mid-engagement.

Promotion is a step within this skill's report generation workflow.
Skip promotion in any mode where the loaded mode skill (e.g.
`skills/benchmark/SKILL.md`) replaces decepticon.md's
`<COMPLETION_CRITERIA>` Final-response sequence with a mode-specific
terminal behaviour (e.g. SHORT-CIRCUIT for direct credential / target
return). The mode skill specifies the deliverable for that mode.

### Heavyweight Schema (deliverable tier)

Frontmatter fields beyond operational tier:

```yaml
---
id: FIND-001                                # copied from operational
severity: critical                          # copied from operational
title: <one-line summary>                   # copied from operational
cvss_score: 9.8                             # orchestrator-computed from severity + technique
cvss_vector: "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
cvss_version: "4.0"
cwe: [CWE-89]                               # REQUIRED at deliverable tier — orchestrator-derived from technique evidence
vrt: server-side-injection/sql-injection/blind  # Bugcrowd VRT category/sub-category/variant (carries P1–P5 + CVSS/CWE cross-walk)
mitre: [T1190]                              # orchestrator-derived from technique evidence
affected_target: "10.0.0.5"                 # extracted from operational description / evidence
affected_component: "MySQL 5.7 on port 3306"
confidence: verified                        # promoted per Findings Aggregation Rules (verified for any operational finding with 2+ evidence items)
phase: recon                                # orchestrator-inferred from agent field (recon→reconnaissance, exploit→initial-access, postexploit→post-exploitation, etc.)
agent: recon                                # copied from operational
objective_id: OBJ-001                       # copied from operational
discovered_at: "2026-04-06T14:23:11Z"       # copied from operational
detected: null                              # explicit `unknown` if Blue Team visibility not assessed
remediation_priority: immediate             # derived from severity (critical/high → immediate, medium → short-term, low → long-term)
---
```

Body sections — extend operational tier with deliverable-grade content:

- `## Description` — promoted from operational, expanded with engagement-context framing
- `## Steps to Reproduce` — synthesized from evidence pointer files (numbered, copy-pasteable)
- `## Impact` — orchestrator's business-impact framing (operational tier does not have this)
- `## Evidence` — table form, references operational `evidence_pointer` files
- `## Detection` — orchestrator-filled if blue-team visibility data exists; else `Detected by Blue Team: unknown`
- `## Remediation` — orchestrator-derived from CWE / MITRE references
- `## References` — CWE URL + MITRE ATT&CK URL list

### Severity → CVSS-numeric Mapping (when CVSS not pre-assigned)

When the technique evidence does not yield a precise CVSS vector, fall back to severity defaults from Findings Aggregation Rules: Critical=9.0, High=7.5, Medium=5.0, Low=2.5.

### Promotion Algorithm

Deliverable filenames are **severity-sorted and human-readable** —
`report/<severity><NN>-<slug>.md` — because at engagement end severity is
final, no further cross-references are created against these files, and a plain
`ls report/` should list findings worst-first. `<severity>` is the lowercased
label (`critical`/`high`/`medium`/`low`/`info`), `<NN>` is a per-severity
counter (`critical01`, `critical02`, `high01`, …), and `<slug>` is a short
kebab-case form of the title. The operational `id: FIND-NNN` is preserved in
the deliverable frontmatter, so the readable file stays traceable to its
operational finding and to all `finding_id` cross-references.

For each operational `findings/FIND-NNN.md` in `findings/`, sorted by severity
(critical → informational) then discovery order:

1. Read frontmatter and body (operational tier).
2. If severity in {critical, high, medium}:
   - Promote to `report/<severity><NN>-<slug>.md` with the full deliverable
     schema above (keep `id: FIND-NNN` in frontmatter).
3. If severity in {low, informational}:
   - Promote in condensed form (id / title / description / remediation only) per the existing "Low / Informational Findings" rule in Findings Detail (Section 3 of the technical-report template).
4. Update the technical-report.md "Findings Detail" section to reference the
   deliverable `report/<severity><NN>-<slug>.md` path (carrying its `FIND-NNN`
   id), not `findings/FIND-NNN.md`.

### Promotion is One-Way

Operational findings remain in `findings/` (the orchestrator does not mutate them); deliverable findings are generated copies in `report/`. If a sub-agent updates an operational finding mid-engagement (e.g. with additional evidence), the next final-report pass re-promotes the updated operational tier — the deliverable file is regenerated, not patched.

## Quality Checklist

Before writing the final files, verify:

- [ ] Every deliverable finding file (`report/<severity><NN>-<slug>.md`) is referenced in the technical report findings table
- [ ] Every CRITICAL/HIGH finding has `confidence: verified` — if not, add unverified disclaimer
- [ ] Detection gap matrix covers all findings (no FIND-ID omitted)
- [ ] Remediation roadmap has specific, actionable items (not generic "patch the system")
- [ ] Executive summary contains zero technical jargon (no CVE IDs, tool names, protocol names)
- [ ] All MITRE ATT&CK technique IDs follow current ATT&CK format (T followed by 4 digits, optional .xxx sub-technique)
- [ ] Timeline is in chronological order (earliest first)
- [ ] All file paths referenced in the report point to files that exist in the workspace
- [ ] Overall risk rating matches the highest-severity confirmed finding
- [ ] Top 3 findings in the executive summary are the 3 highest-severity verified findings
