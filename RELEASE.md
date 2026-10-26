# Release policy and process

This is the canonical **OSS** release policy. Production/SaaS deployments have
independent identifiers and must not bump the OSS version. Maintainers choose
versions; merging a PR does not automatically create a release.

## 1. Choose the version by user impact

Follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Start from the
newest published final version and use the highest-impact change in the candidate.
Commit prefixes and the number of changed files do not decide the version.

| Change | Version | Examples |
| --- | --- | --- |
| Incompatible supported interface or upgrade | **Major**: `(X+1).0.0` | Remove/rename supported CLI flags, API/MCP tools or SDK contracts; incompatible persisted state/plugin contracts; require destructive data migration or remove a supported platform. |
| New capability preserving supported usage | **Minor**: `X.(Y+1).0` | New commands, optional integrations, agent/workflow capabilities, or a substantial additive interactive CLI experience. |
| Compatible correction to existing behavior | **Patch**: `X.Y.(Z+1)` | Resume/startup fixes, detection correctness, compatible security fixes, dependency updates or performance fixes without a new capability. |
| Documentation, tests, CI or internal refactoring with no shipped behavior change | **No release by itself** | Merge and record under Unreleased; include with the next justified product release. A packaging/installer defect affecting users can justify a Patch. |

- A breaking security fix is still Major unless compatibility is retained. Explain
  the migration and urgency; do not silently redefine supported behavior in a Patch.
- Restoring documented behavior is normally Patch. If previously supported usage
  must stop working, document why and classify the compatibility change explicitly.
- Experimental interfaces must be explicitly marked experimental before release;
  this exception does not make the entire OSS product exempt from compatibility.
- Example: `1.4.4` → `1.5.0` adds the interactive CLI experience and workflow/client
  capabilities; a compatible startup correction after `1.5.0` would be `1.5.1`.
- Do not release after every PR or on a fixed calendar merely to increment a
  number. Ship a coherent, verified set; expedite verified security/startup/data
  integrity fixes. A release-blocking regression stops publication.

Tags are `vMAJOR.MINOR.PATCH`; previews use `vX.Y.Z-rc.N` starting at `rc.1`.
Use previews for broad interface/migration changes needing user trials. The final
must pass the same gates; a preview is not permission to skip verification.

## 2. Version source and immutable artifacts

Source version fields in workspace `pyproject.toml` files and CLI/web
`package.json` files remain the `0.0.0` sentinel. The git tag supplies build-time
versions; do not make unrelated version-bump commits. The three PyPI packages
(`decepticon-core`, `decepticon`, `decepticon-sdk`) ship in lockstep with the OSS
launcher and versioned images; sibling dependency pins are stamped by CI.

Select an exact merged `main` SHA and record it. A tag is immutable: never delete,
move or reuse it, and never overwrite a published version's binaries, packages or
versioned image contents. Code corrections require a new PR and version. A retry
of an incomplete build must use the original tag/SHA, before final publication;
existing PyPI files remain immutable.

## 3. Required gates before publication

The release owner records the candidate version, full SHA, included PRs, version
rationale, upgrade notes and evidence. An authorization to merge is not itself an
authorization to publish; follow the user's or maintainer's actual release scope.

1. **Review and integration.** Included PRs are reviewed and merged. No unresolved
   blocker affecting this candidate's security, startup, data integrity or supported
   core workflows. Unrelated backlog may remain with explicit disposition; do not
   claim all security findings or all feature work are solved. The owned worktree
   is clean and the candidate tree matches the tested tree. Review/test any delta.
2. **CI.** Required CI/security jobs pass for the final source. Exercise affected
   Python, CLI, launcher, web, Compose and architecture boundaries. Use the full
   relevant suites; capture expected skips separately. A cancelled, timed-out or
   skipped job is not an executed test. Resolve new actionable scanner findings;
   a justified false positive needs a documented boundary and evidence, not a
   disabled rule. Do not waive a failing required check to publish.
3. **Actual local product.** Test launcher → engagement → service startup →
   interactive CLI on a separate installation. When releasing from WSL, run it
   directly in WSL. Use separate `DECEPTICON_HOME`, Compose project/stack names,
   ports and synthetic database credentials. Never delete/reset a user's volumes
   or modify their installation to obtain a passing result. Use `make quality`
   and `make smoke`/`make dogfood` only against disposable resources.
4. **Changed flows and upgrades.** Exercise the changed user flow and its failure
   path. For model/auth/routing changes, obtain a real model response through the
   actual product, using an authorized provider. For startup/config/persistence
   changes, verify fresh startup and same-volume restart/upgrade, preserving saved
   engagement/conversation data. Blue/MCP/plugin changes need their affected real
   boundary covered; do not represent an unconfigured component as verified.
5. **Candidate distribution.** Before approving the final publish job, download
   that run's candidate launcher, verify its checksum/version, and repeat the
   product smoke test with its versioned images. Record actual service health,
   CLI connection and changed behavior. Local source builds and mocks complement
   this check; they do not replace the distribution test. Retain terminal/log or
   screenshot evidence without credentials, tokens or private target data.
6. **Publication readiness.** Verify all three PyPI packages, launcher assets,
   config and skill checksums, eight image versions/platforms, image digests and
   image signing/SBOM job results. Review any attestation warning explicitly;
   attach evidence of what succeeded and what did not. Approve the normal protected
   environment; do not bypass failed gates. Drafts/tags alone are not a release.

## 4. Publication and change history

1. Before every PR (including maintenance/docs), update root
   single [CHAGELOG.md](CHAGELOG.md), preserving full history, with PR
   links and compatibility/upgrade notes. Pending work stays under Unreleased;
   linked PR state distinguishes proposed from merged. Preserve historical entries.
2. Record the selected SHA and version rationale. Tag only after pre-tag gates:
   ```bash
   git tag -a vX.Y.Z <verified-main-sha> -F <reviewed-release-notes-file>
   git push origin refs/tags/vX.Y.Z
   ```
   The tag starts [release.yml](.github/workflows/release.yml). Reviewers may need
   to approve its `pypi-release` environment again for downstream jobs.
3. Keep GitHub's release draft until candidate-artifact verification and all
   publication gates succeed. Verify the PyPI job separately: the current
   `publish-release` dependency list waits for Docker/launcher jobs, **not PyPI**.
   Publishing wheels happens before GitHub undraft; distribution across PyPI,
   GHCR and GitHub is not a cross-registry atomic transaction.
4. Publish reviewed release notes: highlights, fixes, breaking/upgrade notes,
   known limitations, PR links and measured verification. Confirm actual public
   release metadata and downloadable assets. Never announce success from a tag
   push or from a single green build job.
5. **After actual publication**, move only included merged entries to
   `[X.Y.Z] — YYYY-MM-DD`, using the real `published_at` date (UTC), and update
   compare links in CHAGELOG.md. Do not invent a release date or
   mark a failed draft released. A small post-publication docs PR can record the
   real date; keep that post-tag documentation change under Unreleased and do not
   move the immutable tag to include it. No runtime version bump is needed.
6. Confirm the public version/assets, full workflow result, update command and
   final issue/PR dispositions. Clean only owned test resources and private auth
   copies; preserve evidence. Report any remaining limitation accurately.

## 5. Channels and upgrade compatibility

See [update-channels.md](docs/update-channels.md) for implemented behavior:

- **latest** is the default and selects the newest published final. It does not
  track `main`. The release publish job moves the eight `:latest` image tags.
- **stable** is opt-in and normally waits at least **seven days** after publication.
  The scheduled promotion workflow enforces a seven-day minimum. Client-side
  resolution has documented first-release/fallback exceptions; a channel is not
  proof of successful tests or zero defects.
- Pre-releases are explicitly installed and advance neither final channel.
- Update checks offer **Update now / Skip**, with Skip selected. Non-interactive
  startup does not install automatically. Users can explicitly run
  `decepticon update --channel latest`. Switching channels does not downgrade.
- Install matching launcher, images and configuration. Document migrations,
  backup/rollback compatibility and supported prior versions when applicable;
  ordinary compatible updates must not require deleting credentials or volumes.

A known blocking regression must not be promoted to stable. The current scheduled
promotion filters age, not incident status: the release owner must suspend that
promotion workflow while a known-bad candidate would be selected, publish a
verified correction, and resume promotion only when its eligible target is safe.
Do not shorten the seven-day floor to expedite a hotfix; users can opt into latest.

## 6. Failure, recovery and hotfixes

- Failed pre-publication builds remain incomplete, not released. Investigate the
  failing job. Retry transient failures at the same tag/SHA; source changes go
  through a corrected PR and a new version, never a moved tag.
- [release-recover.yml](.github/workflows/release-recover.yml) only verifies existing
  images, optionally promotes latest and undrafts an existing release. It does
  **not** build missing images or verify PyPI/checksums/digest assets. Manually
  satisfy those checks and the same local product gates before using it. Never
  promote a preview to latest, even though its input is manually selectable.
- Moving multiple registry channel tags is sequential, not transactional. If
  promotion fails partway, retain the draft, verify every image and recover the
  intended channel set. A retry must not obscure an artifact mismatch.
- For a published regression, disclose the affected version and workaround, stop
  inappropriate stable promotion, and ship a verified new Patch/Minor/Major as
  classified above. Mark an affected PyPI version yanked when appropriate; never
  replace its files. Do not silently relabel an older version as a newer release.
- Automatic updates are forward-only. Restoring an earlier image tag does not
  downgrade installed launchers or reverse a database migration. Any rollback
  needs explicit version/config pins and proven data compatibility or a backup;
  deleting a user's database is not a recovery procedure.

## Workflow outputs

| Job | Output |
| --- | --- |
| `publish-pypi` | Three versioned wheels and sdists through Trusted Publishing. |
| `launcher` | Six platform binaries, draft release, binary/config/agent-skill checksums. |
| `docker` | Multi-arch LiteLLM, LangGraph, CLI and Skillogy. |
| `docker-heavy` / `docker-heavy-merge` | Native amd64/arm64 sandbox and c2-sliver; REA amd64. |
| `docker-web` / `docker-web-merge` | Native amd64/arm64 web image and manifest. |
| `publish-release` | Verify eight images, promote final `:latest`, attach `image-digests.txt`, undraft. |
| `promote-stable.yml` | Promote the newest age-eligible final to `:stable`, separately. |

Images are signed with Cosign and have CycloneDX SBOM jobs. Consult workflow
results for attestation warnings; the SBOM policy includes bounded retries and a
warning fallback. `image-digests.txt` records manifest digests for immutable image
pins, covering the published platforms:

```yaml
image: ghcr.io/bittersecurity/decepticon-litellm@sha256:<digest>
```

## Submodules

`benchmark/xbow-validation-benchmarks` is a submodule. Use
`git submodule update --init --recursive` to obtain its pinned code for benchmark
runs. `benchmark/MHBench` is optional and needed only for that provider.
