# Root-published RoE context bootstrap verification

Source: [PR #1200](https://github.com/BitterSecurity/Decepticon/pull/1200); not released.

A trusted host may publish `plan/roe.json` as root-owned `0444` in a root-owned
sticky planning directory before admitting a workload. `managed_start` previously
always replaced that file. A real UID1500 bootstrap therefore failed with
`PermissionError` at `os.replace`, even when its approved bytes already matched.

Retain only an exact-byte, root-owned `0444`, single-link regular file. Check
metadata before opening, open with no-follow/nonblocking flags, then bracket
bounded reads with FD and pathname identity checks. Preserve its inode, owner
and mode. A concurrent replacement is refused. Other contexts keep the existing
atomic private restore behavior, including writable or unreadable ordinary files.
No writable permissions, ownership changes, caller-controlled path, scope bypass,
provider lifecycle action or new authorization are introduced. Context retention
is not policy admission; host approval and OpenShell policy checks still apply.

## Verification

- Linux root/real UID1500 A/B: original source fails two of seven cases; final
  source passes all seven with zero errors/skips. The cases cover preserved root
  inode/mode, denied workload unlink/chmod, changed bytes, writable root context,
  another owner, hard links, symlinks and an identical-byte pathname race.
  Exact source/program/artifact hashes and executed steps were independently
  verified. [Portable result](evidence/root-published-roe-bootstrap-20261116/uid-boundary.json).
- Local affected startup/filesystem cases:82 passed. Seven root-only cases are
  skipped in non-root WSL and are separately qualified by the actual root A/B.
- Full frozen workspace `pytest -m 'not live' -q`:6,323 passed,16 skipped,
  process0. Seven skips are the separately qualified root cases. The other nine
  remain unqualified: optional Playwright(3), Obscura(1), transformers(1),
  Windows file locks(3), curl-cffi(1). Live tests are not claimed.
- Required `UV_NO_SYNC=1 make ci-lint`: Ruff check and format pass(923files),
  errors-only type gate0errors with924 existing reported warnings, process0.
- A candidate first attempted to read every existing context; a write-only
  ordinary-file regression reproduced its compatibility failure. Metadata
  eligibility before open fixes it. Final source includes that regression;
  the earlier partial full-suite run was interrupted and is not a passing gate.

Reproduce dependency setup using the unchanged workspace lock:
`uv sync --frozen --all-packages --group dev --extra openshell --extra mcp --extra neo4j`.
Run the startup/filesystem suites, full non-live suite and required lint gate.
In an owned Linux root environment, the real UID tests require only the standard
library: `python3 -m unittest discover -s packages/decepticon/tests/unit/sandbox_server -p test_managed_start_root.py`.
They fork their own UID1500 children and use no provider key or outbound target.

The initial consumer environment lacked optional full-suite dependencies and did
not pass collection; verification used the frozen full development environment.
An unrelated newer standalone Ruff also inspected Markdown examples; its broad
format output is not the required locked-tool gate. No unrelated files changed.

This qualifies context restoration and the UID boundary. It does not qualify a
live Gateway, complete hosted runtime, package publication or production release.
No version bump, deployment or hosted CI success is claimed. The single root
[CHAGELOG.md](../../CHAGELOG.md) records PR/release state.
