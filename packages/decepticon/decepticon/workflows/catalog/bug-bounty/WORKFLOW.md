---
id: bug-bounty
version: "1"
description: Program-scoped vulnerability discovery and submission-ready evidence.
stages:
  - id: scope
    specialist: decepticon
    depends_on: []
    required_artifacts:
      - plan/program-scope.md
  - id: discover
    specialist: recon
    specialists: [osint_operator, analyst, cloud_hunter, ad_operator, reverser, mobile_operator, contract_auditor, iot_operator, wireless_operator, supply_chain_operator]
    depends_on: [scope]
    required_artifacts:
      - recon/SUMMARY.md
  - id: validate
    specialist: exploit
    specialists: [analyst, cloud_hunter, ad_operator, reverser, mobile_operator, contract_auditor, iot_operator, wireless_operator, supply_chain_operator]
    depends_on: [scope]
    required_artifacts:
      - findings/coverage.md
  - id: verify
    specialist: finding_verifier
    depends_on: [validate]
    required_artifacts:
      - findings/verification-summary.md
  - id: report
    specialist: finding_reporter
    depends_on: [verify]
    required_artifacts:
      - report/submission-summary.md
---

## Common

Use the approved RoE and bound workspace. This workflow selects work; it does
not authorize targets, techniques, data access, persistence or external submission.
Read the live task schema and injected OPPLAN instructions for dispatch and plan
mutation. Translate stages into bounded objectives with the declared dependencies,
then refine them using actual evidence. A stage ID is a template label, not a
server-issued objective ID. Required artifacts are acceptance requirements, not
proof that work has happened. Do not mark a stage complete because a file exists:
read it and assess the criterion and its underlying evidence.

Stage purpose determines the work. The primary `specialist` and additional
`specialists` are eligible capabilities, not a required sequence or authorization.
Choose one available role suited to the target and evidence; every chosen role
fulfills the same stage acceptance and artifact contract. Domain-specific examples
below do not exclude declared alternatives. A discovery assignment collects the
missing surface/prerequisite evidence, a research assignment tests the relevant
mechanism, and a proof assignment produces the bounded positive/control evidence
or an explicit negative/blocker. Apply the stage's required artifact paths even
when a different eligible specialist performs the work. Verification remains a
separate finding_verifier assignment; the proof producer cannot self-approve.

Create one canonical `findings/FIND-NNN.md` per supported finding with
`result_kind: vulnerability`, `observation`, or `negative_result` as appropriate.
Start `verification_status: unverified` and `report_status: draft`. Preserve raw
positive and negative-control evidence, commands, target/session context and
limitations. Never promote a scanner hit, a version match or a graph path into
confirmed impact. Meaningful negative results count as coverage, not vulnerabilities.
No-findings is a valid outcome. Record blocked and untested coverage separately.

## Agent: decepticon

Bind the workspace, read RoE, and create/review the dependency plan using injected
OPPLAN instructions. Approved direct engagements need not rerun Soundwave;
planning documents other than the required RoE are optional unless this engagement
requires them. Dispatch status-ready work with scope, acceptance criteria,
evidence paths, previous attempts, budgets and credential artifact references.
Recheck actual prerequisite evidence and scope independently of readiness.
Inspect each result before recording acceptance or a blocker in OPPLAN.
Use the injected workspace and runtime plan revision when resuming. Missing required
RoE blocks target-facing work; do not create or repair authorization documents from
an operational role. Optional planning documents do not block an approved direct run.
On an abort instruction, stop dispatch, use the run's stop control for active work,
and preserve in-progress objectives, deployed state and evidence for safe resumption.

Discovery is an evidence-acquisition branch, not a mandatory first attack phase.
When supplied source, a PoC or existing captures already establish the needed
surface and prerequisites, record their provenance and omit/cancel redundant
discovery with a reason. Do not fabricate recon/SUMMARY.md to satisfy a skipped
branch. If proof needs missing surface evidence, add the discover objective as a
prerequisite before dispatch. Preserve the proof → independent verification →
report dependencies even when discovery is bypassed.

Instantiate one verifier objective per candidate finding and a dependent reporter
objective for the same canonical file. The verification/report stages may need
multiple such leaf objectives; an empty finding set instead produces an explicit
coverage artifact. You own findings/verification-summary.md and the aggregate
report artifacts listed by the final stage; the reporter edits canonical findings
only. Summarize each finding verdict with evidence links and explicitly record a
zero-candidate result. Retain rejected and unverified findings in limitations. Before
close, account for every objective as completed, blocked or cancelled with reasons,
review the final evidence and requested artifacts, and stop unneeded workloads via
`ops_cleanup_engagement()` when available. Return artifact paths and limitations.

Record program assets, exclusions, permitted testing, disclosure constraints and
submission requirements in plan/program-scope.md from the supplied authoritative
brief. If the launcher supplied bugbounty/program-brief.md, policy.md or program.json,
use that context with plan/roe.json; do not choose or seed another program. Missing
required launch scope is a blocker, not permission to invent targets. Do not expand scope from adjacent domains. Route source, cloud, AD or binary
questions to the relevant specialist when evidence calls for it. Optimize for
reproducible eligible impact, not finding count. Further exploitation stops at the
program's proof/data boundary. Produce report/submission-summary.md with canonical
finding links, eligibility caveats, duplicates/prior-art concerns, tested negatives
and untested scope. Submission-ready does not mean externally submitted.

Record mechanism hypotheses with observation/evidence, suspected boundary or data
transformation, competing benign explanation, minimal oracle/control and relevant
variant surfaces. Update supported, refuted and unresolved outcomes from actual
proofs. Include negative candidate findings in independent verification so a prior
hunter conclusion cannot suppress a false negative. Keep technical verdict separate
from program eligibility, severity floors and dedupe confidence. Known-report
screening uses supplied disclosure evidence; absent evidence means unknown, not unique.

Where multiple verified primitives may compose, assess compatible preconditions,
identities, state and boundary crossings before reporting a combined impact. Label
links proven or plausible, and do not raise confirmed severity from an untested
connection. Retain solo findings when no supported connection exists. The report
must distinguish actual scope/impact from hypothetical downstream damage. No live
chain execution or external submission follows automatically from this analysis.

For a BugClaw launch, validate the seeded plan/roe.json, bugbounty/program-brief.md
and bugbounty/program.json before target-facing dispatch; read optional policy.md
when present. Missing or malformed required seed data blocks the run for reseeding.
The selected program is fixed by the launcher: do not call seed_scope, choose a
replacement program or synthesize missing authorization. No CONOPS/deconfliction
interview is required by this bounty workflow. Record policy provenance, asset
selectors, technique/data/rate restrictions, submission eligibility, reward eligibility
and disclosure/evidence requirements in plan/program-scope.md. Keep those dimensions
separate. Review objective readiness and scope against the live OPPLAN schema before
delegation; assign only the missing evidence work. The optional discovery branch and
supplied-code/PoC bypass still apply.

Within the approved budget, prioritize questions by plausible security impact,
evidence quality, unresolved coverage, setup cost and supplied program acceptance
criteria. Reward bands and duplicate risk can inform effort allocation, but record
their uncertainty; do not invent reward probabilities or use a universal CWE payout
ranking. Do not erase valid evidence because reward is low. Stop a line when the
proof/data boundary is reached or further work cannot distinguish the hypotheses
within the budget, and record the tested negative or blocker. Persistence, lateral
movement, social engineering, destructive/load testing and other expanded techniques
are not implied by a bounty objective; they require explicit applicable authorization.

After independent verification, compare supplied prior reports by mechanism,
component/version, privilege and supported impact. Exact overlap is a duplicate
assessment, not a false-positive verdict; retain the canonical evidence and cite the
prior report. Partial overlap, regressions or supported new impact merit a documented
variant assessment. Missing history means unknown novelty. Use synchronized context
and local findings; do not query external platform accounts from the agent graph.
Record eligible, ineligible, duplicate or uncertain disposition separately in the
submission summary. Complete the internal record for verified evidence even when
no external submission is recommended. Flag uncertain policy/novelty for human review;
no-match alone does not establish submission readiness. Cross-finding analysis is
conditional on compatible evidence, not a required escalation or mandatory extra PoC.

## Agent: finding_verifier

Independently reproduce the dispatched claim with a suitable negative control.
Use `validate_workspace_finding` when appropriate and save raw results. Preserve
existing keys/body, record `verification_status` and `verification_rationale` in
the canonical file. `verified` on a negative result means reproduced coverage;
`false_positive` means refuted; `unverified` means insufficient or blocked evidence.
Assess program eligibility against the supplied policy separately from the technical
verdict. Record excluded class/asset or duplicate evidence with the exact policy/source
reference; a technically verified issue remains technically verified even when it
cannot be submitted. Do not use eligibility rules to rewrite technical CVSS metrics.

## Agent: finding_reporter

Read the canonical finding, verdict and referenced evidence. Complete reproduction,
impact, uncertainty, remediation and references without changing the verdict.
Use `cvss_score` for justified vectors; do not score negative or informational
results as vulnerabilities. Run `check_finding_report` and resolve errors before
setting `report_status: complete`. Keep one canonical file; report/ is synthesis.

Keep `report_status: draft` while editing. Use bounded incremental edits, preserve
the independent verdict and read the final saved document and cited artifacts.
Resolve invalid YAML, unsupported claims and checker errors before the final
completion edit. Return the finding ID, canonical path, verdict and remaining
limits to the orchestrator. Missing optional threat-model documents are evidence
context gaps, not an automatic reporting blocker. Refuted claims remain in the
internal coverage record rather than becoming deliverable vulnerabilities.

Prepare the draft using the supplied destination schema: map the supported CWE,
affected approved asset, requested versioned severity, reproduction, impact and
attachments without inventing platform field limits. For the BugClaw report contract,
use the supported CVSS 3.1 calculator where scoring applies; another destination's
version requirements must be explicit and must not mix metrics. Include required
visual proof, reproducer commits or account setup only when the program requests
them and the evidence exists. Redact secrets while preserving reproducibility.
Separate technical verification, eligibility and novelty; retain rejected hypotheses
and coverage in the aggregate summary rather than presenting them as vulnerabilities.

The workflow produces an internal reviewable draft and evidence. Respect supplied
disclosure conditions and encryption requirements when preparing export artifacts.
A completed canonical report does not authorize remote draft creation, account
connection, transmission or external submission. External submission remains a
separate explicitly authorized human action; never infer it from report_status.

## Agent: recon

Choose the missing evidence work from supplied artifacts, passive sources and
authorized active probes. Their order follows technical prerequisites and the
program budget. Persist partial observations before a budget, rate-limit or access
blocker ends the assignment; uniform responses do not establish complete coverage.

Collect the scoped input/authentication/endpoint inventory and retain evidence in
recon/SUMMARY.md. Discovery breadth follows program coverage and budget, not the
first interesting response. Hand off observations and credential artifact paths.

## Agent: exploit

Validate candidate mechanisms with bounded positive proofs and negative controls.
Do not expand access just to increase severity. Save findable raw evidence and
findings/coverage.md including failed hypotheses, blockers and tested boundaries.
