# OpenShell admission successor verification

Source [PR #1199](https://github.com/BitterSecurity/Decepticon/pull/1199); no release or deployment. The public runtime/API signature remains
unchanged. A host may already have acknowledged a successor when the next backend
operation arrives before the old receipt expires. The backend previously refused
that case even with its explicit trusted renewal callback. The expired-receipt
path also accepted a callback result before checking its complete binding against
the captured identity and observing it through the current receipt reader.

The adapter now accepts a successor only through the explicit trusted callback,
with unchanged binding ID, sandbox, workspace, runtime generation, approval hash,
effective policy hash and readiness. Its future expiry must advance, and the
current reader must independently report the same receipt. Commit the new local
receipt only after those checks. Unknown or invalid renewal preserves the old
receipt and closes this backend, so a later operation cannot replay the callback.
The error remains generic. The host must reconcile before replacing that client.

A lock scoped to this backend serializes concurrent admission checks. Successful
renewal retains the backend instance and its background job tracker. It does not
recreate a sandbox, restart a command or add a direct-HTTP/provider fallback.
The host remains responsible for actual current ownership, policy ACK and all
execution bounds; supplying a dataclass is not policy enforcement.

Compatibility: a renewal callback must expose its acknowledged successor through
`current_admission` before it returns. Merely returning a newer receipt without
an independent matching current observation is refused. A host that has no
renewal callback keeps the existing refusal on any receipt change.

## Verification

An isolated Python3.13.7 environment uses the unchanged workspace lock and the
OpenShell/MCP/Neo4j extras, Deep Agents0.7.32, LangGraph1.2.17 and OpenShell0.1.6.
No live model or provider credential is used.

- Original main runtime:13 selected new regressions fail, including early
  successor adoption, changed identities/policy, unobserved renewal, unknown
  callback replay and concurrent callers. No working-tree source is reverted.
- Corrected source:251 backend and SDK host-surface cases pass, process0.
- Full `pytest -m 'not live' -q`:6,320passed,9skipped, process0. Skips are three
  uninstalled Playwright cases, one missing Obscura case, one optional transformers
  case, three Windows-only file-lock cases and one missing curl-cffi case. They
  are not qualified as passed; live tests are not claimed.
- Required `UV_NO_SYNC=1 make ci-lint` passes: Ruff check/format (922files),
  full errors-only type gate (889files,0errors,924reported warnings). The
  affected source alone reports0errors/0warnings.
- OpenShell0.1.7 compatibility: the same251connected cases pass, process0,
  after installing the exact official wheel SHA256
  `21de1816fd3dc5627c625782c616816ddb8cf6d40ed2aaf568c8d941fffb65c1`
  with dependencies unchanged. This separate SDK check does not rewrite the
  workspace lock or qualify live provider/kernel execution.

Reproduce dependency setup with `uv sync --frozen --all-packages --group dev
--extra openshell --extra mcp --extra neo4j`, then the backend/SDK suites and full
non-live suite. Keep the lockfile. `UV_NO_SYNC=1 make ci-lint` runs the unchanged
required Ruff/format/errors-only type gates against that already frozen setup.

These are source/contract and owned concurrency checks. They do not establish
live Gateway/kernel enforcement, complete hosted runtime operation or an OSS
release. The single root [CHAGELOG.md](../../CHAGELOG.md) records PR/release state.
