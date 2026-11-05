# Managed OpenShell session startup and canonical image verification

Fresh main44eb9032 selects OpenShell but still places the daemon/stdio bridge in `/opt`, outside its reviewed filesystem baseline. It also never transfers the approved RoE into the managed workspace and starts another daemon on each reattachment. This isolated task starts from that fetched main and fast-forwards locally qualified #1160/#1161; no primary worktree, SaaS production resource or expired QA authority is changed.

The canonical sandbox image now carries a root-owned runtime copy under `/usr/local/lib/decepticon-sandbox`, within the selected supervisor's read-only `/usr` baseline. Existing `/opt` packaging and legacy daemon mode remain. Managed clients use absolute scripts with Python `-I`, and server startup explicitly inserts the immutable package root. A writable workspace/PYTHONPATH cannot supply the daemon or bridge implementation. No filesystem-policy broadening or HTTP fallback is introduced. The image makes `/workspace` sticky/writable for the managed identity.

After binding/policy admission, the host captures approved bytes and verifies the existing approval hash before passing them as bounded, checksummed stdin data to the fixed startup script. The script only restores `/workspace/plan/roe.json`, using no-follow directory handles and atomic replacement. It accepts no caller-selected root/path/command. This is an agent-readable scope copy, **not** host authorization; the host source and full binding/policy checks remain authoritative. Cooperative startup locks, PID/start identities and atomic state records reuse a live daemon and retain logs. If recording a newly spawned/unadmitted child fails, only that known new child is terminated; unknown/existing PIDs are not killed.

Managed health includes its own PID/start token. The host requires those to match the startup result instead of treating any HTTP200 or bridge exit0 as readiness. Invalid/malformed or unauthorized replies refuse; another legacy service on port9999 is not accepted or killed. Legacy `/healthz` retains its previous status-only body outside managed mode. The stdio bridge exposes a fixed read-only GET health route on workload loopback; target operations remain bounded/fixed-path and no new upstream address is configurable. No target request is replayed.

The actual default image initially failed at tmux environment synchronization under UID1000: SHELL was empty and HOME was `/`. A fresh controlled image run setting only Bash and the owned home succeeded. The final managed child now pins `SHELL=/bin/bash` and `HOME=/workspace`, without changing legacy daemon behavior. This is a measured runtime failure/correction, not a skipped tmux path or a root-only fixture.

## Actual verification

The canonical Dockerfile with its default browser/corpus features builds through the shared slot. Final r3 image is `sha256:c7fa8b8a648813f6a24a29cd8495636fc9ae2be9841d58b3aaea1631fd444191`; its three daemon module payloads exactly match source and remain root-owned0644. Its actual package inventory is Deep Agents0.7.32/FastAPI0.162.0/Uvicorn0.54.0. Earlier images/failed runtime receipts are retained and are not substituted for r3. No image is published.

The maintained opt-in validator runs this image with UID1000, a read-only root, networknone, all capabilities dropped, no-new-privileges, finite CPU/memory/PID bounds and private tmpfs. It performs real stdio→daemon execution, nonzero-policy-safe file/RoE roundtrips and tmux execution;8concurrent reattachments retain one PID/start identity and file bytes. Deliberate workspace Python shadows are ignored and the runtime bundle stays unwritable. A separate fixture occupies the port with a legacy daemon: status200 fails managed readiness while that service remains alive. All owned containers are removed after owner/nonce checks.

This uses a **synthetic transport admission fixture**, not a native OpenShell kernel or customer authorization. The receipt explicitly records `nativeOpenShell=false` and `productionEquivalent=false`. The source-client run completes in10.132seconds (cold daemon0.933seconds); the actual installed-wheel run completes in10.78seconds (cold daemon1.68seconds). These are local component observations, not GCP/E2B latency, capacity or recovery SLOs.

Reproduce after building the canonical image:

```bash
uv run --frozen --extra mcp --extra openshell python packages/decepticon/tests/validation/managed_session_image.py --image sha256:<local-image-id> --report /private/new-report.json
```

Full frozen Python qualification passes6,244cases/7explicit skips (3missingPlaywright,1missingTransformers,3Windows-only), with75% coverage above its60% gate. The269focused cases also pass after installing the actual private wheel and asserting imports from that installation. Final full lint/format has0type errors/972retained warnings. The new safety tests reproduce/check storage failure, directory/state symlinks, concurrent startup, stale PID, hash/JSON rejection, two umasks, real process health and immutable startup context. The initial exact0444 assertion failed under inherited077umask; final tests require owner readability and no write bits, without changing scope authority or widening permissions.

The repository's blocking Semgrep rules scan777files, explicitly including the new production module, with0findings/parse errors and metrics off. Config reporting compares baseline and candidate: both retain DS-0002HIGH (legacy root image default) and DS-0013MEDIUM (RUNcd), with no new finding IDs or exceptions. They are **not** claimed fixed or waived; managed/native identity/confinement and full release security disposition remain required. This source correction does not certify all image dependencies or native kernel security. GitHub Actions did not run under account billing limits; local exit codes and skips are recorded without bypassing protections.

## Upgrade and remaining gates

Update the matching framework and sandbox image together. Old images lack the managed bundle; silently using `/opt` or the direct HTTP transport is not a compatibility solution. Existing data and legacy runtime names are preserved per #1161. Private0.0.0 wheel/sdist artifacts retain the source sentinel and do not select or publish a release version. Full receipt/hashes and failures are in [the measurements](openshell-managed-start-20261105.json).

Actual native OpenShell gateway/kernel/launcher/versioned-distribution tests, current E2B template support and normal SaaS trusted provisioning/resolver wiring remain open. This PR does not exercise a model, switch production, migrate production data or extend old E10/F5 testing authority. Aggregate VM load/memory/rollout, final active-run recovery/clock, production operator path/quota and entitlement acceptance remain separate and incomplete.
