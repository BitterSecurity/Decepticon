# 0013. Surface operator-provided target credentials through the engagement context

- **Status:** Proposed
- **Date:** 2026-10-02
- **Deciders:** @PurpleCHOIms
- **Related:** #TBD (greybox credential feature request),
  [ADR-0002](0002-pr-tiering-and-blast-radius.md) (blast-radius tiers),
  the engagement context injector (`middleware/engagement.py`), the RoE
  guardrail (`middleware/roe.py`, `middleware/egress.py`).

## Context

A Decepticon engagement is configured by scope. The target lives in
`plan/roe.json` (`in_scope[].target`); credentials against the target app are
treated purely as loot the agents discover mid-run and persist under
`exploit/creds/`. There is no supported way for an operator to hand the agents a
login up front and have them authenticate before probing.

This blocks greybox / assumed-breach testing, which is a common real engagement
shape: the client provides a test account and wants the authenticated surface
assessed. Today the only workaround is pasting credentials into the free-text
kickoff prompt, which does not survive context summarization and is invisible to
freshly spawned specialist sub-agents (each starts with a clean context and reads
state from disk, not from conversation history). The observed failure mode is an
agent that never logs in, cannot see a credential that was mentioned once, or
forgets it between steps.

The non-obvious part is *where* the credential should live. Putting operator
secrets in front of every agent on every model call touches `EngagementContext`,
a named offensive-security guardrail surface (`CONTRIBUTING_AGENT.md` hard-rule
#5), so the data flow deserves a recorded decision.

## Decision

Introduce an optional `plan/credentials.json` document, modelled by
`CredentialBundle` / `ProvidedCredential` in
`decepticon_core.types.engagement`. Every field is optional so the bundle is
generic across auth mechanisms, which are unknowable in advance: token auth fills
`headers`, form login fills `username` / `password` / `login_url`, cookie reuse
fills `cookies`.

`EngagementContextMiddleware` reads the file and injects a
`[Target credentials — provided by operator]` block into the agent's system
message on every model call, using the same mtime-cached loader pattern already
used for `plan/deconfliction.json`. The recon, exploit, and post-exploit prompts
gain one rule directing the agent to authenticate with the provided credentials
first and re-login on expiry.

The credentials are read from the workspace file only. They are not threaded
through `config.configurable`, not added to `EngagementContextState`, and not
logged or egressed: the injector surfaces them to the model and nothing else, the
same trust basis as the deconfliction codes already injected on the same path.

This ADR covers awareness only. Seeding and persisting the shared HTTP session so
one login is reused across agents is a separate decision and a separate PR.

## Consequences

- **Easier:** greybox engagements work without prompt hacks; the credential is
  durably visible to every agent, including fresh sub-agents, every turn.
- **Harder:** operator secrets now appear in the model context. Anyone reviewing
  transcripts or model-call logs for an engagement with a populated
  `plan/credentials.json` will see the credentials there, exactly as they already
  see the deconfliction code and (in benchmark mode) the flag format.
- **Given up:** nothing is removed. The discover-and-loot credential flow
  (`exploit/creds/`) is unchanged; this is additive.
- **Migration:** none. Absent `plan/credentials.json`, behavior is identical to
  today.

## Alternatives considered

- **Add a credentials field to the `RoE` model.** Rejected: RoE's own precedent
  is to split expansion concerns (abort, data-handling, cleanup) into separate
  `plan/*.json` documents rather than grow the legally-binding scope document. A
  standalone bundle follows that precedent and keeps RoE focused on scope.
- **Pass credentials through `config.configurable` onto run state** (as the
  benchmark harness does for `target_url`). Rejected for this PR: it widens
  `EngagementContextState` and spreads secrets across the state/checkpointer
  surface. The workspace file is a single, auditable source of truth and reuses
  the existing deconfliction injection path verbatim.
- **Leave it to the kickoff prompt free-text.** Rejected: not durable across
  summarization or sub-agent spawns, which is the root cause of the failure this
  change fixes.
