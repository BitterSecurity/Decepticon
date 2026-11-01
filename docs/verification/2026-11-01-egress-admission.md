# Required egress admission correction

Base: `cc6044561ff32188dc0aa899bcc7d3666ffcfd1c` (current remote main, OSS 2.2.2 source). SaaS review [#1523](https://github.com/BitterSecurity/decepticon-saas/pull/1523) reproduced the original `applied: false` and transport-error paths returning normally in the actual 2.2.2 image. Parser-side refusal remains separate from network confinement.

The middleware now requires boolean `applied: true`, records successful provisioning only, refuses exceptions with a generic error, and holds the admission lock until provisioning completes. Synchronous and asynchronous admission use the same boundary. Failure remains retryable; the existing trusted operator opt-out is unchanged. An in-progress workspace is never marked protected.

Local frozen-lock verification: `uv run --frozen pytest packages/decepticon/tests/unit/middleware/test_roe_egress_admission.py packages/decepticon/tests/unit/middleware/test_roe.py packages/decepticon/tests/unit/core/test_provision_roe.py -q` → **149 passed, 3 existing compatibility/deprecation warnings, 0 skipped, exit 0**, 59.99 seconds. The 11 new tests use the real policy compiler and dummy RFC5737 scope, a mocked backend, and no model, target, credentials or external network. Ruff check and format pass after formatting the new test file. No dependency or release-pin changes.

Compatibility: enabled backends must acknowledge successful network application with an actual boolean. Unsupported, missing-nft and failed backends now stop admission rather than continue with parser checks alone. This is intentional refusal, not evidence that those backends can enforce policy. Workspace-success cache invalidation after sandbox replacement or scope revisions is outside this bounded correction.

Actual E2B/OpenShell policy application, sandbox restart/replacement and installed client qualification remain separate acceptance. This source fix does not unblock the SaaS #1524 upgrade or establish a new published OSS release. GitHub Actions cannot run due account billing limits; no CI pass or protection bypass is claimed.
