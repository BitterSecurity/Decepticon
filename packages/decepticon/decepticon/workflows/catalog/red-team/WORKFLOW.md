---
id: red-team
version: "1"
description: Objective-led adversary simulation with authorized access paths and cleanup.
stages:
  - id: scope
    specialist: decepticon
    depends_on: []
    required_artifacts:
      - plan/mission.md
  - id: discover
    specialist: recon
    specialists: [osint_operator, analyst, cloud_hunter, ad_operator, reverser, mobile_operator, contract_auditor, iot_operator, wireless_operator, supply_chain_operator]
    depends_on: [scope]
    required_artifacts:
      - recon/SUMMARY.md
  - id: prove
    specialist: exploit
    specialists: [analyst, cloud_hunter, ad_operator, reverser, mobile_operator, contract_auditor, iot_operator, wireless_operator, supply_chain_operator, postexploit, phisher, ics_operator, forensicator]
    depends_on: [scope]
    required_artifacts:
      - findings/coverage.md
      - findings/objective-results.md
  - id: verify
    specialist: finding_verifier
    depends_on: [prove]
    required_artifacts:
      - findings/verification-summary.md
  - id: report
    specialist: finding_reporter
    depends_on: [verify]
    required_artifacts:
      - report/executive-summary.md
      - report/technical-report.md
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

Record mission success criteria, permitted techniques, OPSEC, deconfliction and
cleanup obligations in plan/mission.md. Model attack paths only toward actual
mission goals. C2, persistence, credential access and lateral movement are
conditional on authorization and objective need, not automatic consequences of
a foothold. The prove stage covers the actual mission question: it may establish
an initial effect, validate supplied access, test a cloud/AD/source/binary mechanism,
or assess an authorized post-access objective. Supplied access does not require
another initial-access attempt. Create only objectives needed for the mission;
additional expansion is not a mandatory prerequisite of independent verification.
Record objective results and cleanup/access state in findings/objective-results.md,
including negative or blocked outcomes when the mission cannot be demonstrated.
Follow specified deconfliction contacts/windows before restricted actions. Record
real activity timestamps, observed defender response and cleanup obligations. Any
MTTD or dwell-time metric needs attributable events and aligned clocks; unavailable
telemetry remains a limitation. At close, account for deployed artifacts, sessions
and modifications and their removal or authorized retention.
Synthesize report/executive-summary.md and report/technical-report.md from canonical
findings, mission outcomes, tested paths, defender visibility and cleanup state.

When an adversary-emulation profile is explicitly selected, derive the campaign
from the signed mission and supplied profile. Actor skills supply technique
knowledge, not a mandatory campaign graph. Candidate planning patterns are:

| Profile | Conditional campaign pattern |
|---|---|
| APT28 | discovery, entry, credential access, lateral access and mission collection |
| APT29 | tenant/trust discovery, entry, credential material and identity-based access |
| APT41 | exposed application access, persistence, credentials and mission collection |
| APT44 | approved lab access, propagation and separately authorized destructive simulation |
| Lazarus | developer/build access, supply-chain trust and explicitly scoped mission effects |

Instantiate only requested, authorized links with their actual prerequisites;
existing access can replace entry work. Record scenario-specific success criteria,
OPSEC and C2 timing in plan/mission.md and OPPLAN instead of inheriting arbitrary
host counts, mailbox extraction, dwell times or impact thresholds from a skill.
Credential access, persistence, lateral movement and collection each retain their
own authorization boundary. APT44 destructive activity remains restricted to the
explicit expendable lab scenario; no production or cyberphysical action follows
from selecting that profile. Use specialist delegation with the live task schema,
record observed measurement results and preserve blockers instead of silently
skipping unauthorized work. Browser observations are appropriate when an in-scope
interactive interface is relevant, not a compulsory first action for every task.

For an explicitly assigned detection/response exercise, add the necessary
defensive validation objectives beside the offensive proof. A detector can derive
a rule from observed activity; an available, authorized defender can publish it
through the configured SIEM integration; forensic analysis checks actual matching
telemetry. No matches can mean missing coverage or an unsuitable rule, not proof
of evasion. Revise the hypothesis/rule or record the limitation. Add patch and
regression work only when the mission requests remediation. ICS function-code,
MES/SCADA-source and project-download rules, supply-chain Sigma/linter/SLSA outputs,
and wireless observations are conditional domain deliverables, not mandatory
stages of every engagement. Never assume detector, defender, publishing access or
physical hardware exists merely because a skill mentions it.

## Agent: finding_verifier

Independently reproduce the dispatched claim with a suitable negative control.
Use `validate_workspace_finding` when appropriate and save raw results. Preserve
existing keys/body, record `verification_status` and `verification_rationale` in
the canonical file. `verified` on a negative result means reproduced coverage;
`false_positive` means refuted; `unverified` means insufficient or blocked evidence.

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

## Agent: recon

Choose the missing evidence work from supplied artifacts, passive sources and
authorized active probes. Their order follows technical prerequisites and the
mission budget. Persist partial observations before a budget, rate-limit or access
blocker ends the assignment; uniform responses do not establish complete coverage.

Map the mission-relevant surface and prerequisite observations; save recon/SUMMARY.md.

## Agent: exploit

Test the authorized mission mechanism. Save proof-of-access only when that is the
assigned question; otherwise capture its actual success oracle. Save failed attempts,
boundaries and objective results in findings/coverage.md and findings/objective-results.md. C2 deployment
requires a separately authorized objective and available workload.

## Agent: postexploit

Pursue only assigned mission objectives from a proven foothold. Record each hop's
identity, host, privileges, evidence and detection observations. Save objective
results, unresolved access and cleanup actions in findings/objective-results.md.
Do not treat unobserved defender telemetry as proof of evasion.

## Agent: phisher

Before any send, obtain the approved blue-team contact, channel, availability and
acknowledgment method from the authorized plan. Prepare the campaign identifier,
send window, target count/population, pretext, lure domain/URL and opt-out details.
Use only the configured, authorized contact method to request acknowledgment with
an explicit response time and hard deadline. Poll at the agreed interval until
acknowledgment or deadline. Record sent time/channel/recipient and the actual
acknowledgment time, message ID and text in plan/phisher/deconfliction-log.md and
the required deconfliction_ack knowledge-graph record before sending.

Missing, denied, unreachable or late acknowledgment blocks sending. Request an
alternate contact or permitted replanning through the operator-input mechanism;
a disposition is not a substitute for acknowledgment. Changed targets or windows
require renewed acknowledgment. Apply the engagement header, opt-out and shutdown
obligations, including the existing five-minute stop boundary where applicable.
Retain a blocked result when the contact or evidence system is unavailable.
