# RoE unification — auto-generate a single machine-enforceable RoE from engagement creation

- **Status:** Proposed
- **Date:** 2026-10-22
- **Scope:** `decepticon_core.types` (RoE/MachineEnforcement), `middleware/roe.py` + `middleware/egress.py`, `provision_roe.py` + `authorization.py`, MCP `mcp_server` start path, CLI `/mode` + engagement creation.

## Summary

Collapse the Rules-of-Engagement model to **one machine-enforceable RoE that is
generated mechanically from the engagement-creation data** (engagement name +
targets + optional out-of-scope). Creating an engagement *is* the scope
declaration; the generated RoE is **always enforced**; an engagement with no
scope cannot run (fail-closed). This removes the dual human/machine scope, the
optional `machine_enforcement` block, the `audit`/`warn`/`enforce` mode switch,
and the separate operator-attestation / Red-approval provisioning flow.

## Current state (as of main)

1. **Two-layer scope.** `RoE` (`types/engagement.py`) is a legal/contract
   document (`client`, dates, `testing_window`, `authorization_reference`,
   `prohibited_actions`, `escalation_contacts`, `in_scope`/`out_of_scope` as
   free-form `ScopeEntry`). A separate **optional** `machine_enforcement` block
   (`types/roe.py`, `ScopeRule` host/CIDR/glob) is the only part the runtime
   evaluates. "One scope definition, two enforcement points" (parser +
   egress), but only when the block exists.
2. **Fail-open default.** `middleware/roe.py:_load_rules_for_workspace` returns
   an empty `MachineEnforcement()` (= `mode=audit`) when `plan/roe.json` is
   absent or has no `machine_enforcement` block → **every tool call is allowed
   and only logged**. `types/roe.py` states the audit default is "for backward
   compatibility."
3. **Mode switch.** `audit` (log, allow) / `warn` (log, allow, annotate) /
   `enforce` (refuse out-of-scope). Soft modes can silently disable the gate.
4. **Heavyweight provisioning.** `provision_roe.py` + `authorization.py`:
   operator imports a reviewed RoE file (`validate_operator_roe`, requires
   `authorization_reference` + ≥1 `in_scope`), create-only ("provisioning never
   overwrites authorization"), Red needs a *separate* explicit approval
   (`--approve-red`, `.red-approved`, sha256 attestation).
5. **Entry-point drift.** Specialists are standalone graphs
   (`graph_registry.py`); run directly (MCP `start_engagement`, or the CLI
   `/agent` which lists all graphs despite its "orchestration only" comment)
   they never produce a `plan/roe.json`, so the guardrail is audit-only.

## Problems

- **Fail-open is a footgun.** Forgetting to provision an RoE means the agent
  runs against targets with no enforcement — the opposite of an authorization
  gate. The default being "for backward compatibility" confirms it is a
  pragmatic default, not the intended one.
- **Dual scope drifts.** The human `in_scope` and `machine_enforcement.in_scope`
  are maintained separately; only the latter enforces, so the legal document can
  say one thing while the runtime enforces another (or nothing).
- **`mode` weakens the gate.** `audit`/`warn` let out-of-scope calls through; an
  authorization gate that can be configured not to block defeats its purpose.
- **Inconsistent across surfaces.** Enforcement depends on *how* the engagement
  was started rather than on the scope — direct specialist runs silently drop to
  audit-only.

## Decision

A single, **machine-enforceable RoE, generated mechanically at engagement
creation from the creation data only.**

1. **Generation.** Engagement creation (MCP `start_engagement`, CLI engagement
   creation) takes `engagement_name` + `targets` (+ optional `out_of_scope`) and
   mechanically builds the RoE: each target → a `ScopeRule` (host / CIDR / URL /
   wildcard, inferred by `ScopeRule`'s existing kind detection). No separate
   human scope list; the creation inputs *are* the scope.
2. **Creation = authorization.** The act of creating an engagement with a scope
   is the authorization. There is no separate attestation import or
   `authorization_reference` requirement, and no separate Red-approval step.
3. **Always enforce.** There is no `mode`. The generated RoE is enforced
   unconditionally at both enforcement points (parser + egress).
4. **Fail-closed.** No scope (no targets) → the engagement is refused at
   creation; it never reaches a run. Because the RoE is always generated from
   creation, the "no `plan/roe.json` → audit-only" state is eliminated by
   construction.
5. **Scope expansion is HITL.** When the agent needs an out-of-scope target
   mid-engagement, it does **not** silently proceed and there is **no blanket
   unscoped flag**. It raises an explicit human-in-the-loop approval
   (`HITL_APPROVAL` slot / `HumanInTheLoopMiddleware`); on approval the RoE is
   extended and the audit ledger records the operator decision.

### Target normalization (generation-time)

Every creation input is normalized mechanically before it becomes a scope
rule, so the generated RoE reads the way DNS, CT logs and the egress
allowlist compare names: embedded lists split (`"a, b"`), URLs and
scheme-relative `//host` forms collapse to their hostname, CIDRs
canonicalize to the network address, `host:port` authorizes the host on
**all ports** (the RoE is host-scoped; a port restriction is not expressible
and must not silently narrow), hostnames lowercase / lose the DNS trailing
dot / IDNA-encode, bracketed bare IPv6 drops the brackets, a `*.` glob adds
its apex (the glob does not match the bare domain), and patterns no real
DNS/IP target can match (empty labels, a bare `*.`) are dropped with a
warning instead of sitting as dead rules in the authorization document.
Passive CT discovery uses the same normalization before querying.

### Consciously accepted consequences

- **Authorization model simplifies to "engagement creation = authorization."**
  Whoever can create an engagement self-authorizes the scope they enter. This
  fits an internal / self-serve deployment; a regulated multi-tenant red-team
  that needs a reviewer-signed authorization gate would need an additional
  deployment-level control (out of scope here).
- **Removed / demoted:** `provision_roe.py` attestation import,
  `validate_operator_roe`'s `authorization_reference` requirement, the separate
  Red approval (`--approve-red` / `prepareRedApproval` / `.red-approved`), and
  the `audit`/`warn`/`enforce` mode switch.

### Preserved

- **Legal/out-brief metadata** (`client`, dates, `testing_window`,
  `prohibited_actions`, `escalation_contacts`) stays as **optional** engagement
  metadata (default empty) so the HMAC audit ledger / out-brief record keeps its
  value. It does not gate enforcement.
- **"One scope, two enforcement points"** — the generated scope still compiles
  to both the parser gate and the egress (DNS + nftables) allowlist.
- **The HMAC-chained audit ledger** (`RoEAuditSink`) is unchanged.
- **Non-network scope is out of band.** Physical / social / cloud-console scope
  is not expressible as host patterns and is not something the agent's
  network/command tools execute; the generated RoE is explicitly the *network
  enforcement* scope.

## Migration / backward compatibility

- Existing `plan/roe.json` files: the generator writes a `machine_enforcement`
  block; a provisioned file that already has one is honored (read, not
  regenerated) for one deprecation window. A legacy file with no
  `machine_enforcement` now **fails closed** instead of audit-only — this is a
  **breaking change**, called out in `CHANGELOG.md`.
- A temporary escape hatch (`DECEPTICON_ROE_LEGACY_AUDIT=1`) may be offered for
  one minor version to restore audit-only for legacy files, then removed.

## Implementation phases (each an independent, verified PR)

1. **Scope → RoE builder (pure function) + fail-closed start gate.**
   `build_engagement_roe(engagement_name, targets, out_of_scope=())` →
   machine-enforceable RoE dict; engagement creation writes
   `<workspace>/plan/roe.json` from it, and refuses when it would be empty. No
   removals yet; generated RoE is always `enforce`. *(This PR.)*

   **Creation surfaces covered:** MCP `start_engagement` and the
   `decepticon-cli scan` path share one materializer
   (`decepticon.roe_materialization`, free of the `mcp` extra). The Go
   launcher's direct-engagement flow (`internal/engagement.createDirectEngagement`)
   already wrote its own enforce-mode `plan/roe.json` before the TUI enters —
   its richer dual-form document (human scope + machine block +
   `authorized_windows`) unifies with the minimal machine form in phase 2.
   The TUI conversation surface takes no structured targets input; its
   guardrail is the always-enforced middleware from phase 3. Filesystem-path
   targets carry no network scope: an all-paths engagement materializes an
   empty in-scope allowlist (denylist and forbidden destinations still
   enforce), while network-shaped inputs that all die as unmatched patterns
   are refused even alongside valid paths. Passive CT enrichment applies to
   plain hostnames, URL/git-remote hosts, and glob base domains.
2. **Unify the scope representation** — fold the human `in_scope`/`out_of_scope`
   into the single machine form; update `RoE`, `validate_operator_roe`,
   `event_logging` scope readers.
3. **Remove `mode`** — enforcement is unconditional; delete `audit`/`warn`
   branches in `middleware/roe.py` + `egress.py`; migrate/retire the field.
4. **Retire separate authorization/approval** — demote `provision_roe.py` and
   the Red-approval markers; creation is the authorization.
5. **HITL scope expansion** — intercept a guardrail refusal and offer an
   operator approve-to-extend path.

### Companion launcher PR (pulls parts of phases 2 and 4 forward)

A companion launcher PR (`feat/launcher-roe-mechanical`) implements the same
unification on the Go side, where the marker retirement lands early per the
maintainer's OSS responsibility-model decision (the user declares the scope at
creation and owns it; the control point is the always-enforced middleware, not
an approval ceremony):

- The launcher generates the minimal machine-form RoE from a broadened
  creation grammar (multiple targets: URLs, hostnames, CIDRs, globs, plus an
  optional out-of-scope denylist), pinned to this builder by shared golden
  test vectors (`packages/decepticon-core/tests/testdata/roe-target-vectors.json`).
- `.red-approved` and the Red-approval ceremony (`/mode red approve`, the web
  plan-approval POST, `provision_roe --approve-red`) are retired everywhere;
  readiness on the launcher picker, the TUI Red gate and the web dashboard is
  "an enforced machine scope exists". Legacy dual-form documents remain
  readable.
- IDNA diverges: Python IDNA-encodes non-ASCII hosts, Go rejects them with an
  actionable message (operators pass the punycode form).

## Test & verification plan

- Unit: `build_engagement_roe` maps hosts/CIDRs/URLs/wildcards to the right
  `ScopeRule` kinds; empty targets → raises; generated block parses via
  `MachineEnforcement.from_dict` and `evaluate_target` refuses out-of-scope,
  allows in-scope.
- Integration: MCP `start_engagement` writes `plan/roe.json` and refuses with no
  targets; a started engagement enforces (out-of-scope tool call → `ROE_REFUSED`).
- 실측: reproduce in the running `decepticon-langgraph` container — a real run
  with in-scope targets proceeds, an out-of-scope target call is refused.
