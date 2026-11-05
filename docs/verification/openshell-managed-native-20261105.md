# Actual OpenShell managed daemon qualification

The source and installed framework wheel from #1162 now run through a real, separate OpenShell0.1.6 gateway and stock native runtime, instead of the preceding synthetic session adapter. This task starts at fetched main44eb9032 and fast-forwards the locally qualified stack through #1162. It changes a maintained opt-in validator and evidence, without production application behavior changes.

The gateway listens only on127.0.0.1:18782 with private generated mTLS certificates, anonymous callers disabled, isolated SQLite/config/state and operator-pinned runtime/supervisor image digests. Official CLI/gateway archives match the release checksums. The real authenticated SDK client is supplied to the existing OSS local resolver; sessions, policy/config/loaded ACK, generation checks, complete binding labels and receipts are real. This client injection does not qualify automatic endpoint discovery or SaaS tenant authorization.

The canonical sandbox image is `sha256:c7fa8b8a648813f6a24a29cd8495636fc9ae2be9841d58b3aaea1631fd444191`, with the reviewed immutable `/usr` runtime bundle. Both runs create two distinct owned workspaces using the same approved host scope. Measured checks include UID1000 execution, separate runtime IDs/files and exact scope bytes, retained tmux environment/cwd with independent sessions, four simultaneous native reattachments preserving one daemon identity/data, and refusal of new execution after approved source bytes change. Workloads remain network-fenced by OpenShell; no direct Docker/E2B command substitutes for workload execution.

An owned HTTP listener on the Docker bridge receives the allowed request. A second live listener on an unapproved port receives zero requests. The observer additionally requires this sandbox's trusted OCSF DNS mapping from `host.docker.internal` to the owned listener address and synthetic address, followed by the exact native `transparent_tcp_mapping_denied` decision for that synthetic address/denied port. A transport error alone does not qualify policy enforcement. The operator's disposable gateway pins the host alias to the owned Docker bridge address; no external DNS/target/model request is needed.

The source lane passes in25.277seconds. The final maintained validator against the actual installed0.0.0 framework wheel passes in23.969seconds, after asserting imports originate from the wheel installation. This private sentinel artifact is the already qualified #1162 wheel, not a newly selected release version. Each lane deletes its two bound sandboxes and waits for deletion. Subsequent real SDK pagination finds zero sandbox items; Docker inspection finds zero task containers, and the exact owned gateway process terminates. Private state/certificates and failure evidence remain protected; unrelated resources are preserved. [Measurements and artifact identities](openshell-managed-native-20261105.json) record actual process exits and limitations.

Final repository lint passes:895formatted files, zero type errors and972existing warnings. This validation-only task does not repeat #1162's unchanged6,244-case production-source suite or rebuild the unchanged image. No hosted CI pass or repository-protection bypass is claimed.

## Retained failures and remaining work

The first expanded lane used a CIDR scope with default OSINT allowances. The real gateway rejects their overlapping ports/metadata as ambiguous before sandbox creation. This is an unresolved policy compatibility defect. The succeeding host-name lane preserves default OSINT and tests a distinct supported case; it does not silently disable OSINT, broaden CIDR authority or claim CIDR support is fixed.

Two stronger-audit observer attempts initially searched denial messages for the original hostname. The runtime correctly logs the denied transparent connection using its synthetic DNS address. Preserve those failed attempts; the final observer correlates the trusted per-sandbox mapping and denial, rather than relaxing the requirement to any timeout. A later type check caught an empty-loop log variable; final lint and the installed-wheel lane include its defensive initialization. An initial cleanup observer counted SDK pagination pages rather than items and stopped before mutation; correcting it to `Pager.all()` confirms zero actual resources before shutdown.

This actual native boundary uses local Docker29.6.1/WSL2 Linux6.18.54.1. It does not qualify managed E2B's measured ABI2 kernel, the newer inner-kernel transport, complete SaaS tenant/custody resolution, all tool templates, real-model behavior, Windows product distribution, full release/security gates or aggregate VM migration readiness. The approval-byte test refuses future calls; it does not claim to revoke already-running background work. No production deployment, schema/credit mutation, source merge, tag, package publication or traffic switch occurred.

## Reproduce on a dedicated local gateway

Prepare an isolated gateway with mTLS and exact reviewed image digests. The gateway must pin `host.docker.internal` to the owned Docker bridge address and run no unrelated workloads. Supply a new state directory and report path:

```sh
DECEPTICON_SKIP_BOOT=1 uv run --frozen --extra mcp --extra openshell python \
  packages/decepticon/tests/validation/managed_native.py \
  --endpoint 127.0.0.1:18782 --tls-dir /private/owned/tls \
  --image sha256:<exact-local-sandbox-image-id> \
  --fixture-address <owned-docker-bridge-ipv4> \
  --state-dir /private/new-state --report /private/new-report.json
```

The validator refuses existing state, uses private exclusive evidence output and checks complete binding ownership before deleting a sandbox. It cleans its resources; the operator remains responsible for stopping the dedicated gateway process and preserving private evidence. The default test suite never starts this opt-in lane.
