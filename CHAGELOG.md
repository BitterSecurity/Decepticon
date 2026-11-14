# Decepticon version history

All notable changes to the Decepticon project. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [Semantic Versioning](https://semver.org/) from `1.0.0`
onward (the `0.x` cycle is pre-stable per the core/framework/sdk split
design spec, §13.4).

Before opening or updating a PR, record its changes and PR link under
**Unreleased**. An open linked PR means proposed work; a merged linked PR means
integrated but unreleased work. Only dated release sections describe shipped work.
Maintain this single root `CHAGELOG.md`, preserving the full version history.
At release, move merged entries into a dated version section and add a comparison link.
Keep proposed and unmerged changes out of released sections.

## [Unreleased]

- Proposed plugin tool composition correction (PR pending): named tool replacements and disables also apply to additive plugin contributions, retaining the selected schema once and preventing a disabled tool from being added back. Preserve unrelated tools, role scoping, explicit-over-bundle precedence, existing safety checks and host-owned policy selection. All three focused regressions fail before the fix; 120 connected composition, host-refusal, browser and plugin tests pass after it, including actual LangChain tool invocation/schema checks. No public version or deployment change.

- Proposed [continuous-pentest follow-up designs #1184](https://github.com/BitterSecurity/Decepticon/pull/1184), not released. Design-only: two proposal docs for the remaining continuous-engagement work, no code. [External trigger start](docs/design/2026-11-12-external-trigger-start.md) — start an in-scope run from an authenticated external event (ASM/CI/alert) through a small trigger receiver that cannot widen scope. [Incremental re-assessment](docs/design/2026-11-12-incremental-reassessment.md) — each periodic run still scans from scratch (confirmed), with a baseline-diff overlay that reports only NEW/RESOLVED changes; the open design risk is the cross-run finding identity key. Both carry open decisions for review. Review clarified current tenant/approval/funding gates, OpenShell-only network authority, independent event delivery, comparable coverage before RESOLVED, tenant-scoped finding identity and ordered baseline writes. These remain proposals; merging the documents does not approve implementation choices or activate a service.

- Merged [scheduled engagements #1183](https://github.com/BitterSecurity/Decepticon/pull/1183), not released. `decepticon-cli schedule {create,list,delete}` starts engagement runs on the LangGraph server's native cron. `--mode reassess` (default) starts a fresh thread each tick (periodic re-assessment); `--mode campaign` continues one thread (a long engagement across scheduled slots, which `TimeWindowMiddleware` + `time-window-resume` keep inside its RoE window). `--multitask reject` skips a tick while a run is still active. Schedules are tagged so `list` reports only the ones this tool created. A scheduled run stays bound to the engagement's RoE and OpenShell policy. External-trigger start is a separate follow-up.
