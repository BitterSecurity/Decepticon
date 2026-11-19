---
name: verify-ci-aggregate
description: Verify the OSS required CI aggregate against real Bash and GitHub dependency outcomes without hosted jobs or application execution.
---

1. Launch: use a latest-main task worktree. Reuse the repository's locked dev
   environment; Bash, jq and Python3 must be available. No service is started.
2. Doctor: confirm this task's current workflow, clean/changed inputs and the
   locally available historical commit be0892d55187c2ebb9ee24109020558241eaab14.
   Fetch that immutable commit if a shallow checkout lacks it; never reset a
   working tree to reproduce the old behavior.
3. Drive: run service-harness inspect, verify and status from the task root.
   The behavior helper executes the actual ci-ok shell block with22 fixtures:
   successful/scoped-out jobs, unassigned/unfinished/unknown statuses, and missing
   or malformed results. Its historical mode reads the real old workflow in
   memory. Exit1 proves assertion failures; setup/parse/timeout errors exit2.
4. Evidence: retain the runner's exact source fingerprint, Bash outputs and
   expected historical failures outside worktree cleanup. This verifies result
   aggregation, not hosted runner capacity, a full application suite or CI/CD
   success. Repository protections remain separate.
   The pinned runner currently rejects this repository's existing benchmark
   submodule directories before gate execution. Preserve them and report the
   limitation; manual gate output is not a successful harness receipt. Keep
   this correction in progress until required qualification is complete.
5. Cleanup: there are no servers, containers or provider requests. Preserve
   unrelated work and remove the task worktree only after actual merge and
   clean/pushed/inactive checks. Keep all evidence.
