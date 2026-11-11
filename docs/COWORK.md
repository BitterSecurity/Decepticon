# Working Together on Decepticon

This document describes how we collaborate day-to-day on the Decepticon OSS
repository — branch strategy, code-review flow, what triggers maintainer
review, how releases are gated, and the anti-patterns we have learned to
avoid. It complements:

- [CONTRIBUTING.md](../CONTRIBUTING.md) — how to participate (setup, PR
  basics, code conventions)
- [RELEASE.md](../RELEASE.md) — versioning and the release-workflow
  walkthrough
- [SECURITY.md](../SECURITY.md) — how to report vulnerabilities

If you are reading this for the first time, skim sections 1–4. Sections 5
onward are reference for when something specific comes up.

---

## 1. Operating Model in One Paragraph

Decepticon is developed on a **single trunk (`main`)** using **GitHub
Flow** — short-lived feature branches, pull requests into `main`, no
permanent `dev` / `staging` / `release-x` branches. Contributors with
write access can self-merge their PRs once the required CI status check
passes. **External publishing (PyPI, GHCR, GitHub Releases) is the only
step that always requires explicit maintainer approval**, gated by the
`pypi-release` GitHub Environment.

This keeps day-to-day velocity high while preserving a hard human gate
between code landing on `main` and code reaching downstream users.

---

## 2. Roles

We do not maintain a separate roles file. The effective roles are:

- **Maintainers** — the reviewers configured on the `pypi-release`
  GitHub Environment (`@PurpleCHOIms` today). Responsible for approving
  release deployments; that environment's reviewer list is the canonical
  definition of the role.
- **Write contributors** — collaborators with write access on the repo.
  May open branches directly on the repo, open PRs, self-merge PRs into
  `main` once CI is green, and trigger releases by pushing `v*` tags.
- **External contributors** — anyone with a GitHub account. Contribute via
  forked-repo PRs. Same review path as write contributors, but cannot push
  branches directly to `BitterSecurity/Decepticon`.

A contributor's role is not a level of trust; it is a question of how the
change is delivered (forked PR vs direct branch). The gates below apply
the same way regardless.

---

## 3. Branch Strategy

- `main` is the only long-lived branch. It is always intended to be
  releasable.
- Feature work happens on short-lived branches. Suggested naming:
  - `feat/<short-slug>` — new functionality
  - `fix/<short-slug>` — bug fix
  - `chore/<short-slug>` — tooling / CI / housekeeping
  - `docs/<short-slug>` — docs-only
  - `refactor/<short-slug>` — internal restructure with no behavior change
- Delete the branch after merge.
- We **do not** maintain a `dev` or `staging` branch. If you need to stage
  a multi-PR feature, land each PR behind a feature flag or behind an
  unimplemented entry point so partial progress on `main` is harmless.
- We **do not** cut long-lived release branches. The repo state at tag
  `vX.Y.Z` is the release. Hotfixes for an already-released line are
  handled by tagging a new patch from `main` after a forward fix lands.

---

## 4. Pull Request Workflow

### 4.1 Opening a PR

1. Branch off the latest `main`.
2. Make the change. Run the relevant local gates from the
   [Makefile](../Makefile) before you push — at minimum the lane that
   matches what you changed:
   ```bash
   make quality        # PR-gate mirror: Python + CLI + Web lint/typecheck/test
   make ci-lint        # just lint + typecheck
   make ci-test        # just tests
   ```
   These mirror the GitHub Actions CI lane exactly; the Makefile is the
   single source of truth.
3. Open the PR against `main`. Use a Conventional-Commit-style title
   (`type(scope): description`) — this is what CONTRIBUTING.md and the
   commit log already follow.
4. Fill in the PR description: what changed and **why**. Linked issues,
   reproduction context for bug fixes, and breaking-change notes belong
   here.

### 4.2 What CI runs

`main` requires exactly one status check: **`CI OK (required status)`**.
It is an aggregator job in `.github/workflows/ci.yml` that fails if any
of the lanes it needs failed or was cancelled:

- **Python (lint + typecheck + test)** — `make ci-lint` + `make ci-test`
- **CLI (ubuntu-latest)** / **CLI (macos-latest)** — typecheck + build +
  test for the Ink CLI
- **Launcher** — `go vet` + `go test`
- **Compose validate**, **Docker build** (amd64 + arm64)
- **Security (pip-audit + gitleaks)** — dependency CVE scan + leaked
  secret detection
- **actionlint** — workflow syntax

Adding a new CI job means adding it to `ci-ok`'s `needs:` list; the
ruleset itself does not need to change. If a lane fails, the PR cannot
merge. If a lane is flaky, fix the flake — do not paper over it by
re-running.

### 4.3 Review

**PRs are self-mergeable once CI is green.** There is no per-path review
requirement: `.github/CODEOWNERS` was retired in
[ADR-0012](adr/0012-retire-codeowners-merge-gate.md), which explains why
(the gate was configured in a way that never blocked a merge, and
enforcing it is self-blocking while there is a single code owner).

Review is therefore a convention, not a branch rule. Ask for one when
the change is one you would want a second pair of eyes on — and
supply-chain surfaces are still where that pays off most:
`.github/workflows/**`, `scripts/install.sh`, `docker-compose.yml` and
`containers/*.Dockerfile`, package manifests and lockfiles, and the
plugin contracts under `packages/decepticon-core/decepticon_core/`. The
*Blast radius* section of the PR template exists to make you name which
of those you are touching.

The hard human gate has not moved: nothing you merge reaches a user
until a maintainer approves the `pypi-release` deployment (§5).

### 4.4 Merging

- **Use squash merge** by default. Use merge-commit only when the branch
  represents a coherent series of independently meaningful commits
  (uncommon).
- Delete the branch after merge.
- Do **not** force-push to `main`, delete `main`, or rewrite published
  history — the ruleset blocks deletion / non-fast-forward updates of
  `main`. The same ruleset blocks deletion / update / non-fast-forward
  of any `v*` tag once created.

### 4.5 Anti-patterns we reject on sight

These are written here because each one has caused real damage in the
past, not as theoretical hygiene.

- **Mega-PRs that bundle unrelated work** — for example, "merge all open
  PRs", "integrate the backlog", "consolidated branch". They hide review
  surface, corrupt blame history, and have shipped regressions before.
  Land each PR individually. If two PRs genuinely depend on each other,
  say so in the description.
- **AI co-author trailers** (`Co-Authored-By: Claude`,
  `Co-Authored-By: Copilot`, etc.) on commits. They are not used in this
  repo and will be stripped. Use the assistant for help; commit under
  your own identity.
- **Skipping hooks** (`--no-verify`, `--no-gpg-sign`) without explicit
  maintainer agreement. If a hook fails, fix the underlying issue.
- **Bypassing CI** by editing or removing required checks in the same PR
  — weakening `ci-ok`'s `needs:` list, or making a lane pass
  unconditionally, alongside the change it would have caught. Workflow
  changes go in a PR of their own so the diff is reviewable on its own
  terms. Nothing in the repository configuration blocks this; it is the
  one anti-pattern the gates cannot catch for us
  (see [ADR-0012](adr/0012-retire-codeowners-merge-gate.md) §Consequences).

---

## 5. Releases

The release process is documented end-to-end in
[RELEASE.md](../RELEASE.md). This section covers only the **collaboration
gate** — who approves what, and where the human-in-the-loop step lives.

### 5.1 The gate

`.github/workflows/release.yml` triggers on `push: tags: ["v*"]`. Every
job in that workflow that produces an externally-visible artifact
declares `environment: pypi-release`:

- `publish-pypi` — three workspace wheels to PyPI via Trusted Publishing
- `launcher` — GoReleaser binaries + config-checksums manifest attached
  to the GitHub Release
- `docker`, `docker-heavy`, `docker-heavy-merge`, `docker-web`,
  `docker-web-merge` — multi-arch image builds, GHCR pushes, cosign
  keyless signing, CycloneDX SBOM generation and attestation
- `publish-release` — promotes `:<version>` → `:latest` and undrafts the
  GitHub Release

The `pypi-release` environment requires approval from one of its
configured reviewers (`PurpleCHOIms` today — the environment's reviewer
list is the canonical definition, see §2) before any of these jobs can
start their `runs-on` block. The deployment branch
policy on this environment is restricted to `refs/tags/v*`, so the
environment cannot be invoked from `workflow_dispatch` on `main` or any
non-tag ref.

`.github/workflows/release-recover.yml` (the `workflow_dispatch` recovery
path used to re-promote `:latest` after a partial-failure rerun) is
gated by the same environment for the same reason.

### 5.2 What a contributor sees

1. You land your fixes on `main` via normal PRs.
2. You (or a maintainer) push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The Release workflow starts and **immediately pauses** on every gated
   job, showing pending deployments in the "Review deployments" widget
   on the workflow-run page.
4. A maintainer reviews the pending deployments and approves them. Builds
   then proceed and publish externally.
5. If anything looks wrong at step 3 — wrong tag, wrong commit, wrong
   moment — a maintainer rejects the deployment. Nothing leaves the repo.
   Delete the tag and start over once the issue is fixed. (Note: the
   `tag-immutability-v` ruleset blocks deletion of a tag once
   created — only organization admins can bypass this, and only for
   recovery from a botched tag.)

### 5.3 Supply chain

These are baked into `release.yml` and do not require contributor action,
but you should know they exist so you can verify a release artifact:

- **PyPI Trusted Publishing (OIDC)** — no long-lived API token. The PyPI
  side also requires the OIDC token to come from the `pypi-release`
  environment; defense in depth in case the `environment:` line is ever
  removed from `release.yml`.
- **Cosign keyless signing** — every GHCR image and multi-arch manifest
  is signed via Sigstore. Verify with:
  ```bash
  cosign verify ghcr.io/bittersecurity/<image>:<version> \
    --certificate-identity-regexp 'https://github\.com/BitterSecurity/Decepticon/' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  ```
- **CycloneDX SBOMs** — generated by `anchore/sbom-action` for each
  image and attested to the OCI artifact via `cosign attest`. Also
  uploaded as workflow artifacts.
- **Config-checksums manifest** — `docker-compose.yml`,
  `config/litellm.yaml`, and `.env.example` get a sha256 manifest
  attached to the GitHub Release. The launcher and install script
  verify against this before writing user files. Treat this as the
  source of truth for those three files; do not fetch them from
  `raw.githubusercontent.com/main` for production use.

---

## 6. Communication

- **Bugs**: open an issue using the Bug Report template.
- **Features / design discussion**: Feature Request template, or open a
  draft PR with a design sketch in the description.
- **Security**: do **not** open a public issue. Follow
  [SECURITY.md](../SECURITY.md).
- **Release-blocking questions**: leave them on the PR or issue in
  question; do not DM maintainers about open-source release decisions.

---

## 7. Changing This Document

Any contributor can propose a change to this file; no branch rule gates
it. But because it codifies how the project is operated, open the change
as a PR with a description that explains what is changing and why, and
tag a maintainer for review even though nothing requires you to. If the
change reverses a decision rather than clarifying one, it wants an ADR
(see [docs/adr/README.md](adr/README.md)) — this document describes
current practice; ADRs record why it changed.
