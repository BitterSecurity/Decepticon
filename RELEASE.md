# Release Process

How Decepticon is versioned and released.

## Versioning

Decepticon follows [Semantic Versioning](https://semver.org/). Releases are
tagged `vMAJOR.MINOR.PATCH` (e.g. `v1.1.1`); pre-releases use a suffix
(e.g. `v1.2.0-rc.1`).

The `version` field in `pyproject.toml`, `clients/cli/package.json`, and
`clients/web/package.json` is a permanent `0.0.0` **sentinel**. The real
version is stamped from the git tag at build time — `release.yml` rewrites the
sentinel for the PyPI build and passes `--build-arg VERSION=<tag>` into the
Docker images. There is no version-bump commit to make before tagging.

## Cutting a release

1. Make sure `main` is green and the working tree is clean.
2. Run the local gates:
   ```bash
   make quality      # Python + CLI + Web: lint, typecheck, tests
   make smoke        # Compose build + service health checks
   ```
   Optionally run `make dogfood` for a full launcher → onboard → up smoke test.
3. In [CHANGELOG.md](CHANGELOG.md), move the `[Unreleased]` entries under a new
   `[X.Y.Z] — YYYY-MM-DD` heading, check them against merged PRs, and add the
   compare link at the bottom. Keep an empty `[Unreleased]` heading for the
   next release.
4. Commit the changelog (`docs: release vX.Y.Z`).
5. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

Pushing a `v*` tag triggers `.github/workflows/release.yml`.

The repository currently has no auto-tag workflow. A maintainer selects the
version after checking the merged changes and pushes the tag. The tag starts
`release.yml`; wait for its publish gate before announcing a release.
`release-recover.yml` verifies existing images and finalizes a failed release,
but does not build missing images.

## What the release workflow does

| Job | Output |
|-----|--------|
| `publish-pypi` | Builds wheels and sdists for `decepticon-core`, `decepticon`, and `decepticon-sdk`, pins sibling dependencies to the release version, and publishes via Trusted Publishing (OIDC — no API token). |
| `launcher` | Builds the Go launcher binaries with GoReleaser (which drafts the GitHub release) and uploads `config-checksums.txt`, a SHA-256 integrity manifest for `docker-compose.yml`, `config/litellm.yaml`, and `.env.example`. |
| `docker` | Builds and pushes the multi-arch `litellm`, `langgraph`, `cli`, and `skillogy` images. |
| `docker-heavy` / `docker-heavy-merge` | Builds `sandbox` and `c2-sliver` on native amd64/arm64 runners, plus the REA image on amd64, then merges the per-arch digests into manifests. |
| `docker-web` / `docker-web-merge` | Builds and merges the `web` image the same way. |
| `publish-release` | Verifies all eight `:<version>` images exist on GHCR, promotes final releases to `:latest`, and flips the GitHub release from draft to published. |

Every image is signed with Cosign (keyless OIDC) and ships a CycloneDX SBOM.
The `:latest` tag and the published release appear only *after* every image is
verified, so a half-finished release never moves the `:latest` tag.

## Digest pinning

The release workflow uploads
`image-digests.txt` — one line per image, `ghcr.io/bittersecurity/<image>:<version>@sha256:<digest>` —
as a release asset before publishing. This also covers releases published with
`GITHUB_TOKEN`, which do not trigger a second release-event workflow.
Operators who want immutable, tamper-evident deploys pin
their compose stack to the digest form instead of the moving `:stable` /
`:latest` channel tags:

```yaml
image: ghcr.io/bittersecurity/decepticon-litellm@sha256:<digest>
```

The digest is the manifest-list digest, so it covers all published platforms.

## Pre-releases

A tag whose version contains a hyphen (`v1.2.0-rc.1`) is treated as a
pre-release: the GitHub release is marked pre-release and the `:latest` Docker
tag is **not** promoted, so existing `:latest` users are unaffected.

## Recovering a failed release

`.github/workflows/release-recover.yml` is a manual (`workflow_dispatch`)
fallback that re-runs the release steps for an existing tag. Use it when a
transient failure (registry 5xx, Sigstore Rekor outage) leaves a release
half-finished.

## Submodules

`benchmark/xbow-validation-benchmarks` is a git submodule. After cloning, run:

```bash
git submodule update --init --recursive
```

so benchmark runs use the pinned upstream commit. `benchmark/MHBench` is
optional and only needed for that benchmark provider.
