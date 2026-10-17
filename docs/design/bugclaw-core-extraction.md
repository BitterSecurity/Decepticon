# BugClaw practices absorbed into core

Reviewed sources: SaaS `apps/bugclaw/bugclaw/prompts/bugclaw_recon.md`,
`bugclaw_hunter.md`, `bugclaw_validator.md`, `bugclaw_orchestrator.md`; their four
matching `agents/*.py` factories; and bug-bounty `validation-proof-bar` and
`cvss-scoping` skills. This review distinguishes implementation contracts from
prompt claims rather than treating existing prose as authoritative.

## Implementation findings

Recon and hunter wrap OSS `create_recon_agent` and `create_exploit_agent`, register
role-specific skill ACLs, and replace the static system prompt while retaining
runtime middleware. Validator wraps the separate plugin `create_verifier_agent`,
uses verifier slots, analyst/shared/verifier skill sources and an exploit-model
fallback. It is not the standard `finding_verifier` factory. All three set a 1000
recursion limit; that operational budget is not a capability rule to embed in core.
The orchestrator owns curated persona registration and dynamic task dispatch; its
catalog exposes recon/hunter/validator names while reusing other OSS specialists.
This is an adapter concern, not a reason to fork specialist reasoning identities.

A prompt replacement preserves middleware injection but removes stable base-prompt
rules. Workflow overlays instead allow the base capability and its tool contract
to remain intact. Production role names need explicit adapter bindings to OSS role
names; no implicit string guessing belongs in a core prompt.

## Extraction decisions

| Reviewed behavior | Destination | Adaptation and reason |
| --- | --- | --- |
| Recon records exact `where` for endpoints, identities and flows | Core recon; technical recon methods | Every observation has a response/source/DNS/browser locator. Distinguish an existing boundary from a broken one and a reflected value from execution. |
| Concrete hosts versus wildcard zones; no invented localhost target | Core recon | Names remain candidates until evidence establishes resolution/reachability. A failed guessed label does not exhaust a zone. Do not assume every task is production web; honor the supplied environment. |
| Recon target-model JSON, mandatory final write, program brief first | Workflow/adapter artifact contract | Bounty-specific paths and sequencing are not universal recon identity. Core preserves its SUMMARY.md contract; adapters may request an additional structured model. |
| Mechanism hypothesis, benign alternative, minimal oracle/control | Core decepticon and exploit | Dispatch a testable mechanism, not just a class label. Failed controls revise the hypothesis; variant search requires shared-cause evidence. No mandatory HYP numbering in core. |
| Hunter stops at exactly one PoC with no C2 or lateral activity | Bug-bounty workflow/RoE | Stop at the assigned proof boundary. Core remains usable for other authorized objectives; one PoC does not terminate unrelated assessment coverage. |
| Reusable executable probes and prior-attempt context | Core exploit and finding_verifier | Save reproducible inputs/environment and review received scripts before execution. Independence means an independent judgment, not rebuilding the hunt from scratch. |
| Validator challenges positive and negative conclusions | Core finding_verifier | Attempt the dispatched claim, including refuted/negative candidates. Fresh proof can overturn a false negative with explicit provenance and result-kind correction. |
| Positive oracle plus discriminating control | Core exploit/verifier; technical exploit methods | A positive must actually occur. Control must challenge the benign explanation. Timing/race results need repeated matched trials, not a claim of perfect determinism. |
| Per-class observables | Technical exploit methods and existing technique skills | Browser execution is not HTTP reflection; controlled identities prove access boundaries. In-band SSRF need not use OOB. HTTP error differences alone do not establish impact. |
| Program eligibility folded into VALID/INVALID | Bug-bounty workflow, corrected | Technical validity and program eligibility are separate. A real excluded issue is not a false positive. A timeout or inability to reproduce is not proof of absence. |
| PROVEN/PLAUSIBLE chain and business impact | Core evidence principles plus workflow synthesis | Preconditions and measured effects stay explicit. Untested downstream links cannot inflate confirmed impact, and analysis never authorizes further execution. |
| Bounty dedupe, chain-composer dispatch, drafts and external submission rules | Bug-bounty workflow/adapter | Use supplied prior-report evidence and compatible proven primitives. No auto-submission and no forced chain when primitives do not connect. |
| CVSS class-based caps, policy downgrades, compulsory vector | Not copied | Retain deterministic scoring of justified vectors and separate business eligibility. Do not mechanically equate cross-account read with C:H, blind evidence with C:L, or policy floor with a technical metric. |
| Three failed payloads mean endpoint hardened; rate refusal means invalid | Not copied | Stop based on information gain and budget. Failed probes and policy blocks retain uncertainty; they do not prove a target secure. |
| Fixed bash wedge recovery and tool preference | Existing injected bash contract | Do not duplicate potentially stale session arguments or require HTTP/browser tools that the current role may not expose. |

## Skills versus workflows

All 16 new specialist-methods files were individually reviewed and authored as
technical prerequisites, tool/environment interpretation and evidence limits.
They contain no OPPLAN startup, dispatch sequence, universal credential/lateral
loop, campaign rollout, handoff ceremony or report-status lifecycle. Existing
technique skills are reused for implementation detail. The new method references
are not renamed copies of old operational workflows.

The four WORKFLOW.md catalogs own startup/context, scope-dependent stage selection,
quality dependencies, aggregate artifacts and engagement close. Bug-bounty owns
program-policy, eligibility, prior-report screening and submission boundaries.
Red-team owns conditional access expansion and cleanup; pentest owns coverage and
remediation synthesis; vulnerability-research owns version/root-cause/reproducer
questions. Provided code, PoC or captures can satisfy discovery. Missing evidence
adds a discovery prerequisite; proof → independent verification → report is retained.

## Modified core agent inventory

Nineteen standard prompts changed: `decepticon`, `recon`, `exploit`, `postexploit`,
`analyst`, `cloud_hunter`, `ad_operator`, `reverser`, `contract_auditor`,
`finding_verifier`, `finding_reporter`, `forensicator`, `ics_operator`, `iot_operator`,
`mobile_operator`, `osint_operator`, `phisher`, `supply_chain_operator`,
`wireless_operator`. Each retained its domain identity, actual environment/tool
contracts and authorization boundaries. The detailed role-by-role rationale is in
core-prompt-workflow-audit.md. Soundwave, Autohunt and Blue Cell remain unchanged
because their selected planning/defensive adapters have distinct contracts.

## Existing orchestration skill review

The review also found that preexisting orchestration skills could reintroduce the
same fixed process after a clean core prompt was loaded. `engagement-startup` and
`engagement-lifecycle` were retired from the skill catalog with MIGRATION.md notes preserving their
ownership mapping; their applicable resume,
missing-authorization, abort/deconfliction and cleanup procedures now live in the
workflow catalogs. No remaining skill references require those removed IDs.
`orchestration` now covers transferable context/evidence interpretation;
`kill-chain-analysis` covers causal prerequisites and confidence rather than a
credentials-first or crown-jewel-first execution order; `pentest-task-tree` retains
hypothesis-tree modeling without a second session lifecycle or execution plan.
`final-report` retains detailed optional writing templates, aggregation semantics
and quality interpretation while removing its universal two-file reporting process,
mandatory startup paths and separate finding lifecycle.

## Capability selection within stages

Catalog stages declare a primary specialist plus allowed alternatives. Discovery can
use recon or a relevant passive/domain investigator; proof can use cloud, AD, source,
binary, contract, mobile or other suitable capabilities. Stage purpose and acceptance
artifacts remain stable across those choices. Red-team now has a unified prove stage
including postexploit for supplied access, rather than obligatory initial-access and
post-access phases. Independent finding_verifier and finding_reporter remain dedicated
quality stages. Bounty technique exclusions still come from policy/RoE, not from
assuming all capabilities of an allowed specialist are authorized.

Retirement deployment note: copying the current skill tree does not remove old nodes
from a preseeded Skillogy database. A production deployment must remove/suppress the
retired engagement-startup and engagement-lifecycle IDs (or ingest an explicit
retirement record). Their MIGRATION.md files are informational and are deliberately
not new executable SKILL.md assets. This catalog change alone does not claim that
an already-running external brain has been synchronized.

## Final BugClaw source-skill classification

All eight original files were read individually. Seven stable skill identities are
retained with versioned Skillogy metadata; startup becomes a non-indexed migration
record. No technical specialist is required to load a business-stage sequence.

| Original skill | Retained reusable knowledge | Procedure destination / correction |
|---|---|---|
| bug-bounty-startup | None: exclusively launch and orchestration procedure | MIGRATION.md; bounty Decepticon section owns seeded launch checks, objectives, dispatch and review. Existing exact-path catalog retirement also blocks persisted copies. |
| program-policy-interpretation | Asset-selector semantics, independent authorization/submission/reward dimensions, policy conflicts and provenance | Bounty Decepticon owns first-turn reads and scope artifact. Eligibility flags no longer automatically determine authorization or technical validity. |
| cwe-bounty-roi | Reward-table fields, sampling bias, uncertain/correlated estimates and effort inputs | Bounty Decepticon owns budget prioritization. Removed universal payout ranking, unconditional crown-jewel priority and low-reward evidence suppression. |
| dedup-against-disclosed | Mechanism/component/version/privilege signatures, variant/regression comparison and corpus confidence | Bounty Decepticon owns duplicate disposition and human-review routing. No-match does not mean globally novel or submittable. |
| platform-severity-floors | Policy-vs-impact distinction and context-dependent evidence gaps | Bounty workflow owns eligibility decisions. Removed universal kill list and validity-ratio pressure. |
| submission-protocol | Portable report field meanings, taxonomy mapping, attachment and destination-schema fidelity | Bounty reporter owns preparation/review/export boundaries. No universal title/attachment limits or platform-version assumptions remain in the reference. |
| cvss-scoping | Versioned metric concepts, compatible calculator use, evidence limits and provenance | Bounty reporter owns scoring/report lifecycle. Removed universal side-channel cap, automatic C:H for any cross-account read and destructive-proof implication. |
| validation-proof-bar | Detailed class-specific oracle/control matrix, benign alternatives, noisy-signal interpretation and bounded negative conclusions | Independent assignment/lifecycle is in workflow. Missing callbacks are inconclusive without additional evidence; program exclusion does not refute a mechanism. |

The selected bounty workflow retains supplied-code/PoC discovery bypass and mandatory
proof → independent verification → reporting dependencies. Economic prioritization,
prior-report screening, disposition and external-submission boundaries are conditional
business instructions in that workflow, not requirements installed in core expertise.
Technical CVSS wording was checked against the [FIRST CVSS 3.1 specification](https://www.first.org/cvss/v3.1/specification-document).

Verification compiles the actual seven source skills through the Skillogy builder
and protocol compiler, checks catalog identities and retired-path exclusion, and
runs BugClaw agent/CVSS tests plus workflow/prompt and Skillogy regression suites.
