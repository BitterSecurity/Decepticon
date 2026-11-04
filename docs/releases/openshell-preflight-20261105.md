# OpenShell release preflight — 2026-11-05 KST

**Publication is held.** The user authorized an OSS release if SaaS adoption
requires it. This is a preflight report, not a release approval or a published
version. No tag, registry publication, channel promotion or SaaS deployment was
performed. Measurements were taken on November 4 UTC / November 5 KST.

Fresh remote main is `0a8518f00e38766dfaf87536c9e482949aa74752`.
GitHub's latest final and all three PyPI packages are still **2.2.2**;
[#1144](https://github.com/BitterSecurity/Decepticon/pull/1144) is merged but
unreleased. Publishing a package alone would not change a downstream SaaS image
pinned to an older package or install its trusted OpenShell admission adapter.
App consolidation on a VM does not itself require publishing OSS.

Exact commands, counts, limitations and candidate package hashes are in
[the measurement receipt](openshell-preflight-20261105.json).

## Confirmed default deployment gap

Current RoE middleware requires OpenShell admission for a workspace before model
or tool execution. The default factory still selects `HTTPSandbox`. The resolved
Compose configuration points `SANDBOX_URL` to `http://sandbox:9999` and does not
explicitly enable the OpenShell provider or local resolver. Neither the example
environment nor launcher configures these switches. Operators may supply custom
environment files; this finding concerns the shipped default, not every custom
deployment.

An isolated run used the actual LangChain graph and default backend factory with
the actual RoE middleware, a temporary synthetic scoped workspace, an inert fake
model and a tool whose invocation was recorded. Both `invoke` and `ainvoke`
raised:

```text
RoE requires an admitted OpenShell kernel boundary; configure DECEPTICON_SANDBOX_PROVIDER=openshell
```

There were **zero model and zero tool calls**. The backend was not mocked. This
demonstrates the admission failure with the default backend; it is an offline
graph probe, **not an executed launcher smoke or live kernel-confinement test**.
The refusal is the required security behavior. Restoring HTTP/nftables fallback,
enabling retired opt-outs or removing this gate is not a resolution.

The following smaller reproduction exercises the same real default admission
without starting services or contacting a model/target. Run after the frozen
development install; the expected exception demonstrates the unresolved gate:

```bash
env -u DECEPTICON_SANDBOX_PROVIDER -u DECEPTICON_OPENSHELL_LOCAL \
  uv run --frozen --extra mcp --extra openshell python - <<'PY'
import json
import tempfile
from pathlib import Path
from decepticon.backends.factory import build_sandbox_backend
from decepticon.middleware.roe import RoEGuardrailMiddleware

with tempfile.TemporaryDirectory() as directory:
    workspace = Path(directory)
    (workspace / "plan").mkdir()
    (workspace / "plan" / "roe.json").write_text(json.dumps({
        "machine_enforcement": {"in_scope": ["example.com"]}
    }))
    print(type(build_sandbox_backend()).__name__)
    RoEGuardrailMiddleware().before_agent({"workspace_path": directory}, None)
PY
```

The LangGraph Dockerfile installs `--extra neo4j --extra mcp`, whereas the SDK
is an optional `openshell` extra. An independent clean environment using the
Dockerfile's exact `uv sync --frozen --no-dev --extra neo4j --extra mcp` profile
confirmed that `importlib.util.find_spec("openshell")` is `None`. This measured
installation profile is not represented as an actual container-image inspection.
Adding environment switches alone therefore does not complete deployment.

To close the gap, qualify a supported trusted runtime topology and credentials,
install the SDK in the serving artifact, wire per-workspace admission and verify
effective policy/Loaded ACK before execution. Fresh startup, resume/revocation,
representative policies and persisted data must work through the actual product.
Kernel/protocol constraints remain part of that qualification; earlier patched
runtime and loopback evidence does not certify an untested stock deployment.

## Executed checks

| Check at the recorded source SHA | Actual result |
| --- | --- |
| Frozen development install with MCP and OpenShell extras | Passed |
| `make ci-lint` | Passed in 31.348 seconds |
| Full pytest, four workers, MCP/OpenShell extras, coverage gate 60% | 6,182 passed; 7 skipped; 21 warnings; 75.14% coverage; process 100.915 seconds |
| All three package wheels and source distributions, shared build slot | Six artifacts built; local `0.0.0` sentinels retained |
| Compose resolution with `.env.example`, no operator env-file loading | Valid; HTTP default observed |
| Actual default-factory admission, sync and async graphs | Both refused before model/tools, as detailed above |
| Independent default image dependency profile | OpenShell SDK absent |

The seven pytest skips are three browser integrations without Playwright, one
optional injection detector without Transformers and three Windows locking
cases. They are not passes. No slow-marker exclusion was supplied to this run.
The package artifacts are local source candidates; they have no published
version and do not satisfy candidate-distribution verification.

The exact-head [CI run](https://github.com/BitterSecurity/Decepticon/actions/runs/41764832551)
completed its required aggregate check, CLI, launcher, Web, Compose and security
jobs. Its Python, Docker build/arm64 smoke and telemetry jobs were **skipped**.
This is not an all-components executed CI result. The local Python measurement
above is independent; no GitHub billing rejection was observed for this run.

## Release and adoption disposition

Follow [RELEASE.md](../../RELEASE.md). A final version/tag has not been selected.
If the removal of the previously supported HTTP execution contract remains in
the release, the compatibility rule calls for a **Major**, with an explicit
migration and preview qualification; calling a mandatory operator migration a
Patch would conceal the break. A preview also requires the same applicable
verification gates. Source version sentinels stay unchanged.

Before any publication, complete actual isolated WSL launcher/service/CLI use,
live supported runtime policy enforcement, same-volume upgrade and checkpoint
preservation, changed-flow checks, downloaded candidate launcher/versioned-image
verification and the full artifact/platform/signing/SBOM inventory. This
preflight stops at the confirmed deployment gap and does not claim those gates.

SaaS adoption has its own E2B lifecycle, tenant binding, testing-window, billing
and checkpoint compatibility gates. Its existing HTTP adapter is not converted
by an OSS merge or release. Keep production serving unchanged until those
integration gates and the separate VM qualification are complete.
