# ADR-0018: Preserve requested workspace before destructive uninstall

Status: Proposed

## Context

An uninstall can fail while stopping containers or backing up a workspace.
Deleting database volumes before a requested backup succeeds leaves the
operator with partial data loss. The current interactive flow also asks about
workspace preservation after destructive work has begun.

## Decision

Collect the preservation choice and check the backup destination first.
Interactive preservation defaults to keeping the workspace.
When a workspace backup is requested, stop managed services without deleting
volumes, perform the filesystem backup, and then purge service volumes.
Abort on a cancelled preservation prompt, failed service stop, failed backup,
or failed purge. Installation files and the launcher are removed only after
that preparation succeeds.

Expose `decepticon remove --yes --preserve-workspace` for explicit unattended
workspace preservation. Existing `--yes` without the new flag retains its
explicit deletion behavior. Refuse to overwrite an existing backup destination,
including a dangling symlink.

## Consequences

A failed backup leaves services stopped and database volumes intact. The
operator can fix the backup destination and retry. A successful workspace
backup contains workspace files, not a database snapshot: uninstall still
deletes database volumes afterward. No MCP execution tools or dependencies
are added by this change.

The removal runner remains an orchestration function longer than 50 lines;
the safety ordering is isolated in a short function exercised with real
Compose subprocess construction and owned filesystem fixtures.
