# Design: RoE boundary conversion to OpenShell kernel enforcement

Date: 2026-11-03 · Status: draft for review · Baseline: `main` @ `cd252a5` (post PR #1139)
Verification artifact of: prototype compiler + gateway grammar probes (see §5, §6)

## 1. Goal

Make OpenShell the **single network enforcement authority** for machine RoE in OSS
Decepticon. The in-container nftables egress path (`sandbox_kernel/egress.py`) is
retired for OpenShell-backed engagements; the app-layer parser gates stay, scoped to
what a kernel policy cannot express. RoE provisioning becomes: deterministic
compile → policy attach → admission-verified policy hash, all fail-closed.

Non-goals: replacing the tmux session stack (transport-neutral, kept), replacing
the audit ledger, or weakening any current guarantee.

## 2. Current boundary inventory (what moves, what stays)

| # | Boundary | Where | Authority today | Fate |
|---|----------|-------|-----------------|------|
| 1 | Gated tool admission, per-target/command evaluation | `middleware/roe.py:471-540` (`_evaluate`, `_record`) | parser (app) | **Stays** — time windows, command patterns, concurrency, cadence, HITL are not kernel-expressible |
| 2 | Network egress enforcement | `sandbox_kernel/egress.py:135-191` (`render_nftables`), applied via daemon `POST /provision_egress` (`sandbox_server/app.py:491-516`) | nftables in-container (NET_ADMIN) | **Replaced** by OpenShell `network_policies` (default-deny, binary×host scoped) |
| 3 | Egress provisioning loop | `middleware/roe.py:432-469` (`_maybe_provision_egress`) | best-effort, noisy retry on OpenShell (calls `provision_egress` which raises, `openshell_sandbox.py:115-116`) | **Redesigned** (§4) |
| 4 | Filesystem confinement | container caps/mounts (`docker-compose.yml`), `middleware/filesystem.py:208-209` (roe.json lock) | mounts + app gate | FS profile → OpenShell `filesystem_policy` + `landlock`; roe.json lock stays (app) |
| 5 | Process identity | image `USER` / caps | image | → OpenShell `process.run_as_user` (static) |
| 6 | Audit ledger | `middleware/_audit_sink.py:79-156` | HMAC chain (app) | Stays; OpenShell denial logs (OCSF) are a supplement, not the ledger |
| 7 | RoE materialization / immutability | `roe_materialization.py:50-91`, `provision_roe.py:19-58` | create-only, atomic | Stays unchanged — kernel policy is derived FROM `plan/roe.json`, never edited by agents |
| 8 | DNS allowlist | `sandbox_kernel/egress.py:194-205` (`render_dns_allowlist`) | rendered, **never applied** (no consumer) | **Dropped** — OpenShell endpoint rules are DNS-hostname based; resolves the dead contract |
| 9 | OSINT always-allow | `middleware/egress.py:91-98` | nftables allow | → separate `osint_allowance` network policy section (binaries-scoped) |
| 10 | Emergency abort | `.abort` marker (`roe.py:129-135,397-430`) | app | Stays; complements `openshell sandbox stop` as a host-side kill switch |

Parser gate list that must survive conversion (from `docs/security/roe-machine-enforcement.md:70-80`):
`forbidden_command_patterns`, `authorized_windows`/`blackout_windows`,
`max_concurrent_connections`, `min_inter_request_delay_ms` (+jitter), HITL technique
gates. The kernel enforces the *network superset bound*; the parser narrows behavior
inside it ("kernel is the truth-set ceiling, app gate is a subset" — the red-agent
model, `~/projects/red-agent-v1/src/red_agent/interface/service.py:696-706`).

## 3. RoE → SandboxPolicy compiler (deterministic)

Pure function, no inference: same `MachineEnforcement` → same policy bytes.
Prototype: verified against the real evaluator this session (§6).

| RoE construct | Policy construct | Notes |
|---|---|---|
| in_scope `host` | endpoint `{host, ports: [443], protocol: rest, enforcement: enforce, access: full}` | lowercase, trailing dot stripped (parser `types/roe.py:399` parity) |
| in_scope `*.sub.base` glob | wildcard endpoint `{host: '*.sub.base', ...}` | OpenShell wildcard = **single label** (`*.base` matches `a.base`, not `a.b.base`) — **identical** to parser `matches_domain_glob` (`types/roe.py:349-357`, `[^.]+`) |
| in_scope mid-glob (`api-*.base`) | wildcard endpoint (schema-accepted) | runtime match behavior needs live re-verify (§6 blocker) |
| in_scope CIDR/IP | hostless endpoint `{port: P, allowed_ips: [cidr]}` | forward-proxy path; `protocol: tcp` + `allowed_ips` is **invalid** (gateway-verified) |
| out_of_scope | nothing emitted | default-deny covers it; overlap with in_scope = **compile error** (kernel would be wider than parser) |
| forbidden_destinations (+default IMDS 5) | nothing emitted + compile-time coverage check | link-local/metadata are **kernel invariants** — always blocked upstream regardless of rules |
| sensitive TLDs (`.gov/.mil/.edu/.int`) | nothing emitted + compile-time coverage check | a default-deny kernel cannot "deny a TLD" structurally; the compiler must refuse any in_scope rule whose match set intersects a sensitive TLD unless `allow_sensitive_tlds` |
| OSINT hosts | separate `osint_allowance` policy section | binaries scoped to web tooling |
| filesystem / process | static `filesystem_policy` + `landlock` + `process` | sandbox recreation to change |

**Fail-closed compile rules** (refuse to emit a policy when):
1. an in_scope rule's match set covers a forbidden destination, a sensitive TLD host,
   or an out_of_scope pattern (parser denies, kernel would allow — kernel must never
   be the wider bound);
2. a glob is not enumerable against a forbidden/TLD entry (undecidable ⇒ refuse);
3. in_scope is empty (deny-all network — legal but must be explicit, mirroring
   `build_engagement_roe`'s `ValueError` on empty scope, `types/roe.py:268-288`).

Ports: parser is host-level (any port); kernel endpoints pin ports. Compiler emits
`ports: [443, 80]` by default plus any ports extracted from in-scope URL rules
(`network_scope_pattern` accepts explicit default-port URLs). Documented residual
divergence: parser allows arbitrary ports on in-scope hosts; kernel pins the list.
Resolution (recommended): extend `MachineEnforcement` with optional per-rule ports in
a follow-up so both layers share one contract.

## 4. Provisioning flow redesign

Today (`middleware/roe.py:306-315 → _maybe_provision_egress`): compile nftables
policy → `build_sandbox_backend().provision_egress(policy)` — on OpenShell this
raises every turn and is swallowed (retry loop). New flow, replacing the call site:

1. `before_agent` (sync + async): load `plan/roe.json` (unchanged) → resolve the
   run's sandbox backend **with the run config** (fixes the ambient-config call at
   `roe.py:457`, which is also a latent multi-tenant bug on the HTTP path).
2. If backend `egress_authority == "openshell"`:
   a. compile policy (§3) — compile errors refuse the engagement (fail-closed, no
      network without a policy);
   b. push policy: `openshell policy set <sandbox> --policy <yaml> --wait` (Rust CLI
     subprocess — the SDK 0.1.5 has no push wrapper; gRPC `UpdateConfig` stubs are
     internal API. Same route red-agent validated, `service.py:91-92`);
   c. verify: revision `Loaded` + policy hash recorded; admission already re-checks
     `effective_policy_hash` every request (`openshell_sandbox.py:96-98`). Bind the
     admitted hash to `sha256(plan/roe.json bytes)` via the host admission resolver —
     the missing "RoE ↔ policy hash" link called out in
     `docs/validation/openshell-session-adapter-20261026.md:83-98`.
   d. record provisioning in the audit ledger (`_record`-style event: policy
     revision, hash, latency).
3. If `egress_authority` is not openshell (legacy HTTP daemon): current behavior
   unchanged. `DECEPTICON_EGRESS_DISABLE` keeps its HTTP-path meaning but is
   **rejected on the openshell path** (a kernel policy always exists; silent opt-out
   is not representable).
4. Scoped dynamic updates (discovered hosts): use incremental `policy update
   --add-endpoint/--rule-name` or `PolicyMergeOperation` patches instead of
   replacing tables; each update is `--wait`-verified. Wildcard endpoints remain the
   primary mechanism so recon discoveries inside an approved wildcard need **no**
   churn (single-label semantics bound them).

## 5. Verified this session (2026-11-03, trial gateway v0.1.5, docker driver)

Semantic matrix — 20 probes, real `decepticon_core.types.roe.evaluate_target` vs
compiled-policy model:

- 17/20 agree, including: single-label wildcard (+deep-subdomain rejection), apex
  non-coverage, trailing-dot and case normalization, forbidden (incl. IMDS with
  trailing dot), sensitive TLDs, CIDR in/out, explicit out-of-scope, default-deny.
- 3 divergences, all resolved at grammar level by probes: pinned ports (→
  `ports: [80,443]` accepted), mid-pattern glob (→ schema-accepted), hostless CIDR
  (→ accepted without `protocol: tcp`).

Gateway validator probes (real `openshell sandbox create`, v0.1.5):

| Probe | Verdict |
|---|---|
| `allowed_ips` + `protocol: tcp` + L7 fields | **Rejected** — "protocol tcp requires a DNS hostname; hostless allowed_ips endpoints are supported only by the forward proxy"; "protocol tcp does not support access/enforcement" |
| wildcard endpoint `*.staging.example.com` | Accepted |
| mid-pattern glob `api-*.example.com` | Accepted (runtime match = pending live re-verify) |
| `ports: [80, 443]` | Accepted |
| hostless `{port: 443, allowed_ips: [10.10.0.0/24]}` | Accepted |

Kernel invariants (from the published policy contract, re-checked via the skill):
loopback, link-local `169.254.0.0/16`, unspecified, and cloud metadata addresses are
**always blocked** as upstream destinations regardless of `allowed_ips` — the RoE
IMDS-forbidden contract maps onto a structural guarantee, not a rule.

Baseline tests on this branch: `uv run pytest` over `test_roe.py`, `test_egress.py`,
`test_openshell_sandbox.py`, `test_factory.py`, `test_roe_builder.py`,
`test_roe_wildcards.py` → **227 passed** (0 failed).

Runtime behavioral tests (DENIED logs, deep-subdomain live match, binary-scoping
live) are **blocked by the environment**: every sandbox create (even a minimal
policy) dies at `seccomp notification probe: Resource busy (os error 16)` →
`ControlSupervisorStartFailed`. This is a gateway runtime issue, not a policy issue
(policy validation passes before provisioning). Prior live evidence of kernel
enforcement exists at `tests/live/openshell/test_runtime.py:54-198` (2026-10-26:
declared-port-only hits, tmux curl cannot bypass) and red-agent's observed
`chain=permission denied` binary scoping (2026-10-27). Retest plan is §8.

## 6. Gaps and risks

1. **PR #1140 (open)** touches `middleware/roe.py` (egress-admission fail-closed).
   Coordinate: land #1140 first or rebase this work on it; the new flow supersedes
   its provisioning path for openshell but keeps its HTTP-path semantics.
2. **Production admission resolver is still missing** (`factory.py:56-64` has no
   caller). This design assumes the host control plane lands it; without it,
   OpenShell mode cannot be admitted at all (existing fail-closed behavior).
3. **Port contract divergence** (parser any-port vs kernel pinned ports) — residual,
   documented; optional follow-up adds ports to `MachineEnforcement`.
4. **IP-literal / CIDR scopes go through the forward proxy** — enforcement strength
   must be re-verified live (transparent TCP is not available hostless; proxy hop is
   now part of the TCB).
5. **Dynamic re-provisioning** (recon discovers a host outside wildcards): requires
   incremental policy updates + re-ACK; until implemented, engagement stays on the
   initially-attached policy (tighter, not looser — safe default).
6. **`render_dns_allowlist` dies with nftables** — docs
   (`roe-machine-enforcement.md`, `middleware/egress.py` docstrings) must drop the
   DNS-allowlist narrative in the same PR.
7. **Daemon `POST /provision_egress` + NET_ADMIN cap** can be retired for
   openshell-only deployments later (separate PR; HTTP path keeps it for now).

## 7. Implementation plan

Phase 1 — compiler + routing (this branch):
- `packages/decepticon/decepticon/middleware/policy_compiler.py` (new): pure
  `MachineEnforcement → policy dict → YAML bytes`, deterministic ordering, fail-closed
  checks (§3). Unit tests: property matrix (parser ↔ policy-model agreement),
  coverage-refusal cases, byte-stability.
- `middleware/roe.py`: `_maybe_provision_egress` branches on `egress_authority`;
  openshell path = compile + push + verify + ledger event; run-config threading fix.
- `backends/openshell_sandbox.py`: replace `provision_egress` raise with the
  admission-verified flow (or explicit `NotSupportedError` behind the new flow).
- `tests/unit/backends/test_openshell_sandbox.py`, `tests/unit/middleware/test_roe.py`:
  update expectations; add compile/push/verify fakes (CLI subprocess mocked).

Phase 2 — docs + cleanup: `roe-machine-enforcement.md`, `sandbox-isolation.md`,
`security-controls.md`, threat-model rows; drop dead `_warn_message` (`roe.py:162-176`)
and the `"audit/warn mode"` remnant (`sandbox_kernel/egress.py:407`).

Phase 3 (host control plane, out of repo): admission resolver, RoE-hash binding,
policy ACK/revocation lifecycle (the `docs/validation/openshell-session-adapter-20261026.md`
"remaining production gates").

## 8. Runtime re-verification (when the gateway is healthy)

```bash
# 1. recreate the verification sandbox from the compiled policy
openshell sandbox create --name roe-verify --policy policy.yaml --detach
# 2. in-scope allowed / out-of-scope, deep-subdomain, IMDS, port-pin, binary-scope → DENIED
openshell sandbox exec roe-verify -- curl -sS https://example.com -o /dev/null -w '%{http_code}'
openshell logs roe-verify --since 5m --source sandbox   # NET:OPEN / HTTP:* [MED] DENIED
# 3. mid-glob behavior: api-test.example.com allowed vs api.x.test.example.com denied
# 4. policy set --wait round-trip; revision Loaded; hash binding check
```

Acceptance: every parser-deny probe produces a kernel `DENIED` record; every
parser-allow probe produces no `DENIED` record; `policy list` shows the compiled
revision `Loaded` with the expected hash.
## 9. Implementation record (2026-11-03, this branch)

Phase 1 is implemented on this branch:

| File | Change |
|---|---|
| `decepticon/middleware/policy_compiler.py` (new) | Deterministic `MachineEnforcement` → OpenShell policy mapping + canonical sha256; fail-closed coverage checks (forbidden / sensitive-TLD incl. the rule's own target / out-of-scope overlap / non-leading globs / non-default-port URL rules); `DECEPTICON_OPENSHELL_ROE_PORTS` / `..._ROE_BINARIES` narrowing knobs; `dcpt-<slug>-<roe8>` sandbox naming |
| `decepticon/backends/openshell_local.py` (new) | Operator-opt-in local trusted resolver (`DECEPTICON_OPENSHELL_LOCAL=1`): compile → born-confined `SandboxSpec.policy` create via SDK proto → `wait_ready` + policy-version check → session daemon start through the admitted exec path → admission binding `effective_policy_hash = compiled hash`, `approval_hash = sha256(roe.json)`; backend cache per `(workspace, roe hash)` with revalidation |
| `decepticon/backends/factory.py` | Lazily installs the local resolver on the operator's env opt-in before failing closed |
| `decepticon/backends/openshell_sandbox.py` | Public `current_admission()` for provisioning verification |
| `decepticon/middleware/roe.py` | `_maybe_provision_egress` branches on `egress_authority`: openshell → verify admitted policy hash vs compiled RoE hash (`egress_provisioned` audit event; mismatch fails loudly, unmarks, retries); HTTP/nftables path unchanged |
| `packages/decepticon/pyproject.toml` + root `pyproject.toml` | `openshell>=0.1.5,<0.2` extra (`decepticon[openshell]`) |

Verification on this branch:

- Full unit suite: **6,154 passed, 3 skipped (Windows-only), 0 failed** (`packages/decepticon/tests/unit`, `decepticon-core/tests`, `decepticon-sdk/tests`, `-m "not slow"`).
- New tests: policy-compiler matrix (mapping, determinism, 10 fail-closed cases, port/binary knobs, DNS-label naming), resolver lifecycle with a fake gateway client (born-confined spec captured, hash binding, cache reuse, RoE-change → new sandbox, missing/uncompilable RoE refusal, install gating), RoE provisioning branch (hash match → audit + mark-once; mismatch → loud retry; uncompilable → retry; HTTP path untouched).
- Gateway grammar probes (0.1.5 + 0.1.6 gateways): `ports: [80,443]`, mid-pattern glob schema acceptance, hostless `allowed_ips` (forward proxy), rejection of `protocol: tcp` + `allowed_ips` + L7 fields — the compiled policy passes validation up to the sandbox provisioning stage.
- SDK: **0.1.6 is the current stable** (PyPI 2026-10-28; docs.nvidia.com "Latest (v0.1.6)"). `SandboxSpec.policy` attach was verified against both 0.1.5 and 0.1.6 wheels; no `UpdateConfig` push wrapper exists in either (raw stub or CLI remains the live-update path). Pin documents the SDK≤gateway minor guidance.
- v0.1.6 release notes (for the record): gateway auth now mandatory (`allow_unauthenticated_users` dev opt-out), sandbox→supervisor control-connection flip requiring supervisor+runtime binaries paired, sandbox `suspend`/`resume`, Python SDK ships credential-driver proto bindings, OCSF audit events for authorization denials + credential-misuse findings, middleware `binding_policy`.
- **Live kernel-enforcement replay is blocked by the host, not by this change**: WSL2 kernel `6.18.54.1-microsoft-standard-WSL2` refuses `seccomp(2, SECCOMP_FILTER_FLAG_NEW_LISTENER)` with `EBUSY` even for a minimal no-op filter, reproduced on the host outside Docker (so neither the gateway version nor Docker 29.9 is implicated). Every OpenShell sandbox therefore dies at the sandbox qualification probe (`seccomp notification probe: Resource busy`) on 0.1.5 and 0.1.6 gateways alike. The same host ran the committed live kernel evidence on 2026-10-26/27, so the kernel regressed in between (WSL kernel update). §8's retest plan applies unchanged once the host runs a kernel where the probe passes.
