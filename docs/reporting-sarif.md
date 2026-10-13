# SARIF sandbox transfers

`report_sarif(engagement_id, output_path)` writes to the same sandbox that the
current run uses. `kg_ingest(scanner_kind="sarif", path=...)` downloads from
that sandbox before parsing with the built-in adapter. Registered SARIF adapter
overrides and other scanner adapters retain their existing process-local IO.
This fixes the separated-filesystem mismatch described in
[#910](https://github.com/BitterSecurity/Decepticon/issues/910).

The implementation follows the direction proposed by @ChazyTheBest in
[#911](https://github.com/BitterSecurity/Decepticon/pull/911), with independent
qualification through the maintained HTTP client, FastAPI routes and daemon.
It does not depend on the dedicated-root proposal in #909 or change the daemon
transport, deployment topology, dependencies, or public model arguments.

## Scope and compatibility

- Trusted injected state supplies `engagement_name`, `workspace_path`, and the
  optional distinct `kg_engagement` graph partition. A missing graph label uses
  the engagement name. Explicit invalid labels are refused.
- Both the launcher's `/workspace` and the web's `/workspace/<engagement-name>`
  layout are supported. Paths use the existing engagement-filesystem mapping.
- MCP-started runs supply `/workspace` in both initial state and run config.
- Conflicting explicit run scope is refused before graph loading or file IO.
  Per-run `sandbox_url` and `sandbox_token` select the backend, including when
  a subagent has no ambient engagement context.
- `engagement_id` remains report metadata, not a selector for another graph.
- Parent directories are created and existing report files are replaced.
  The maintained protected `plan/roe.json` write policy still applies.
- Sandbox errors reach the caller: report errors raise and ingest errors appear
  in its existing JSON error response. Malformed/missing transfer replies fail.

## Limits

Inputs are limited to 64 KiB of UTF-8 bytes; rendered reports are limited to
1 MiB. A report larger than 64 KiB can be exported but cannot be reimported
through `kg_ingest`. These are compatibility limits, not transport memory
bounds: the input check happens after download, and output rendering happens
before the size check. No streaming or daemon transfer-size limit is added.

Filesystem semantics remain those of the chosen sandbox. In particular, the
current daemon follows native filesystem links and writes non-atomically.
This change does not establish hostile-link confinement or compare-and-swap.

## Local qualification

`packages/decepticon/tests/unit/reporting/test_sarif_remote.py` executes real
ToolNode injection, HTTPSandbox serialization, FastAPI upload/download routes,
and DaemonSandbox file methods. Only the network endpoint, container mount
namespace and graph-store environment are fixtures; files live under owned
temporary directories. It covers export, regeneration, reimport, both workspace
layouts, distinct graph partitions and per-run endpoints, protected paths,
invalid scope, backend errors and byte budgets. This is an in-process HTTP
integration test, not a deployed-container or live-model acceptance claim.
