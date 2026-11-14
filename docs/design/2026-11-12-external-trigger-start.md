# External trigger start — design proposal

Status: proposal — design only, needs maintainer decisions before implementation.
Baseline: `main` after the time-window (#1182) and scheduled-engagement (#1183)
work. No code in this document.

## Goal

Start an engagement run from an **external event** — not only on a clock
(`decepticon-cli schedule`, #1183) or a human request. Examples an operator
asked for: a new asset appears on the attack surface, a CI pipeline publishes a
build, a monitoring alert fires, a ticket moves to a state.

This is the third start mechanism of continuous engagements, alongside:

- **scheduled start** — LangGraph cron (#1183),
- **time-window keep** — `TimeWindowMiddleware` + `time-window-resume` (#1182),
- **external trigger** — this document.

Each start mechanism only *starts* a run; the time window and the OpenShell
policy keep it in bounds. A trigger never widens scope.

## Integration boundary

Use the existing authenticated run-creation API, as the schedule CLI does.
An event source that already validates and maps events can call that API through
an authorized adapter. Other sources need a **trigger receiver** that:

1. authenticates the event,
2. maps it to an already-approved engagement,
3. checks the event target against that engagement's RoE scope,
4. starts a run (`runs.create` for one-off, or reuses the campaign thread),
5. deduplicates repeated deliveries.

```
event source ──HTTP(S)──▶ trigger receiver ──langgraph_sdk──▶ run on the engagement
  (ASM / CI / alert)        auth · map · scope · dedup
```

## Boundaries (non-negotiable)

- **Scope is immutable.** The event selects *which approved engagement* runs; it
  cannot create an engagement, edit `plan/roe.json`, or add a target. An event
  whose target is not in that engagement's scope is rejected, not auto-added.
- **Authenticated events only.** Every source authenticates (HMAC signature or a
  per-source bearer token); unsigned or unknown-source events are dropped.
- **Operator authorization still holds.** A trigger does not bypass the human
  review/delivery gates; it schedules in-scope work the operator already
  approved for that engagement.
- **Bounded blast radius.** Per-engagement rate limit and `multitask_strategy`
  (default `reject`) so a noisy source cannot launch a storm of runs.
- **Current authority.** Revalidate the bound tenant, engagement approval and
  scope revision when dispatching, including delayed or duplicate deliveries.
  A revoked binding or approval starts no work. Hosted adapters retain their
  existing entitlement and credit-reservation gates; this adds no SaaS billing
  requirement to the OSS CLI.
- **One network authority.** Event eligibility selects an approved engagement;
  it does not replace the OpenShell execution policy or revive the retired
  application-layer RoE middleware. Do not introduce a second URL/glob firewall.
- **Provider-independent integration.** No GitHub Actions runner or CI/CD job is
  required. CI is only one possible event source, alongside manual operators,
  asset monitors and other authenticated services.

## Mapping model

An operator registers a **trigger binding** per engagement (stored with the
engagement, like a schedule):

| Field | Meaning |
|---|---|
| `source` | which event source (asm, ci, alert, generic) |
| `assistant` | graph to run (e.g. `recon`) |
| `engagement` / `workspace` | the approved engagement the run binds to |
| `mode` | `reassess` (fresh thread) or `campaign` (one thread), mirroring #1183 |
| `filter` | which events fire (e.g. asset type, CI status), plus a scope check |
| `dedup_key` | event field(s) that make two deliveries "the same" |

The receiver resolves an event to a binding, runs the scope check against the
engagement's RoE, then starts the run exactly like `schedule` does.

## Open decisions (for PR review)

1. **First source.** Which event source ships first? Candidates: RedOps/EASM
   asset-discovery ("new in-scope host found → recon it"), CI build published,
   generic signed webhook. Recommendation: start with one concrete source, not a
   generic bus.
2. **Where the receiver runs.** A new small service in compose next to the
   `time-window-resumer`, or a route on an existing service. Recommendation: a
   separate minimal service (same pattern as the resumer), so its auth surface
   is isolated.
3. **Event → scope check.** Exact rule for "is this event's target in scope":
   associate the event with the same trusted approval/policy projection used by
   the engagement. OpenShell remains the execution authority. Reject vs
   hold-for-operator on an event that does not match the approved engagement.
4. **Delivery semantics.** At-least-once with dedup (recommended) vs
   exactly-once. Dedup store location and TTL.
5. **Auth per source.** HMAC vs bearer; secret storage; rotation.
6. **Relationship to schedules.** Is a trigger binding just a schedule with an
   event source instead of a cron, sharing the `decepticon_schedule` tag and the
   same CLI (`decepticon-cli schedule create --on <source>`)? Recommendation:
   yes — one surface, one tag, one list.

## Out of scope here

- The event sources themselves (RedOps ASM, CI) — this proposal consumes their
  events, it does not build them.
- Baseline diff of the resulting run — see
  [incremental re-assessment](2026-11-12-incremental-reassessment.md).

## Verification plan (when implemented)

- Receiver unit tests: auth accept/reject, scope accept/reject, dedup, rate
  limit, mapping to the right engagement.
- Real server: a signed event starts exactly one in-scope run on the bound
  engagement; an out-of-scope or unsigned event starts none.
- Repeated and delayed deliveries, revoked approvals, tenant mismatches and
  exhausted hosted funding start no unauthorized work. Deduplication and run
  dispatch must account for crashes between accepting and starting an event.
