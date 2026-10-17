# Skill and workflow boundary review

Reviewed on 2026-10-17. Skills hold reusable specialist knowledge and evidence interpretation. Workflows own business stage selection, assignments, dependencies, reporting disposition and completion. A technical recipe can contain ordered steps without becoming a business workflow. Scope, authorization, human acknowledgement and stop conditions remain enforced wherever the relevant capability is exposed.

## Coverage and changes

The production audit read the bodies of all 49 existing SaaS/Bugclaw skills. The OSS audit inventoried metadata/headings across 329 remaining skills and read 59 selected bodies; this is not a claim that all 329 bodies received a complete semantic review. Core prompts and Bugclaw extraction have separate review records.

Startup and engagement lifecycle instructions moved to individual bug-bounty, red-team, pentest and vulnerability-research WORKFLOW.md documents. Their former skill paths contain migration notes. Orchestration, task-tree, final-report, reporting and verifier skills now retain technical models, schema semantics, evidence requirements and diagnostic methods rather than controlling the overall mission. Domain indices and specialist method leaves retain technical prerequisites without mandating full-domain coverage for every task.

Production APT28/29/41/44 and Lazarus references retain techniques, attribution, operational cautions and safety restrictions. Fixed campaign stage tables and arbitrary universal success thresholds were removed. The red-team workflow owns profile-selected campaign criteria and conditional defense feedback. Domain packs retain evidence requirements; the selected workflow owns coverage and closure. Bounty eligibility, disclosure formatting and technical validity remain distinct.

## Explicit compatibility boundaries

The optional OSS vulnresearch plugin still has a scanner/detector/verifier/patcher/exploiter pipeline coupled to its factory, graph and prompts. The new vulnerability-research workflow is available through the common agent runtime; this change does not rewrite that separate legacy plugin. Its procedural skill requires a coordinated runtime migration, not deletion of instructions alone.

Benchmark skill routing is coupled to EngagementContextMiddleware and harness tests and remains intact. The Soundwave follow-up read all 17 planning skill bodies, moved whole-process sequencing/publication instructions into planning/WORKFLOW.md and retained reusable templates, JSON schemas and actor knowledge. Soundwave's plan-only filesystem and existing document validator remain enforced; its workflow is selected only in optional Plan mode and is not an execution prerequisite. Shared OPSEC and defense-evasion restrictions remain capability policy; their presence is not treated as permission to weaken or relocate enforcement.

## Production Skillogy

The SaaS image builds an overlay on its pinned OSS catalog, removes the three exact retired startup/lifecycle paths, and compiles a strict protocol manifest. Search, loading and traversal also exclude those paths in already seeded graphs. New knowledge leaves declare the protocol metadata required by production. The SaaS reporting skill has a distinct machine name, redteam-finding-report, so it cannot overwrite the common finding-report node.

Catalog synchronization compares per-skill identities and body hashes, stages candidates and atomically publishes image-owned catalog nodes. It must not remove unrelated graph data. Editing local Markdown alone does not update AuraDB; production needs a new Skillogy image/catalog synchronization through its normal deployment process.

A read-only check of the serving Cloud Run configuration confirmed direct AuraDB transport. The Aura management API reported the configured instance as paused. A direct driver read could not resolve its host, so live node contents, hashes and indices were not verified. No database resume, catalog synchronization or deployment was performed during this review. Offline image catalog/protocol builds and runtime read-path tests are recorded separately.
