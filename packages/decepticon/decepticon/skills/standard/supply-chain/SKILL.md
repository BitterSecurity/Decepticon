---
name: supply-chain-overview
description: >
  Use when the engagement scope includes supply-chain attack simulation —
  typosquatted package publication, dependency confusion, GitHub Actions
  secret mining, internal mirror poisoning, OAuth-app impersonation,
  or vendor portal credential abuse.
metadata:
  skillogy:
    version: 1
  subdomain: supply-chain
  when_to_use: "supply chain typosquat dependency confusion gh actions secret oauth app impersonation internal mirror poisoning vendor portal"
  tags: supply-chain, typosquatting, dep-confusion, gh-actions, oauth-app
  mitre_attack: T1195, T1195.001, T1195.002, T1199, T1136, T1543
---

# Supply Chain Operator Skill Catalog

Supply-chain attacks have grown 1,300% since 2020 per Decepticon's own
[ai-red-teaming.md](../../red-team/tools-techniques.md). This catalog
gives the agent the playbooks to simulate the most common patterns —
all in a sandbox-isolated mode that publishes to a local mock registry by
default and to a real one only with `supply_chain_real_publish=true` in
ConOps.

## Playbooks

> **Inline technique reference — not separately loadable skills.** The entries below
> are summarized here for direct use; there is no separate `SKILL.md` to open for
> each. Do NOT call the skill loader on them — apply the technique with your tools
> using this summary and the technical guidance in this file.


| Technique | Use for |
|---|---|
| **typo-name-gen** | Generate typosquat candidates for a target package; reachability + popularity score |
| **dep-confusion-probe** | Check whether an internal package name is squat-able on PyPI / NPM / RubyGems / NuGet |
| **post-install-script** | Author + sandboxed publish of a benign post-install probe |
| **gh-actions-fork-pr** | Fork-PR secret mining; `pull_request_target` misconfiguration scan |
| **oauth-app-impersonation** | Lookalike OAuth app + scope-creep social engineering |
| **internal-mirror-poison** | Verdaccio / Artifactory / Nexus index manipulation |
| **sbom-divergence** | Audit SBOM vs actual installed packages for drift |
| **vendor-portal-creds** | SaaS vendor admin portal credential abuse paths |

## Dry-run mode

All publish-mode skills accept a `--dry-run` flag that:

1. Generates the typosquat package contents in `/workspace/typo-pkg/`.
2. Builds the artifact (`.tar.gz`, `.tgz`, etc.) without uploading.
3. Computes the "hit probability" via the target package's historical
   download counts + Levenshtein distance.
4. Reports the artifact location + hit probability for human review.

Real publish requires both `supply_chain_real_publish=true` in ConOps AND
operator HITL approval at the moment of publish. Defense in depth.

## GitHub Actions attack surface

Most rewarding attack class in 2024-2026. Common misconfigurations:

- `pull_request_target` with `actions/checkout` of `${{ github.event.pull_request.head.sha }}`
  → fork PRs run with target-repo secrets.
- `workflow_run` triggers reading `inputs` without sanitization.
- `${{ github.event.pull_request.title }}` interpolated into shell.
- Shared `GITHUB_TOKEN` with write scope on `contents`.

The `gh-actions-fork-pr` skill encodes the full enumeration: search the
target org's workflows, identify exploitable patterns, build a PoC fork
PR that exfiltrates `secrets.*` without modifying the workflow file
itself (so the operator's PR doesn't look obviously malicious to a human
reviewer).

## Detection references

Supply-chain detection and assurance artifacts include:

- A Sigma rule for the SIEM (e.g., unusual `npm install` in CI logs,
  `actions/checkout@` followed by `secrets.*` reference patterns).
- A GitHub Actions YAML linter rule for the customer's pre-merge checks.
- A SLSA attestation gap report.

## Out of scope

Real-world publication that could harm third parties (other companies
who consume the customer's internal packages). The `dep-confusion-probe`
explicitly avoids this by checking name availability without uploading;
the operator decides whether to follow through.
