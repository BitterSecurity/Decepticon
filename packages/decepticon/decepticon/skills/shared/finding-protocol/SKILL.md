---
name: finding-protocol
description: "Canonical finding template, enriched in place through verification and reporting."
allowed-tools: Read Write
metadata:
  subdomain: reporting
  when_to_use: "write finding, record finding, create finding, document vulnerability, FIND-, findings/, severity"
  tags: finding, protocol, template, severity, reporting, documentation, operational
  upstream_ref: "Operational-tier finding template — sub-agent decision support output, not an attack technique"
---

# Finding Protocol — One Canonical File

Start with the evidence another agent needs to decide what to do next. The
independent verifier then adjudicates the same file, and the reporter completes
it in place. Engagement summaries link to this canonical finding.

## File Naming Convention

`findings/FIND-{NNN}.md`

The file name and the `id` field in YAML frontmatter (FIND-001,
FIND-002, ...) use the same canonical cross-reference. Determine the
next unused sequential ID by inspecting existing `findings/FIND-*.md` names.

Do not create empty scaffold directories or placeholder files before
there is a real artifact to write.

## Initial Template

Every operational finding uses this minimal Markdown structure with
YAML frontmatter — required fields only:

```markdown
---
id: FIND-001
severity: critical
severity_rationale: "Why this severity applies to this target and observed impact"
result_kind: vulnerability # vulnerability | negative_result | observation | hypothesis
verification_status: unverified # independent verifier updates this
report_status: draft # reporter sets complete after read-back checks
title: <one-line summary>
cwe: CWE-89               # optional; only if evidence supports classification
vrt: server-side-injection/sql-injection/blind   # optional Bugcrowd VRT path (category/sub-category/variant)
agent: recon | exploit | postexploit | analyst | ...
objective_id: OBJ-001
discovered_at: "2026-04-06T14:23:11Z"
evidence_pointer: findings/evidence/FIND-001_<slug>.txt
location: http:https://app.example.com/admin/users  # optional but recommended stable locator
---

## Description
2-4 sentences: what the issue is and where.

## Evidence
- <pointer 1>: <one-line per pointer>
- <pointer 2>: <one-line per pointer>


## Verification
Required before a finding is marked confirmed:
- status: `confirmed` or `rejected`
- positive command and discriminating success signal
- equivalent negative-control command and expected baseline signal
- `findings/evidence/FIND-001_verification.json` from `validate_workspace_finding`
- CVSS vector string when confirmed

## Next
next agent should: <action>
OR
blocking — <reason>
```

The `## Next` section is the decision-support hook — the orchestrator
reads it to choose the next dispatch.

## Severity Guide (operational, principle-only)

- **CRITICAL**: Immediate exploitation, data breach, full compromise
- **HIGH**: Known CVE, significant misconfiguration, privilege escalation
- **MEDIUM**: Information disclosure, weak configuration
- **LOW**: Hardening recommendation, informational
- **INFORMATIONAL**: Observation, no direct security impact

When a CVSS score is recorded, always store its vector and version alongside
the numeric score. The reporter uses the deterministic `cvss_score` tool for
CVSS 3.1; never infer a numeric score from a severity label.

## Classification fields (CWE + VRT)

- `cwe` — the CWE identifier (e.g. `CWE-89`). Add only when evidence supports
  the classification; an unknown CWE is more honest than a guessed one.
- `vrt` — Bugcrowd Vulnerability Rating Taxonomy path
  `category/sub-category/variant` (e.g. `server-side-injection/sql-injection/blind`).
  Optional but recommended; it carries a machine-readable cross-walk to CVSS/CWE
  and a P1–P5 priority, and keeps classifications interoperable with bug-bounty
  triage. See the VRT at github.com/bugcrowd/vulnerability-rating-taxonomy.

## Location

`location` is optional but recommended when a finding has a crisp target. It is
a stable, typed `scheme:value` locator for cross-run correlation; do not put
the location only in the description. Omit it for domain-wide policy weaknesses
or findings without a specific locator.

The outer scheme classifies the finding location; its value may itself contain
colons (for example, `http:https://...` or `cloud:aws:iam-role:...`). Write the
outer scheme in lowercase.

Use exactly one of these schemes and normalize the value before writing it:

| Scheme | Value | Normalization |
| --- | --- | --- |
| `http` | URL | Lowercase scheme and host; remove fragment and default port; preserve path and query. |
| `net` | `protocol://host:port` | Lowercase protocol and DNS host; use an IP address as written; include port. |
| `code` | repository-relative path with optional `#L<line>` | Use `/` separators and the repository-relative path. |
| `pkg` | package name with optional `@version` | Use the ecosystem's canonical package name and exact version. |
| `cloud` | `provider:resource-type:resource-id` | Lowercase provider and resource type; preserve the provider resource ID. |
| `identity` | `provider:principal` | Lowercase provider; preserve the canonical principal identifier. |
| `mobile` | `platform:package-or-bundle-id` | Lowercase platform and use the canonical application identifier. |
| `device` | `manufacturer:model[:firmware]` | Use manufacturer and model identifiers; include the exact firmware version when relevant. |

## After Creating a Finding

1. Save raw evidence to `findings/evidence/FIND-{NNN}_{description}.txt`
   only when it supports the finding.
2. Append a timeline entry to `timeline.jsonl` for the real finding event:
   `{"ts":"...","type":"finding","id":"FIND-001","severity":"critical","agent":"recon","objective":"OBJ-001"}`

## Rules

- One Markdown file per finding — do NOT bundle multiple vulnerabilities
- ALL agent documents use Markdown format — never write JSON as a deliverable document
- Do NOT create `findings.md`; each finding lives in its own `findings/FIND-{NNN}.md` file

## Verification and reporting lifecycle

The first author records observed facts and evidence under the stable
`findings/FIND-{NNN}.md` name with `report_status: draft`. A distinct verifier
reproduces the claim and a negative control, then writes
`verification_status: verified | false_positive | unverified` and
`verification_rationale` into this same file. The reporter adds supported
detail, uses `cvss_score` for justified CVSS 3.1 metrics, reads the saved file
and evidence back, then sets `report_status: complete`. The orchestrator links
to this file from both engagement summaries. It never copies it into `report/`.
