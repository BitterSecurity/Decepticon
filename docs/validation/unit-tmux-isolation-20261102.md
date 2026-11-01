# Capture-timeout unit test isolation

The existing timeout case inserted a bare session name into a cache now keyed by transport. Its mocked capture ran after real initialization, leaving a local tmux shell during the #1141 full unit suite. The owned session was identified by socket/session, creation time inside the test window, no clients, and cwd inside that task worktree; only that session was removed. Other tmux sessions were preserved.

Stub initialization for this timeout-only unit case and assert initialize/capture are called exactly once and no tmux command runs. Runtime implementation, error assertions and other tests remain unchanged.

Local verification: 26 affected cases pass; Ruff check and format pass; basedpyright errors-only exits 0 (0 errors). Full fast Python suite through the shared coordinator exits 0: 6,133 passed, 3 Windows-only skips and 15 existing warnings in 197.72 seconds. Post-suite inspection finds no live `hung` tmux server/session. [Measured receipt and logs](evidence/unit-tmux-isolation-20261102/receipt.json). Actual OSS CI also runs; inspect its exact-head statuses rather than assuming the previously reported account limit still applies. No release, tag or SaaS production deployment.

[CHAGELOG.md](../../CHAGELOG.md).
