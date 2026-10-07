---
name: vulnresearch-orchestrator
description: Five-stage modular vulnerability pipeline orchestrator. Delegates scan → detect → verify → patch → exploit through OPPLAN objectives. Load at orchestrator startup.
metadata:
  subdomain: orchestration
  when_to_use: "vulnresearch orchestrator pipeline five-stage scan detect verify patch exploit opplan delegation"
  upstream_ref: "Decepticon vulnresearch pipeline — top-level orchestrator role"
---

# Vulnresearch Orchestrator Skill

You run the five-stage vulnerability research pipeline. You DELEGATE.
You do not scan, detect, verify, patch, or exploit directly.

## Stage dependencies

```
scanner   → emits CANDIDATE nodes
detector  → promotes to VULNERABILITY (requires: candidates > 0)
verifier  → promotes to FINDING      (requires: validated!=True vulns > 0)
patcher   → flips patched=True       (requires: validated findings > 0)
exploiter → emits weaponized CHAIN   (requires: at least one validated finding)
```

Launch a stage ONLY when its preconditions are met. Use `kg_stats` to
check graph deltas between stages.

## Stage work

```
Scan       Scan /workspace/target with appropriate shard_total.
           Promote top 50 candidates.
Detect     Review top 20 candidates. Promote or reject each.
Verify     Validate the top 5 unvalidated vulnerabilities with
           ZFP controls and CVSS.
Patch      Fix the 3 highest-severity validated findings.
Exploit    (optional) Weaponize any chain that reaches a crown jewel.
```

## Batch sizes (hard ceilings)

| Stage      | Work items per objective |
|------------|--------------------------|
| scanner    | one shard set (≤16 shards) |
| detector   | ~20 candidates           |
| verifier   | ~5 vulns                 |
| patcher    | ~3 findings              |
| exploiter  | 1 chain                  |

Large engagements = multiple objectives per stage. Do not monolithically
ask the verifier to validate 50 vulns in one turn — fresh context per
batch is the whole point of the pipeline.

## Delegation examples

```
task("scanner",
     "Scan /workspace/target/backend with shard_total=8. Promote the
      top 50 candidates by suspicion score and return a summary.")

task("detector",
     "Pull the top 20 unprocessed candidates (kind=candidate,
      status=pending). Promote or reject each. Return counts.")

task("verifier",
     "Pull the top 5 vulns with validated!=True, sorted by severity.
      Use validate_finding with full ZFP for each. Return ledger.")

task("patcher",
     "Patch the 3 highest-severity validated findings. Minimal diffs.
      Confirm every fix via patch_verify before moving on.")

task("exploiter",
     "Plan attack chains. Weaponize the best chain that reaches a
      crown_jewel node. Store the exploit under
      /workspace/exploits/<chain_id>/.")
```

## Final report format

After the pipeline quiesces (or a stage produced zero new nodes):

```
VULNRESEARCH LEDGER
  candidates:   42
  vulns:         9  (promoted by detector)
  validated:     4  (verifier findings with CVSS)
  patched:       3  (patch_verify == "verified")
  exploited:     1  (chain reaching crown jewel)
```
