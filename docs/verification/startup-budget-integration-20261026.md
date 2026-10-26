# Startup budget integration — 2026-10-26

PR [#1090](https://github.com/BitterSecurity/Decepticon/pull/1090) was reviewed against main `dd5b43c01bbc6ac09615a98444afcd2a4664480e` in an isolated worktree. The compatible correction aligns launcher and opscontrol Compose interpolation with the existing validated startup wait budget. Early readiness still succeeds immediately; retry limits and the overall wait remain intact. No data migration, model change, release tag or downstream version selection is included.

## Verification

- All four affected Go packages passed: compose, config, infracontrol and health. The final compose run took 417.294 seconds. An earlier existing hanging-request fixture failed its request-count timing assertion under host load; its focused rerun and the final complete affected-package run passed. Preserve this distinction when interpreting the evidence.
- Four Python tests using actual Docker Compose configuration interpolation passed. These are configuration checks, not a live stack startup or release-artifact acceptance test.
- Ruff check and format check passed for the new Python test. Git whitespace checks passed.
- Existing release history is preserved; only this pending correction is added to Unreleased. A merged PR does not publish OSS.

## SaaS disposition

The production SaaS uses Cloud Run and its own image/configuration release path, not this Go launcher or OSS Compose healthcheck. No SaaS healthcheck backport or production OSS pin bump follows from this change. SaaS and Desktop Local runtime pins require their own qualification; this change does not upgrade either.
