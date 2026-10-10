# Bound file transfers within a dedicated engagement root

- **Status:** Proposed
- **Date:** 2026-10-10
- **Deciders:** Pending maintainer decision
- **Related:** Companion issue linked in the draft PR

## Context

The sandbox protocol already provides file transfers. A retained downstream
repair proposed limiting transfers to one operator-configured engagement root.
Its filesystem assumptions do not describe every upstream deployment: the CLI
launcher can use `/workspace`, while the proposed guard requires
`/workspace/<engagement>`.

## Decision

Propose an opt-in daemon backend for a dedicated root, with 64 KiB imports,
1 MiB outputs, file descriptor traversal without following symlinks, and
regular-file ownership checks. Keep the current backend as the default.
Approve the topology, response-error contract and environment switch before
treating this implementation sketch as a supported backend.

## Consequences

- **Easier:** Bound transfer reads and regenerate owned report outputs.
- **Harder:** Dedicated root ownership and protocol compatibility require
  qualification. Download and upload traversal stay in long methods so file
  descriptor cleanup and the publication boundary are visible together.
- **Given up:** This proposal does not support a shared root. Replacement of
  existing files cannot exclude external writers; no universal CAS guarantee.
- **Migration:** None while disabled. Enabling requires a dedicated workspace
  and maintainer acceptance; no deployment configuration is changed here.

## Alternatives considered

- Keep existing transfers: simplest compatibility, without these added bounds.
- Apply the guard unconditionally: rejected because launcher and shared-root
  deployment compatibility has not been established.
