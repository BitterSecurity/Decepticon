---
title: CI/CD Pipeline
description: The workflows that gate every Decepticon change — build/test, security, supply-chain, and release.
---

# CI/CD Pipeline

Decepticon's CI/CD is deliberately **small and high-signal**. Each tool earns
its place by catching a class of bug nothing else catches; redundant and
style-only checks run locally in pre-commit instead of burning CI minutes.

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | PR + push to `main` | Build/test gate: lint, types, tests, web build, CLI/launcher smoke, Docker builds, workflow lint. The five required checks live here. |
| `security.yml` | PR + push + weekly | Consolidated security: CodeQL, Semgrep, Trivy, TruffleHog, dependency review. All findings land in the Security tab as SARIF. |
| `scorecard.yml` | Weekly + push | OpenSSF Scorecard — supply-chain posture. |
| `release.yml` | tag `v*` | PyPI (OIDC trusted publishing), Go launcher binaries, multi-arch signed+SBOM container images. |
| `release-recover.yml` | manual | Re-verify images and promote a partially-published release. |

## `ci.yml` — the gate

Change-detection (`dorny/paths-filter`) skips work for areas a PR doesn't
touch, but the five **required** status checks always report (a no-op success
step stands in when their area is untouched) so a PR is never left
indefinitely "pending":

| Check | Surface | Hard-gate |
|-------|---------|-----------|
| **Python (lint + typecheck + test)** | Ruff lint + format, basedpyright (errors), pytest | yes |
| **CLI (ubuntu / macOS)** | TypeScript CLI typecheck + tests | yes |
| **Launcher (ubuntu / macOS / windows)** | `go vet` + `go test` | yes |
| **Security (pip-audit + gitleaks)** | Python dependency + secret sweep | report |
| **Docker build (per image)** | Buildx build + Trivy image scan (SARIF) | build hard-gates |
| **Compose YAML validate** | `docker compose config` drift | yes |
| **actionlint (workflows)** | Workflow + composite-action syntax / shell-in-YAML | yes |

## `security.yml` — consolidated security

| Tool | Surface | Hard-gate | Notes |
|------|---------|-----------|-------|
| **CodeQL** | Python + JS/TS | findings → Security tab | `security-and-quality` query suite |
| **Semgrep** | repo rules | yes (ERROR severity) | `.semgrep/decepticon-rules.yml` invariants only — public packs are covered by CodeQL |
| **Trivy** | deps (fs) + IaC/Dockerfile (config) | report (SARIF) | CRITICAL/HIGH/MEDIUM, `ignore-unfixed` |
| **TruffleHog** | secrets across the diff | yes (`--only-verified`) | verified secrets fail the run |
| **dependency-review** | new deps on PRs | high severity | advisory until the repo dependency graph is confirmed enabled |

Why the consolidation: a previous iteration ran 18 overlapping tools (Bandit,
Checkov, Hadolint, OSV-Scanner, mypy, yamllint, markdownlint, knip, …). Most
duplicated CodeQL/Trivy or only produced style noise. They were removed from
CI; the ones worth running on every commit (ruff, shellcheck, hadolint, typos,
gitleaks) run locally in pre-commit instead.

## Custom Semgrep rules

`/.semgrep/decepticon-rules.yml` enforces Decepticon-specific invariants that
public rule packs can't express:

| Rule | What it catches |
|------|-----------------|
| `decepticon-no-shell-true-outside-sandbox` | `subprocess.run(..., shell=True)` outside `sandbox_kernel/` — command-injection vector |
| `decepticon-no-blanket-type-ignore` | `# type: ignore` without a `[code]` — disables ALL checks on the line |
| `decepticon-no-weak-hash` | `hashlib.md5/sha1` without `usedforsecurity=False` documenting non-crypto intent |
| `decepticon-no-verify-false` | `verify=False` on requests/httpx — disables TLS verification |
| `decepticon-no-hardcoded-default-key` | `sk-decepticon-master` literal — the publicly documented LiteLLM default |
| `decepticon-no-assert-in-prod` | `assert` in middleware/tools/sandbox/runtime — stripped by `python -O` |

## Suppression syntax

Use the precise directive for the tool that flagged the finding:

| Tool | Comment | Example |
|------|---------|---------|
| Ruff | `# noqa: <code>` | `# noqa: F401` |
| basedpyright | `# pyright: ignore[<code>]` | `# pyright: ignore[reportAttributeAccessIssue]` |
| Semgrep | `# nosemgrep: <rule>` | `# nosemgrep: decepticon-no-hardcoded-default-key` |
| CodeQL | `# lgtm[<rule>]` | `# lgtm[py/unused-global-variable]` |

**Bare suppressions (`# type: ignore` with no code) are rejected** — they
disable every check on the line including future ones.

## Local pre-commit hooks

`.pre-commit-config.yaml` runs the fast, high-signal checks on `git commit` so
they never reach CI. Install with:

```bash
uv tool install pre-commit
pre-commit install
```

Hooks: ruff (+ format), shellcheck, hadolint, typos, gitleaks, basedpyright,
plus file-hygiene checks (merge conflicts, large files, private keys, case
conflicts, line endings). The heavy scanners (CodeQL, Semgrep, Trivy,
TruffleHog) run on CI only.
