# Capture-timeout unit test isolation

The existing timeout case inserted a bare session name into a cache now keyed by transport. Its mocked capture ran after real initialization, leaving a local tmux shell during the #1141 full unit suite. The owned session was identified by socket/session, creation time inside the test window, no clients, and cwd inside that task worktree; only that session was removed. Other tmux sessions were preserved.

Stub initialization for this timeout-only unit case and assert initialize/capture are called exactly once and no tmux command runs. Runtime implementation, error assertions and other tests remain unchanged.

Local verification: 26 affected cases pass; Ruff check and format pass; basedpyright errors-only exits 0 (0 errors). Full fast Python suite is queued through the shared full-build coordinator and remains pending. This temporary draft is not merge-qualified. No release, tag or SaaS production deployment.

[CHAGELOG.md](../../CHAGELOG.md).
