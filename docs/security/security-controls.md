# Security Controls

> One-page reference for the runtime guards in the agent middleware
> stack: what each control does, its default posture, the knob that
> changes it, and how to verify it is active.

## TL;DR

Every agent assembles its middleware stack by walking `MiddlewareSlot` in
declaration order; a role only instantiates the slots in its
`SLOTS_PER_ROLE` set. Five of those slots are the runtime security
guards, with role-specific slots and optional HITL. The safety-critical set
is defined in code; plugins cannot silently replace those slots.

Source of record:
[`contracts/slots.py`](../../packages/decepticon-core/decepticon_core/contracts/slots.py)
(slot enum, `SAFETY_CRITICAL_SLOTS`, `_BASE_SLOTS`),
[`middleware/hitl.py`](../../packages/decepticon/decepticon/middleware/hitl.py),
and
[`middleware/budget.py`](../../packages/decepticon/decepticon/middleware/budget.py).

## Controls overview

| Control (slot) | Default posture | Knob | How to verify |
|----------------|-----------------|------|---------------|
| **RoE guardrail** (`roe-guardrail`) | Gated calls enforce machine rules; missing/invalid configuration is refused. Safety-critical. | `<workspace>/plan/roe.json:machine_enforcement`; legacy `mode` values are ignored | Add a target excluded by the reviewed rules; expect a `[ROE_REFUSED]` `ToolMessage` and an `OUT_OF_SCOPE` row in the [audit ledger](./audit-ledger.md). |
| **RoE egress guardrail** (network) | OpenShell kernel policy attached at sandbox creation; the admitted policy hash is verified against the compiled RoE hash every turn. | The trusted OpenShell resolver compiles `plan/roe.json:machine_enforcement` into `SandboxPolicy` (`middleware/policy_compiler.py`); backends without OpenShell authority fail loudly and retry. The retired `DECEPTICON_EGRESS_DISABLE=1` flag only logs a warning. | Inspect the `egress_provisioned` audit event (policy hash) and exercise the actual kernel boundary. A parser refusal still does not prove network policy was applied. |
| **Untrusted-output quarantine** (`untrusted-output`) | Always on, every role. Safety-critical. | None — in `_BASE_SLOTS`; only a plugin with `DECEPTICON_ALLOW_SAFETY_OVERRIDES=1` can replace it. | Read a hostile banner; tool output is wrapped in the `<UNTRUSTED_TOOL_OUTPUT>` envelope. See [prompt-injection-defense](./prompt-injection-defense.md). |
| **Prompt-injection shield** (`prompt-injection-shield`) | Always on, every role. Safety-critical. | None — in `_BASE_SLOTS`; same override gate as above. | Deny-listed payloads in tool output are wrapped before reaching the next model call. |
| **HITL approval** (`hitl-approval`) | Off by default — opt-in. Safety-critical when enabled. | `DECEPTICON_HITL__ENABLED` (truthy enables) | Enable it, trigger a `T1003` objective or a `sliver_*` tool; the run pauses for an approval written to `<workspace>/approvals/requests.jsonl`. See [hitl-approval](./hitl-approval.md). |
| **Budget enforcement** (`budget`) | No-op unless a cap is set. In `_BASE_SLOTS`. | `DECEPTICON_BUDGET__ENGAGEMENT_USD` / `DECEPTICON_BUDGET__PER_AGENT_USD` (a positive cap enables it) | Set a low cap; expect a `budget_warning` stream event at the soft threshold and `BudgetExceeded` (run terminates) at 100%. |

### What "safety-critical" means

`SAFETY_CRITICAL_SLOTS` lists the slots a plugin can only replace or
disable when `DECEPTICON_ALLOW_SAFETY_OVERRIDES=1` is set: engagement
context, RoE enforcement, untrusted-output, prompt-injection shield,
sandbox notification, opscontrol notification, and HITL approval. The gate exists so an
accidentally-installed plugin cannot silently subvert the safety story.
`DECEPTICON_ALLOW_SAFETY_OVERRIDES` is documented in the
[library-usage](../library-usage.md) and
[plugin-author-guide](../plugin-author-guide.md) docs.

## Environment variables

These runtime-guard knobs are otherwise undocumented; their defaults are
read in `budget.py` and the audit sink.

### Budget

| Variable | Default | Effect |
|----------|---------|--------|
| `DECEPTICON_BUDGET__ENGAGEMENT_USD` | `0.0` | Hard USD cap for the whole engagement. Non-positive = disabled. |
| `DECEPTICON_BUDGET__PER_AGENT_USD` | `0.0` | Hard USD cap per specialist agent. Non-positive = disabled. |
| `DECEPTICON_BUDGET__SOFT_WARN_AT_PCT` | `0.7` | Fraction of a cap at which a `budget_warning` stream event fires (the agent proceeds). |
| `DECEPTICON_BUDGET__POLL_SECONDS` | `5.0` | How often LiteLLM spend is re-queried; spend is cached for this interval. |

The middleware is a no-op unless `ENGAGEMENT_USD` or `PER_AGENT_USD` is
positive. It blocks LLM calls only, never tool calls — an in-flight tool
round-trip and a final synthesis message can still complete before the
next inference fails. Spend is read through LiteLLM's `/spend/tags` API using the configured
proxy URL/key; the middleware does not query its internal SQL tables. Spend
lookup errors can yield zero/no enforcement for that turn. Separately, with a
positive cap, an unpriced metered Cheaper Inference/Kimi credential chain or
a failed credential-chain check blocks the model call. These are different
failure boundaries; do not describe all budget failures as fail-open or all as
fail-closed.

### Audit ledger

| Variable | Default | Effect |
|----------|---------|--------|
| `DECEPTICON_AUDIT_HMAC_KEY` | unset (`hmac` field empty) | Operator secret that binds the audit chain via HMAC-SHA-256. |
| `DECEPTICON_ROE_AUDIT_PATH` | `<workspace>/audit/roe-decisions.jsonl` | Pins the ledger to a deterministic path. |

See [audit-ledger](./audit-ledger.md) for the chain format and
verification.

### HITL

| Variable | Default | Effect |
|----------|---------|--------|
| `DECEPTICON_HITL__ENABLED` | unset (disabled) | Truthy enables the approval gate. Falsy spellings (`""`, `0`, `false`, `no`, `off`) keep it off. |

See [hitl-approval](./hitl-approval.md) for the policy and approval flow.

## See also

- [RoE Machine Enforcement](./roe-machine-enforcement.md)
- [Audit Ledger](./audit-ledger.md)
- [HITL Approval](./hitl-approval.md)
- [Threat Model](./decepticon-threat-model.md)
- [Prompt-Injection Defense](./prompt-injection-defense.md)
