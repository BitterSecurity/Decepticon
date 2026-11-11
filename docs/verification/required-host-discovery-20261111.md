# Required host discovery correction — 2026-11-11

Proposed source correction stacked on OSS #1170 (`f72e136e8add5eb4a9672433a9442d6b2bbeaa7c`),
created in an isolated worktree from freshly fetched main
`d26ddaa1e3b0ccaffd50b982fcc2debeb650c840`. No package release or SaaS deployment.

## Reproduced problem and behavior

The original discovery catches an installed provider's import failure and
returns `NullHostIntegration`. Its `read_policy()` returns `None`, so engine
model/tool consumers use ordinary no-host behavior. Multiple enabled providers
also select whichever entry point happens to appear first.

Refuse broken discovery/loading, invalid providers and ambiguous enabled
providers. A deployment may require an entry-point name with
`DECEPTICON_HOST_INTEGRATION`; missing, duplicate or allowlist-disabled required
providers refuse. An explicit selection does not load other providers. A truly
absent host keeps pure OSS behavior. Serialize initial discovery and avoid
caching a failure as no-host state. Do not expose plugin exception messages.

## Local evidence

Python 3.13.7; actual checked-out core/framework/SDK sources are supplied together
through `PYTHONPATH`, with boot disabled and no model/provider/target request.
Existing virtual-environment dependencies are reused; this is not the newer
SaaS production candidate environment.

```sh
PYTHONPATH=packages/decepticon-core:packages/decepticon:packages/decepticon-sdk \
DECEPTICON_SKIP_BOOT=1 DECEPTICON_PLUGINS=__test__ \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/catow/GIT/decepticon_new/.venv/bin/python -m pytest \
  -c /dev/null -p no:cacheprovider packages/decepticon-core/tests \
  packages/decepticon/tests/unit/agents/test_host_integration_refusal.py -q
```

Result: **204 passed, 0 failures/errors/skips, process exit 0**. Includes all
195 core cases (23 host registry cases) and nine actual engine consumer cases:
required provider missing, broken and duplicate through model resolution,
tool composition and model middleware construction. Refusal errors remain
non-retryable through the real run-control classification. Retained warnings
are two deprecated research type imports.

Scoped Ruff passes. BasedPyright on the changed core library reports
**0 errors, 0 warnings**, exit 0. The complete correction diff is also scanned
with Gitleaks; actual scan evidence is retained with the PR qualification.

Diagnostics are preserved: the first wider local invocation supplied only the
new core path and accidentally resolved the framework from an unrelated older,
dirty primary worktree (194 passed / one incompatible RoE import failure).
Supplying all three current source roots fixes the environment; no product
assertion, exception policy or timeout was weakened. That earlier result is not
called a passing gate.

## Remaining parent adoption

This qualifies the discovery correction only. The remaining #1170 source review,
relocated SaaS/Desktop host provider, actual selected-image/model/tool/custody/
in-flight revocation qualification and updated source cohort remain required
before SaaS/Desktop adoption. GitHub Actions have not run for this correction;
no hosted CI success or protection override is claimed.

See [host integration contract](../design/host-integration.md) and
[CHAGELOG.md](../../CHAGELOG.md).
