# 0008. Skillogy enforces path-prefix ACL in Phase 1a (don't wait for Phase 2)

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** @PurpleCHOIms
- **Related:** PR #613 (Skillogy default-on), `docs/design/skillogy-brain-redesign.md` OQ-3, `packages/decepticon/decepticon/middleware/skills.py` (legacy SkillsMiddleware), `packages/decepticon/decepticon/agents/middleware_slots.py::skills_sources_for`

## Context

The Skillogy v0.2 redesign deferred per-agent graph slicing to Phase 2
(`docs/design/skillogy-brain-redesign.md` §9 OQ-3):

> "Per-agent graph slicing (16 specialists each see only their region)?
> Phase 1a uses single graph + per-agent prompt phase filter. Slicing
> is a Phase 2 hardening when multi-tenancy lands."

In practice that means the `find_skill` / `load_skill` / `traverse`
tools accept any subdomain / path the agent asks for. End-to-end
dogfood verification (2026-06-06) confirmed the symptom: the `recon`
graph successfully called
`find_skill(subdomain="web-exploitation")` and
`load_skill("api-grpc")`, reading the body of
`/skills/standard/exploit/api/grpc/SKILL.md` — a skill that belongs to
the `exploit` role's scope.

The legacy `FilesystemBackend` SkillsMiddleware enforced a hard ACL
via its `sources=[...]` list: `_make_skills` defaulted to
`["/skills/standard/{role}/", "/skills/shared/"]` (see
`agents/middleware_slots.py::skills_sources_for`) and the backend
simply could not read files outside those prefixes. Migrating to
Skillogy without carrying that contract forward is a silent regression
of the OPPLAN role-discipline guarantee the codebase had pre-1.1.7.

The Phase 2 motivation (multi-tenancy) is real, but it is not
the *only* reason to scope per role:

- OPPLAN discipline depends on each phase agent reasoning over its own
  surface — recon shouldn't be reading exploit playbooks during the
  recon phase because it widens prompt-injection blast radius.
- The OSS install ships the same kill-chain framing CLAUDE.md
  describes: 16 specialist agents, fresh context per objective,
  minimum prompt surface. Cross-role skill visibility silently
  inflates "minimum prompt surface".
- The mechanism the legacy backend used (path-prefix filter) is
  cheap, deterministic, and well-understood. It does not require the
  per-tenant graph partitioning that OQ-3 deferred.

OQ-3 conflated two distinct hardening steps. **Path-prefix ACL**
(Phase 1a, cheap, deterministic) and **per-tenant graph slicing**
(Phase 2, requires per-engagement Neo4j databases or label rewrites)
are different problems. Bringing the former into Phase 1a does not
require the latter.

## Decision

Skillogy enforces a path-prefix ACL on `find_skill`, `load_skill`, and
`traverse` in Phase 1a. The ACL contract is the same one the legacy
`FilesystemBackend` honored — `skills_sources_for(role)` — so the
two backends are interchangeable from an authorization standpoint.

1. **Wire surface.** Each of the three POST endpoints
   (`/v1/skills:find`, `/v1/skills:load`, `/v1/skills:traverse`)
   gains an optional `allowed_path_prefixes: list[str] | None` request
   field. Unset / `null` preserves the existing unrestricted behaviour
   for library use, tests, and the standalone CLI; the agent factory
   always populates it.
2. **Backend (`Neo4jBackend`).** `find_skill` appends a Cypher clause
   `AND ANY(p IN $allowed_path_prefixes WHERE s.path STARTS WITH p)`
   when the parameter is non-empty. `load_skill` rejects a path that
   does not match any prefix by returning `None` (the same shape as
   "no skill at this path"), so the agent gets a clean "not found"
   instead of an authorization error it cannot reason about.
   `traverse` filters the seed path and the returned `:Skill`
   neighbours by prefix; non-`:Skill` neighbours (`:Tag`,
   `:Technique`, `:Tactic`, `:MoC`) are left visible because they are
   classification metadata, not skill content.
3. **Middleware (`SkillogyMiddleware`).** Accepts
   `allowed_path_prefixes: list[str] | None` at `__init__`. The three
   tool closures capture it and forward to the backend on every call.
   `agent_phase` remains a separate prompt-only signal (MoC summary).
4. **Builder hook (`maybe_install_skillogy`).** Accepts the same
   `skill_sources: list[str] | None` argument that `_make_skills`
   already plumbs through, so the legacy and Skillogy backends share
   one source-of-truth for "what does this role see". When no
   `skill_sources` is supplied, the helper falls back to
   `skills_sources_for(role)` — the same default the legacy backend
   uses.
   A role-bearing agent must resolve a nonempty, restricted prefix list.
   Resolver errors and invalid or empty lists stop middleware construction;
   they must never turn into unrestricted graph access. Only a caller with
   no role and no explicit scope retains unrestricted library/CLI access.
5. **`/skills/shared/` is the only intentional cross-role surface.**
   Cross-cutting expertise (OPSEC, adversary emulation, finding
   protocol, references) lives there. Phase 0 / 1a corpus authors
   keep moving cross-cutting material under `/skills/shared/` so the
   per-role hard ACL stays meaningful.

Phase 2 multi-tenancy still has work to do on top of this ACL — a
per-engagement Neo4j label or database that the path-prefix filter
cannot express. This ADR does not pre-empt that work; it puts the
deterministic, cheap layer in place first so we ship the OSS UX with
the same contract the codebase had before the Skillogy migration.

## Consequences

**Positive.**
- The legacy `FilesystemBackend` ACL contract is preserved during and
  after the Skillogy default-on transition; no silent role-scope
  regression in OSS.
- The two skill backends become interchangeable from an authorization
  perspective — useful for the pytest fixtures + standalone library
  path that still uses `FilesystemBackend`.
- Phase 2 (multi-tenancy) work can layer on top of this ACL
  rather than introducing a brand-new control mechanism.

**Negative.**
- Cross-role discovery now requires an explicit move under
  `/skills/shared/`. Authors who previously relied on the implicit
  cross-role visibility (none confirmed at time of writing, but
  possible in private plugins) will need to relocate skills or pass
  an explicit `skill_sources=[...]`.
- The `find_skill` Cypher gains a `STARTS WITH` clause on every call.
  Negligible at Phase 1a graph sizes (270 :Skill nodes); revisit if
  Phase 1b corpus growth crosses the index-needed threshold.

**Neutral.**
- The `agent_phase` field still drives the MoC summary block exactly
  as before — Phase 1a's "per-agent prompt phase filter" half of
  OQ-3 stays in place. Only the "single graph" half is sharpened.
- `allowed_path_prefixes` is optional. Standalone library, pytest,
  and the skillogy CLI continue to work without a role context.

## How to undo

Drop the `allowed_path_prefixes` field from the three request models
in `server/app.py`, the three backend methods in `neo4j_backend.py`,
the three client methods in `client/rest.py`, the middleware
`__init__`, and the builder hook. Tests in
`packages/decepticon/tests/unit/middleware/test_skillogy.py` +
`tests/unit/skillogy/test_acl.py` would need to be deleted. The
underlying Cypher would revert to today's unrestricted form.
