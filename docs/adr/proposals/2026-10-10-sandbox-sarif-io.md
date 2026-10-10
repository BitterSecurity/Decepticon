# Transfer SARIF through the engagement sandbox

- **Status:** Proposed
- **Date:** 2026-10-10
- **Deciders:** Pending maintainer decision
- **Related:** Companion issue linked in the draft PR

## Context

The current SARIF ingest dispatcher reads a path in the agent process. The
report tool writes an agent-local path. Those paths need not describe the
engagement sandbox filesystem in a remote-backend deployment. This is a
deployment-dependent compatibility concern, not a verified live incident.

## Decision

Propose routing SARIF imports and exports through the existing sandbox
download/upload protocol. Resolve workspace and graph partition from injected
state, check explicit run configuration for disagreement, and load the named
graph partition directly. Keep the existing scanner arguments and SARIF
renderer; use a private read-text protocol to accept downloaded bytes.

## Consequences

- **Easier:** SARIF paths address the engagement sandbox rather than assuming
  that the agent shares its filesystem.
- **Harder:** Validate tool-schema injection, remote error handling and report
  regeneration against both CLI and web workspace layouts before merge.
- **Given up:** Imports over 64 KiB and rendered outputs over 1 MiB are rejected.
  The import size check runs after download and does not bound transport memory.
- **Migration:** No deployment changes. The default backend must support the
  existing protocol; the separate bounded-transfer proposal is not a dependency.

## Alternatives considered

- Keep process-local IO: simplest for shared-filesystem deployments, but does
  not address separated sandbox filesystems.
- Stack this on the report-context fix: avoided by loading the graph with the
  explicit trusted partition, so this draft can be reviewed independently.
