---
name: cleanup-template
description: "Cleanup & restoration plan generator — artifact inventory, persistence removal commands, pre-engagement baseline, post-engagement verification."
allowed-tools: Read Write Edit
metadata:
  subdomain: planning
  when_to_use: "create cleanup plan, artifact inventory, persistence removal, post-engagement teardown, restoration plan"
  tags: cleanup, restoration, persistence, post-engagement, hygiene
  upstream_ref: "Soundwave cleanup template — artifact inventory, persistence removal, restoration plan"
---

# Cleanup & Restoration Plan Generator

The cleanup plan is the **anti-foothold roster** — every artifact the kill chain will create must have a concrete removal command and a verifier, or dummy accounts / scheduled tasks / beacons routinely outlive the engagement.

## When to Use

- After CONOPS is written (the kill chain phases dictate what artifacts will exist)
- User says "create cleanup plan", "list what we'll leave behind", "post-engagement teardown"

## Workflow

### Step 1: Map Kill Chain Phases → Expected Artifact Types

For every phase in CONOPS.kill_chain, infer which `CleanupArtifact.artifact_type` entries will be produced:

| Kill Chain Phase | Likely artifact_types |
|---|---|
| recon | `tool` (installed scanners), `network-rule` (firewall whitelist) |
| initial-access | `account` (test users), `file` (uploaded payloads), `tool` (web shells) |
| post-exploit | `persistence-mechanism` (scheduled-task / service / registry-run), `account` (created backdoor users), `file` (dropped binaries) |
| c2 | `beacon` (C2 implants), `network-rule` (egress allow), `tool` (sliver / cobalt-strike payloads) |
| exfiltration | `file` (staged exfil archives), `network-rule` (DNS tunneling) |

### Step 2: Seed CleanupArtifact entries

For each expected artifact, set:
- `artifact_type` — category above
- `host` — placeholder (e.g. ``"<initial-access target>"``) — operations agents replace at run time
- `path` — likely filesystem / registry / account-name location
- `persistence_mech` — concrete mechanism if applicable
- `removal_command` — idempotent shell or API call to remove
- `verifier_command` — zero-exit on success
- `created_by_objective` — left blank; operations agents fill on creation
- `removed=False`, `removed_at=""`

### Step 3: Pre-engagement Baseline

Set `pre_engagement_baseline` to whatever snapshot reference the operator gives during the interview (volume ID, AWS AMI, hypervisor snapshot, manual filesystem hash list). If no baseline is available, record that explicitly — it's a critical risk signal for the engagement owner.

### Step 4: Completion Criteria

Default schema text covers most engagements. Override only when the engagement specifies different completion semantics (e.g. "leave honeyfile FILE_X in place for blue team training" — note as a cancellation_reason on that artifact later).

## Validation Checklist

Before writing `plan/cleanup.json`:

- [ ] Every CONOPS phase that creates persistence has a matching artifact entry
- [ ] Every artifact has a non-empty `removal_command`
- [ ] No artifact's `path` references an out-of-scope host
- [ ] `pre_engagement_baseline` is either set or explicitly marked "no baseline available"

## Anti-patterns

- Leaving `removal_command` empty — operations agents will skip the artifact entirely
- Putting absolute paths from the operator's machine (engagement workspace is `/workspace/<eng>` inside the sandbox; cleanup runs on TARGETS not the workspace)
- Forgetting C2 beacons — the kill chain's c2 phase is the most common omission

## Output

Write to `plan/cleanup.json` validating against `decepticon.core.schemas.CleanupPlan`.

## Document schema (derived from the validator's model)

`plan/cleanup.json` must validate against `CleanupPlan`. Fields not listed here are not part of the model and are ignored by the validator.

| field | type | required | allowed values / nested keys |
| --- | --- | --- | --- |
| `engagement_name` | str | yes |  |
| `completion_criteria` | str | no |  |
| `artifacts` | list[CleanupArtifact] | no | each object: `artifact_type`, `host`, `path`, `sha256`, `persistence_mech`, `removal_command`, `verifier_command`, `created_by_objective`, `removed`, `removed_at` |
| `pre_engagement_baseline` | str | no |  |
| `version` | str | no |  |
| `last_updated` | str | no |  |

`CleanupArtifact` object:

| field | type | required | allowed values |
| --- | --- | --- | --- |
| `artifact_type` | str | yes |  |
| `host` | str | yes |  |
| `path` | str | yes |  |
| `sha256` | str | no |  |
| `persistence_mech` | str | no |  |
| `removal_command` | str | yes |  |
| `verifier_command` | str | no |  |
| `created_by_objective` | str | no |  |
| `removed` | bool | no |  |
| `removed_at` | str | no |  |

