# Opt-in OpenShell session transport

This source candidate builds on the agent-stack refresh (#1119). It does not
switch the default backend, change SaaS pins or provide an E2B compute driver.

## Layering

The maintained `langchain-nvidia-openshell` adapter already supplies basic exec,
upload and download. Its 41 unit checks passed in an isolated environment with
Deep Agents 0.7.32 and OpenShell SDK 0.1.5. A live stock OpenShell 0.1.5 Gateway
also passed execute and exact empty/binary file round trips through that adapter.
Use that upstream surface when only those operations are needed.

Decepticon additionally consumes named tmux sessions, persistent cwd/environment,
interactive input, background jobs, completion notifications and incremental
session logs. The upstream adapter does not provide those contracts. This PR
adds an opt-in `OpenShellSandbox` transport for the existing session service,
reusing its serialization and job bookkeeping. All operations enter through
OpenShell SDK session exec. A fixed stdio helper calls only the workload's own
loopback daemon; it has a route allowlist, message limit and bounded timeout,
and ignores environment proxy settings. It never chooses an E2B SDK command,
arbitrary daemon URL or host subprocess as a fallback. Ambiguous failures are
not automatically retried.

The trusted host must install `configure_openshell_resolver`, resolve an opaque
binding against its own tenant/run/custody store and return the cached backend.
Setting `configurable.sandbox_provider = "openshell"` without that resolver
fails. Every request rechecks the immutable receipt, session ID, expiry,
readiness, approval hash, effective policy hash and runtime generation against
the trusted reader. Receipts supplied by graph/model config are not authority.
The callback must actually verify policy ACK, ownership and revocation; this
adapter does not discover or implement that control plane by itself.

The guest image must install the stdlib helper at
`/opt/decepticon/sandbox_server/session_client.py`. The host must start and check
the session daemon **through OpenShell**, with explicit environment and the
engagement workspace. Docker image ENTRYPOINT/ENV were not a sufficient startup
contract in the tested managed runtime. The live fixture starts the service
through an admitted exec, waits for readiness, and deletes the sandbox in its
finally block. Streaming logs use a per-engagement workspace; existing code
deliberately does not enable pipe-pane logging for the generic `/workspace` root.

## Verified locally, 2026-10-27

The runtime fixture uses stock OpenShell 0.1.5, the local host's ABI 7 kernel and
a disposable reference container based on an immutable Decepticon sandbox image.
The loopback development Gateway uses unauthenticated plaintext locally, solely
for these disposable tests. This is not the production authentication design.
Test HTTP servers are fixture-owned listeners, never engagement targets.

The nine live checks passed (60.19 seconds):

- Maintained upstream adapter exec and byte-exact empty/binary files.
- Allow only the declared HTTP port; reject the undeclared port without a hit.
- Retain tmux cwd/environment and isolate named sessions.
- Reject a disallowed destination from inside tmux.
- Complete a background job and read its log incrementally without duplication.
- Execute asynchronously through the same OpenShell session transport.
- Kill a session and remove its job.
- Deliver interactive input to an already-running shell.

The fixture admission hashes/generation are explicitly synthetic. Those tests
qualify transport behavior and stock policy enforcement, not application custody
or real policy-ACK validation. IP-only endpoints use the forward proxy: stock
OpenShell rejects hostless `protocol: tcp` rules because transparent TCP requires
a DNS hostname. The test keeps the CIDR narrow and does not broaden the policy.

Affected backend, kernel, daemon, bash and notification regressions: **402 passed**,
with one expected skip for the unconfigured live module. Ruff lint/format passed;
the affected-file type check reported zero errors. These checks use the upgraded
Deep Agents 0.7.32 environment.

Reproduction: build `packages/decepticon/tests/integration/openshell.Dockerfile`
through the shared build coordinator, run an explicit disposable stock Gateway,
then set `DECEPTICON_OPENSHELL_TEST_ENDPOINT` and
`DECEPTICON_OPENSHELL_TEST_HOST_IP` when running `test_openshell_runtime.py`.
Install OpenShell SDK 0.1.5 and `langchain-nvidia-openshell` 0.1.1 in the test
environment. Without the explicit endpoint the live module skips.

## Remaining production gates

Implement the trusted admission resolver, durable driver state, canonical
RoE-to-policy compilation, policy ACK/revocation and runtime lifecycle in the
host control plane. Keep approval authority outside the workload. Do not permit
an OpenShell-admitted run to select another provider through mutable graph
config. The new factory branch alone does not enforce host configuration custody.

Qualify the actual inner-kernel E2B runtime with Supervisor transport, all policy
boundaries and workload performance. Passing a newer-kernel capability probe in
E2B is separate evidence from these local live checks. Browser/Ghidra/C2 paths,
external companions and host HTTP tools still need their own integration gates.
Python async cancellation does not stop a remote mutation; explicit runtime
revocation/session cleanup remains necessary. The normal 60-second automatic
background threshold and production auth/resume behavior are not claimed as
qualified by these short local tests.
