# Per-dispatch subagent step budget (dynamic recursion_limit) — design proposal

Status: proposal — needs a maintainer decision against the
[extension contract](extension-contract.md) before implementation.

## Problem

The orchestrator cannot tune a dispatched subagent's step budget. The deepagents
`task` tool schema accepts exactly `{description, subagent_type}` and actively
rejects unknown keys (`_reject_unknown_keys`), so a dispatch cannot carry a
`recursion_limit`. Budgets are fixed at build/registration time: each agent
module hardcodes `_RECURSION_LIMIT` (unified at 2000 by #1150), and the main
agent reads the operator's `DECEPTICON_RECURSION_LIMIT`.

Operators asked for per-objective headroom (a deep-research objective needs more
steps than a quick verification probe); models asked for it by burning the cap on
long report phases (#1145). Today the only lever is a global value that fits the
worst objective.

## Design forces

1. **Resource governance stays operator-owned.** The step budget exists to stop
   runaway models; letting the orchestrator raise its own ceiling unchecked
   defeats the guard (the model could stamp 100000 on every dispatch).
2. **The task contract is strict by design.** Unknown keys are rejected because
   models invent keys and dispatch labels as instructions. Any extension must be
   an explicit, validated schema field — not a free-form pass-through.
3. **The extension contract keeps scope enforcement and tool restrictions as
   capability/runtime contracts.** A budget is a runtime resource contract, so
   the ceiling lives with the runtime, not with model intent.

## Proposed design

Two operator-owned values, one model-visible argument:

- `DECEPTICON_RECURSION_LIMIT` — unchanged: the default budget for every agent.
- `DECEPTICON_RECURSION_CEILING` — new: the hard maximum any dispatch may
  request (default: the default budget, i.e. no headroom unless the operator
  opens it).

The `task` tool gains one optional field:

```
task(description, subagent_type, step_budget?: int)
```

Validation and clamping (server-side, in the dispatch middleware — never
client-computed):

```
step_budget = clamp(step_budget, floor=25, ceiling=DECEPTICON_RECURSION_CEILING)
```

- Absent `step_budget` → the registered per-agent budget (today's behavior,
  byte-for-byte).
- `step_budget` above the ceiling → silently clamped to the ceiling, with a
  system event stating the clamp (the orchestrator sees that its request was
  bounded; no silent truncation of intent).
- The clamped value flows into the subagent's `with_config({"recursion_limit":
  …})` and its `StepBudgetMiddleware(total_steps=…)` — both already accept the
  parameter through the plugin factories.

## Implementation surface

- `clients/` is unaffected. The change is daemon-side:
  1. Extend the dispatch schema where deepagents' `SubAgentMiddleware` mounts
     the `task` tool. deepagents owns `TaskToolSchema`; decepticon subclasses
     the middleware (or wraps the tool) to add the optional field — the
     subclass keeps `_reject_unknown_keys` semantics for everything else.
  2. Clamp in the dispatch node, not the tool parse, so the ceiling is read
     from the runtime env at dispatch time.
  3. Thread the clamped value into the existing plugin-factory
     `recursion_limit` parameter (the plumbing exists).
- `deepagents` upstream may accept the optional field natively; if so, the
  subclass shrinks to the clamp + system event.

## Explicitly out of scope

- Letting the orchestrator set the ceiling (force #1).
- Per-dispatch model/provider overrides (a separate contract question).
- Sub-agent nesting budgets (a child dispatching a grandchild inherits the
  same ceiling; no stacking).

## Security review checklist

- The clamp is applied in the dispatch middleware, which runs under the
  trusted runtime — agent state cannot reach it.
- The ceiling is operator env, read at dispatch time; changing it requires a
  daemon restart like every other `DECEPTICON_*` knob.
- A clamped dispatch emits an audit event (the existing system-event channel),
  so budget escalation attempts are visible in the engagement ledger.