# Runtime workflow authoring

Core prompts describe stable capabilities, scope boundaries, tool contracts and
truthful evidence handling. Skillogy SKILL.md assets describe reusable technical
methods. WORKFLOW.md documents describe business intent, stage dependencies,
acceptance artifacts and role-specific instructions. A workflow never grants RoE
permission or bypasses tool enforcement.

## Catalog and selection

Builtins live in `packages/decepticon/decepticon/workflows/catalog/<id>/WORKFLOW.md`:
`red-team` (default), `bug-bounty`, `pentest`, and `vulnerability-research`.
Operators can register another catalog root with `DECEPTICON_WORKFLOW_DIR`.
Callers select a registered ID through `config.configurable.workflow_id` and may
pin `workflow_version`. Do not pass a caller-supplied file path as a workflow ID.
Duplicate ID/version registrations, unknown IDs, and ambiguous versions fail.

The middleware records the complete content, ID, version and SHA-256 as a
checkpoint snapshot. Resumes retain that content even when a catalog file changes;
a conflicting requested ID/version must fail. Start a new run to select a different
workflow. Skills and target artifacts are evidence/method sources, not selectors.

## Document format

```yaml
---
id: focused-assessment
version: "1"
description: Assess an authorized surface with independent validation.
stages:
  - id: assess
    specialist: recon
    specialists: [osint_operator, cloud_hunter]
    depends_on: []
    required_artifacts:
      - recon/SUMMARY.md
  - id: verify
    specialist: finding_verifier
    depends_on: [assess]
    required_artifacts:
      - findings/verification-summary.md
---
```

Follow frontmatter with `## Common` and any `## Agent: <registered_role>` sections.
Each section may appear once. Put no unscoped prose before the first section; use
third-level headings within sections. Quote the version as a string. Stage IDs
must be unique, prerequisites must exist, and cycles are rejected. `specialist` names
the primary capability; optional `specialists` names allowed alternatives. All
eligible roles fulfill the same stage purpose and artifact contract. Select a role
by the actual target and available evidence rather than forcing every proof through
exploit. An allowed role is not permission for its entire technique repertoire. The parser
validates syntax; authors must also check specialist names against the live catalog.

Common text reaches every role. Role text reaches only that role; orchestration
receives all stage metadata, while specialists receive metadata for every stage that allows their role.
Avoid putting orchestrator procedures in Common. Keep a specialist's local method
in SKILL.md rather than duplicating it into every workflow.

## Stage and evidence semantics

Stages are templates for OPPLAN objectives, not a second execution engine. Use the
injected OPPLAN tools and live task schema; never assume a template stage ID is a
server-issued objective ID. Expand verification/reporting into one leaf objective
per canonical finding, with reporter depending on verifier. Prerequisite completion
is not authorization. Inspect the actual evidence before marking an objective done.

Discovery is conditional evidence acquisition. If a supplied source tree, PoC or
capture already establishes the required surface, record its provenance and omit
or cancel redundant discovery with a reason. If the proof needs missing evidence,
add discovery as an OPPLAN prerequisite. Never bypass proof → independent
verification → report, or write an invented discovery artifact for an omitted stage.

Define who produces aggregate artifacts. The orchestrator owns coverage synthesis,
verification summaries and engagement reports; specialists own dispatched evidence
and canonical findings. With zero candidates, verification/report stages produce
explicit no-findings coverage summaries instead of fabricated findings. If a stage
cannot run, record a blocker or revise the plan and retain untested coverage; do
not claim that a missing artifact or cancelled stage passed.

Preserve `result_kind` distinctions: vulnerability, observation, negative_result.
Independent verification can mark a negative_result verified without converting it
into a vulnerability. Retain false_positive and unverified claims as coverage or
limitations. Numeric CVSS comes from a justified vector and scoring tool. A complete
report is readable and checked; it does not assert remediation or a new verification.

## Extension and review

Add a catalog directory and WORKFLOW.md; register it server-side. No core prompt
edit is needed to add a business workflow. Reuse existing specialists and Skillogy
methods, adding a specialist only for a genuinely new capability. Validate parsing,
DAG dependencies, role isolation, checkpoint/resume behavior, and actual runtime
selection. Run `uv run pytest packages/decepticon/tests/unit/prompts
packages/decepticon/tests/unit/workflows -q` and inspect captured evidence.

Soundwave remains an explicitly selected planning adapter with its existing planning
fragment. Autohunt remains its conservative bootstrap alternative. Neither is an
operational business workflow silently appended to core specialists.

Red-team uses a unified `prove` stage. Its eligible roles include postexploit,
cloud, AD, source and binary capabilities as well as exploit. Supplied footholds
can therefore be evaluated directly; initial access and further expansion are not
mandatory separate phases. Verification depends on proof, and reporting depends on
verification. Dedicated verification/report roles have no proof-producer alternatives.
