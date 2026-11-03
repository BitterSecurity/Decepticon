# Sandbox daemon dependency installation

Fresh main `79978a642b7a9c9cbd351150774b869fbb724416` plus #1144 head `4a404063f228633086b2ab93e0a8d4c78559aa27`. The actual [sandbox build job](https://github.com/BitterSecurity/Decepticon/actions/runs/41671886792/job/126042279121) fails in the early fastapi/uvicorn/deepagents installation: pip attempts to uninstall certifi2026.7.22installed by Debian and fails with `uninstall-no-record-file`. This is an executed build failure, not an Actions billing limitation.

The subsequent web-engine pip layer already uses `--ignore-installed` for exactly this distro-package compatibility problem. Apply the same option to the earlier daemon layer, preserving distro-owned copies instead of removing their untracked files. No package constraints, lockfile, source versions, tool capabilities, NET_ADMIN or RoE authority are changed. No arbitrary cache cleanup or system package deletion is used.

Canonical full image build and actual daemon/runtime measurements are pending; this PR is not ready until those gates pass. No release/tag/publication, QA adoption or production deployment.


The first independent full build fails earlier, after17.071s: the Go-tool stage follows the default Kali mirror redirect to a TLS endpoint that the pinned base cannot yet trust, leaving no apt index/packages. The final stage already solves this by selecting the direct mirror and bootstrapping CA certificates over signature-verified APT before restoring HTTPS. Apply that same bootstrap to the Go-tool stage, without disabling signature or TLS verification. Preserve the failed receipt and retry the complete canonical image build.
