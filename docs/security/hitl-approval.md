# HITL Approval

> The human-in-the-loop checkpoint that pauses high-impact tool calls
> until the operator approves, denies, or redirects them.

## TL;DR

Autonomous execution on real client infrastructure cannot be
all-or-nothing. Some actions — credential dumping, C2 implant
deployment, pushing detection rules to a production SIEM, destructive
operations — need a human stop-the-line gate. `HITLApprovalMiddleware`
intercepts tool calls that match a declarative policy, pauses the run,
and applies the operator's decision.

It is **opt-in and off by default**: the slot factory returns nothing
unless `DECEPTICON_HITL__ENABLED` is truthy, so default engagements never
freeze waiting on a human.

Source of record:
[`middleware/hitl.py`](../../packages/decepticon/decepticon/middleware/hitl.py)
(middleware, policy, transports) and
[`agents/middleware_slots.py`](../../packages/decepticon/decepticon/agents/middleware_slots.py)
(the opt-in slot factory).

## Enabling it

`DECEPTICON_HITL__ENABLED` controls the slot. Any value other than the
falsy set — `""`, `0`, `false`, `no`, `off` (case-insensitive) — enables
the gate. When enabled, the factory builds the middleware with
`DEFAULT_HIGH_IMPACT_POLICY`, `transport=None`, the engagement id from
`DECEPTICON_ENGAGEMENT_ID` (default `default-engagement`), and the
agent's role as `agent_name`.

With `transport=None`, the middleware resolves a transport **per
request** from `state["workspace_path"]` (falling back to
`DECEPTICON_WORKSPACE_PATH`, then `/workspace`). The resolved transport is
a [`FileBackedApprovalTransport`](#transports) writing
`<workspace>/approvals/requests.jsonl` and
`<workspace>/approvals/decisions.jsonl` — a contract shared with the web
bridge. Per-request resolution matters because the standard graphs are
built once at import, but a long-lived server serves many engagements.

## Policy rules

A policy is a list of `ApprovalPolicyRule`. Each rule MUST set
`technique_tag` and/or `tool_pattern` (at least one):

| Field | Default | Meaning |
|-------|---------|---------|
| `technique_tag` | `None` | ATT&CK technique id (e.g. `T1003`); matched against the active objective's `technique_id`/`mitre`. |
| `tool_pattern` | `None` | Regex matched against the tool name with `re.search`. |
| `timeout_seconds` | `300.0` | How long to wait for an operator decision. |
| `default_on_timeout` | `"deny"` | Action applied when no decision arrives in time. |
| `reason` | `""` | Human-readable justification surfaced in the request. |

Resolution checks each rule in order: a technique-tag match (when the
objective carries a tag) wins, otherwise a tool-pattern regex match. The
first matching rule applies; a tool call matching no rule passes straight
through.

## The default policy

`DEFAULT_HIGH_IMPACT_POLICY` ships these rules:

| Match | Timeout | On timeout | Why |
|-------|---------|-----------|-----|
| `T1003` (OS Credential Dumping) | 300 s | deny | Credential dumping needs operator approval. |
| `T1486` (Data Encrypted for Impact) | 600 s | deny | Destructive operation. |
| `T1485` (Data Destruction) | 600 s | deny | Destructive operation. |
| tool `^(sliver_implant\|sliver_generate\|c2_deploy)` | 300 s | deny | C2 implant deployment needs explicit approval. |
| tool `^(sigma_to\|yara_to)_` | 180 s | deny | Pushing detection rules to production SIEM/EDR needs approval. |
| tool `^bash$` | 120 s | **allow** | Informational — bash invocation logged for audit only. |

Note the `bash` rule defaults to **allow** on timeout: it exists so bash
calls surface to the operator UI, not to block them.

## Approval flow

On a matching tool call the middleware:

1. **Builds** an `ApprovalRequest` — `request_id` (UUID), engagement and
   agent names, tool name, the tool args with secret keys redacted to
   `***REDACTED***`, the matched rule, and a reason.
2. **Submits** it via `transport.submit(request)`.
3. **Waits** with `transport.wait_for_decision(request_id, timeout)` up to
   the rule's `timeout_seconds`.
4. **Applies** the decision:
   - `allow` → pass through to the inner handler.
   - `deny` → return an error `ToolMessage` explaining the denial.
   - `redirect` → run the operator-supplied `redirect_args` instead.
   - no response within the timeout → fall back to the rule's
     `default_on_timeout` (deny for safety, except the informational bash
     rule).

Secret redaction covers keys such as `password`, `token`, `api_key`,
`private_key`, `authorization`, `credentials`, `session`, and `cookie`.

### Stack ordering

HITL sits **above** the RoE gate so RoE refusals still fire after
operator approval — operator approval cannot override RoE. This is
defense in depth: see [RoE Machine Enforcement](./roe-machine-enforcement.md).

## Transports

The middleware does not hardcode any event-log format; it talks to an
`ApprovalTransport` (a `submit` + `wait_for_decision` protocol).

- **`InProcessApprovalTransport`** — in-memory queue for tests and
  single-process UIs. Operator code calls `provide_decision` to unblock a
  pending request.
- **`FileBackedApprovalTransport`** — append-only JSONL queue. Writes
  `approval_request` records to `requests.jsonl`; tails `decisions.jsonl`
  for matching `approval_decision` records keyed by `request_id`. This is
  the transport the default factory resolves per workspace.

## See also

- [Security Controls](./security-controls.md) — the runtime-guard
  knob/default table, including `DECEPTICON_HITL__ENABLED`.
- [RoE Machine Enforcement](./roe-machine-enforcement.md) — network scope
  (OpenShell policy) and execution time windows; operator approval cannot
  widen either.
- [Threat Model](./decepticon-threat-model.md) — HITL as the
  operator-approval control.
