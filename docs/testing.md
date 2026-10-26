# Test lanes

Keep tests that protect current behavior and meaningful failure boundaries.
Do not add tests merely to mirror constants, exact private implementation text,
or historical architecture. Add a regression where it can fail for the actual
bug; consolidate overlapping checks rather than counting assertions.

| Lane | Contents | Run |
|---|---|---|
| Unit/contracts | Pure behavior and isolated service boundaries; no live stack | `make test-unit` |
| Local integration | Owned local HTTP/process/browser/tmux fixtures | `make test-integration` |
| Live | Explicit disposable OpenShell/Neo4j services; E2B qualification is recorded separately in the SaaS runtime workbench | `make test-live ARGS='-q'` |

`pytest` and the existing PR lane never collect `tests/live` by default. Selecting
that directory requires `--live` even when a filename is passed directly. The
selected live lane fails on missing configuration, missing dependencies or
unreachable services; these are not green skips. `slow` describes execution
cost, not network ownership or whether a test is live. Existing main coverage
and release checks remain; separation does not waive release qualification.

Use per-run engagement/session IDs and owned service resources. Clean up in
`finally`. Never infer credentials, reuse a developer's service through default
passwords, or reset a real workspace to make a test pass. The Neo4j live fixture
requires explicit `DECEPTICON_NEO4J_URI`, `_USER` and `_PASSWORD`, creates unique
engagement records and deletes only those records. OpenShell requires explicit
`DECEPTICON_OPENSHELL_TEST_ENDPOINT` and `_HOST_IP`, creates a disposable sandbox
and waits for confirmed deletion. It needs the official OpenShell SDK/adapter.
Do not claim local OpenShell live tests prove E2B's inner-kernel transport.

## Cleanup disposition, 2026-10-27

- Rename `test_docker_sandbox_helpers.py` into the sandbox-kernel tmux suite:
  the Docker transport was retired and the file tests pure helpers.
- Remove the exact-regex-shape assertion and repeated ordinary marker cases
  from two other files. Keep adversarial bracket/newline cases and native
  command completion tests; printed markers are not execution authority.
- Benchmark orchestration unit tests mock stack reset, health and cost clients.
  Only the timeout test retains a real bounded clock wait. Replace the forever
  skipped old SDK HTTP-internals fake with a check of public SDK calls; keep its
  input-state contract and current challenge-name kickoff behavior.
- Framework boot contracts belong in the framework suite, not SDK-only tests.
  Boot/idempotence checks run in fresh subprocesses and explicitly clear the
  sandbox-only skip-boot option, avoiding order-dependent registry state.
- Remove unused `__init__`/`provision_egress` fixture leftovers from the RoE
  network test class. The constructor prevented pytest collecting that whole
  class. Its actual network-gating regression cases now execute.

Other tests remain until evidence shows their contract has been retired or is
covered elsewhere. Fewer tests are not a substitute for verified execution,
policy enforcement, upgrades or release artifacts.
