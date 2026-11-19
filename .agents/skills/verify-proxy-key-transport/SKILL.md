---
name: verify-proxy-key-transport
description: Verify per-run proxy credentials through actual loopback HTTP and SSE without external providers or customer data.
---

1. Launch: use an isolated latest-main worktree and its owned locked dev environment
   (`uv sync --locked --dev --extra mcp`, reusing the package download store).
   The tests own a temporary loopback HTTP server; no application service,
   container, database, target or external model is started.
2. Doctor: `python3 tests/ci/verify_proxy_key_transport.py --doctor` asserts that
   all three product packages and the interpreter environment belong to this
   worktree. Baseline `28b97543` must exist locally for historical checks.
   `--types` and `--types-full` reuse the canonical root type configuration.
3. Drive: run the root service-harness inspect, verify and status commands.
   The transport gate runs real sync/async HTTP and SSE requests, selected-model
   and vendor tool-turn checks, credential-failure refusals and connected suites.
   The identical six assertions must fail against immutable old middleware,
   loaded in memory without changing the worktree. Historical infrastructure
   errors, skips and incomplete regressions return2 rather than qualifying.
4. Evidence: retain source hashes, exact commands, stdout/stderr and actual exit
   codes outside the task worktree. This proves local transport behavior, not
   external upstream availability or deployment acceptance. The pinned personal
   runner rejects existing benchmark submodule directories before execution;
   report that limit, preserve the submodules, and run every exact declared
   command independently. Manual results are not a successful runner receipt.
   Repository protection and exact-head hosted checks remain separate.
5. Cleanup: the fixture closes and joins only its own HTTP server, and the
   driver deletes only its temporary JUnit file. No credentials are required.
   Preserve evidence and remove the worktree only after actual merge and
   clean/pushed/inactive checks.
