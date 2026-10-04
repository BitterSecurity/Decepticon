---
name: finding-report
description: "Complete an independently verified finding in its canonical file."
allowed-tools: Read Write
metadata:
  subdomain: reporting
  when_to_use: "report one finding, CVSS, verification verdict, remediation, FIND-"
  tags: finding, reporting, verification, cvss, evidence
  upstream_ref: "Canonical Decepticon finding verification and reporting workflow"
  skillogy:
    version: 1
    requires: [finding-protocol]
---

# Finding report

Read `findings/FIND-NNN.md`, its evidence references and the verifier's
`verification_status` and `verification_rationale`. Work in that same file.

## Verdict handling

| Verdict | Treatment |
| --- | --- |
| `verified` | Describe what was reproduced. For `result_kind: vulnerability`, score only with a justified `cvss_score` call and record its `base_score`. A verified negative result remains coverage, not a vulnerability. |
| `unverified` | Explain the constraint and the next discriminating test. Do not assert the claimed impact as fact. |
| `false_positive` | Preserve the rebuttal for coverage accounting. Do not assign a vulnerability score or remediation priority. |

For `result_kind: negative_result` or `observation`, report what was tested or
seen and what it does **not** prove. Do not turn it into a vulnerability.

## Canonical document

Preserve all existing frontmatter, including `id`, `objective_id`, target,
`evidence_pointer`, `evidence_manifest`, `result_kind`, verdict and rationale.
Complete supported fields such as `severity_rationale`, `cvss_vector`,
`cvss_score`, `cvss_version`, `cwe`, `mitre`, `remediation_priority`, and
`report_status`. Unknown values may remain absent. Use sections for Summary,
Verification, Steps to Reproduce, Proof of Concept, Attack Scenario, Impact,
Controls and Limitations, CVSS, Remediation, and References. Cite exact paths.
The agent must read back the saved file and evidence before marking it complete.

The final engagement report links to `findings/FIND-NNN.md`; it never copies it.
