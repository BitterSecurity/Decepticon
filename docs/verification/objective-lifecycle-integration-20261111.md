# Objective lifecycle integration review — 2026-11-11

For [#1174](https://github.com/BitterSecurity/Decepticon/pull/1174), review
original head`9f35a68b` against current main, then combine #1170 and #1173.
Tested source`0d253a95` and integration commit`6b7d15c88c704a4c017bd6650fa0479888e21a8b`
have the same Git tree. The actual #1173 merge is main`16c6b66d`.
[Complete paths, process hashes and wheel/codec receipts](objective-lifecycle-integration-20261111.json).

## Review and integration correction

Review all12 original paths together: immutable core transition/classification
tables, compatibility helpers, OPPLAN mutation/refusal, dependency inspection,
dispatch readiness, workflow gates and search policy. Requested transitions
match the old string tables. Completed/cancelled remain final for ordinary
requests; evidence invalidation still blocks affected objectives for rework.
Parents cannot complete with unresolved children. The improved final-status
message does not mutate the persisted objective on refusal.

`SearchObjective.status` changes from a Literal to the existing string enum.
Membership, JSON values and restored legacy plain-string state are checked
explicitly below. This does change the schema's representation to an enum
reference; no new state value or permission is introduced. SearchObjective is
the existing search adapter, not a replacement graph-state or authorization model.

Both PRs independently extend the public API snapshot. Resolve its expected
count as **97 unique entries**—all eight objective additions plus both host
config additions—rather than choosing either side's95or89. Both changelog
histories are retained. No runtime rule is relaxed to resolve the conflict.

## Direct qualification

Fresh locked source environment, production credentials/tracing excluded:

- Full fast suite: **6,347passed /3explicit Windows-only skips /17warnings /
  process0**, including existing OPPLAN, search, workflow and checkpoint tests.
- Full Ruff check/format pass. Raw basedpyright:0errors/969retained warnings,
  process0. The lockfile does not change from #1173.
- Three actual wheels built through the shared build slot; RECORD and installed
  payload hashes verified. Seven isolated public host/provider profiles reuse
  the #1173 contract checks against these new actual packages with socket
  connections refused. These are QA sentinel wheels, not publication.
- Using the real installed LangGraph serializer, generate10owned fixtures with
  the previous built packages: five statuses in plain checkpoint dictionaries
  and five legacy Pydantic search models. Current source restores all10;
  JSON values, executable-leaf classification and current reserialization hold.
  Repeat against current built-wheel imports outside the source tree.

One codec harness attempt omitted the existing required `facts` argument to
`ready_objectives`; retain its TypeError/process1. The corrected invocation
passes an empty fact set against the same frozen legacy blobs. No application
code, serializer allowlist, checkpoint value or permission changes to obtain a pass.

These are synthetic owned legacy codec fixtures and existing local checkpoint
tests. They are **not** production PostgreSQL historical-thread replay, a
licensed Agent Server run or real E2B acceptance. No model/provider/customer
request or production data was used.

## Release boundary

No package version/publication, Secret/IAM/provider operation or production
deployment. Recheck newly published exact-head repository checks and unresolved
threads before normal merge; do not publish a replacement status or bypass rules.
SaaS final latest-source cohort still needs the new SDK extra in its strict
sibling-dependency verifier and coherent public-API consumption, selected-image
and historical-checkpoint/native E2B/desktop/API release gates.

[CHAGELOG.md](../../CHAGELOG.md)
