# Plain HTTP transport deprecation path — design proposal

Status: proposal — staged deprecation of the boundary-less transport,
needs maintainer decisions per stage.

## Why deprecate (eventually)

Since #1144 the OpenShell provider is the only network-boundary
authority. `HTTPTransportSandbox` (the plain HTTP wire to the sandbox
daemon, `DECEPTICON_SANDBOX_PROVIDER=http`) has no kernel network
boundary, so it cannot run engagement workloads — the RoE guard refuses
gated tools on it. Its remaining roles are non-engagement contexts:
tests, benchmarks, pre-scope file work.

## What already happened (stage 0 — done)

- The route-layer semantics moved to `SandboxDaemonClient`
  (daemon_client.py) with an abstract `_request()` seam.
- The route-semantics tests run against `FakeDaemonTransport` — a new
  transport inherits them for free. `test_http_transport.py` owns only
  the HTTP wire (pooling, headers, retry, error translation).
- `OpenShellSandbox` is a direct `SandboxDaemonClient` subclass — no
  HTTP baggage.

## Proposed stages

1. **FakeTransport everywhere in tests.** Audit the remaining
   HTTPTransportSandbox-dependent tests outside test_http_transport.py
   (reporting/sarif fixtures, evidence-contract fixtures) and move them
   to FakeDaemonTransport or a minimal in-process daemon stub. Exit
   criterion: only test_http_transport.py touches the class.
2. **Benchmarks onto the OpenShell path or a fake.** BENCHMARK_MODE
   currently rides the HTTP sandbox; switch the harness to the default
   provider or a fake, measured separately.
3. **Mark the escape hatch experimental/deprecated.** `.env.example`
   + docs: `DECEPTICON_SANDBOX_PROVIDER=http` requires an explicit
   opt-in note that engagements are unsupported on it (the RoE guard
   already refuses gated tools).
4. **Remove in the next Major (4.0.0).** Drop the provider value, the
   compose `sandbox` HTTP daemon wiring decision (the Kali toolset
   container may stay as an OpenShell runtime image), and
   HTTPTransportSandbox with its wire tests. The route layer and
   FakeDaemonTransport are unaffected.

## Trigger criteria

- The OpenShell default deployment is stable across engagements
  (admission, renewal, and kernel enforcement verified in real use).
- The test suite no longer depends on the HTTP wire for route-layer
  coverage (stage 1 exit).
- No supported integration documents provider=http.

## Non-goals

- Removing `SandboxDaemonClient` or the daemon HTTP API — the OpenShell
  session bridge speaks the same routes.
- Renaming to Safe/NonSafe-style policy names — the mechanism-based
  naming (HTTP transport vs OpenShell transport) stays.
