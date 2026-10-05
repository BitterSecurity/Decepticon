# 0017. Import local target snapshots through MCP

- **Status:** Proposed
- **Date:** 2026-10-05
- **Deciders:** OSS maintainers
- **Related:** [MCP capability audit](../integrations/mcp-capability-audit-2026-10-05.md)

## Context

The installed launcher runs on the host, while the Decepticon agent reads a
selected engagement workspace mounted at `/workspace` in the sandbox. A host
repository path sent as a target is only a string; the sandbox cannot read
arbitrary host files. Coding agents need an explicit way to present local
source code to Decepticon without widening the container mount.

## Decision

Expose `decepticon_cli_import_target` on the host MCP bridge. After the
operator confirms the absolute source directory, the launcher copies a
bounded snapshot into `<engagement>/targets/<name>` and returns its
sandbox-visible `/workspace/targets/<name>` path. The destination name must
be new. The copier confines reads and writes with `os.Root`, rejects symlinks
and special files, skips dependency/cache directories and `.env*` files, and
caps file count, directory count, depth, individual file size, and total bytes.
On failure it removes the partial snapshot.

## Consequences

- Coding agents can start source-based engagements using a path the sandbox
  can actually read.
- Imports are copies. Changes in the source require a new snapshot name and
  another operator confirmation.
- Large trees and symlink-heavy repositories require a smaller prepared source
  directory. Excluded files are absent from the analysis input.

## Alternatives considered

- Mounting arbitrary host paths into the sandbox would expand the running
  container's filesystem access and require stack recreation for each target.
- Forwarding the host path as text preserves the existing unreadable-target
  failure mode.
