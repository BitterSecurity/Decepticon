# RoE machine enforcement

The operator/client owns reviewed `plan/roe.json`. Soundwave can prepare a
separate candidate; it cannot rewrite that protected scope. See
[provisioning](../roe-provisioning.md) and the
[extension contract](../design/extension-contract.md).

This guide follows the current
[MachineEnforcement schema/evaluators](../../packages/decepticon-core/decepticon_core/types/roe.py)
and [RoEGuardrailMiddleware](../../packages/decepticon/decepticon/middleware/roe.py).
Some source comments and older design notes describe an audit/warn/enforce
migration. Those comments do not describe the current executable policy.

## Missing configuration and legacy modes

Gated tool calls are refused with `ROE_CONFIG_MISSING` when the workspace is
unset, the RoE is missing/unreadable/malformed, or `machine_enforcement` is not
an object. They do not fall back to audit-only execution.

`MachineEnforcement` has no `mode` field. A legacy `mode: audit`, `warn`, or
`enforce` key is ignored when parsing the block; it does not disable enforcement
or select a warning-only path. Remove reliance on these obsolete mode values
when upgrading. Operator authorization remains required; there is no separate Red approval marker.

An empty object is a configured block with default rules. An empty `in_scope`
list does not itself deny all targets: after default/explicit denials, unmatched
targets are allowed by the evaluator. Provision an explicit reviewed allowlist;
do not confuse syntactically present configuration with adequate scope.

## Supported fields

| Field | Default | Runtime use |
| --- | --- | --- |
| `in_scope` | empty list | A nonempty list requires a matching target |
| `out_of_scope` | empty list | Denials take precedence over allows |
| `forbidden_destinations` | empty list | Additional denied hosts/IPs/CIDRs |
| `forbidden_command_patterns` | empty list | Regex refusals on literal command text |
| `allow_cloud_metadata` | false | Allows omission of built-in IMDS denials |
| `allow_sensitive_tlds` | false | Controls default `.gov`, `.mil`, `.edu`, `.int` denial |
| `max_concurrent_connections` | null | Bounds concurrent gated tool invocations using middleware semaphores |
| `min_inter_request_delay_ms` | 0 | Spaces tool invocation cadence, with optional extra jitter |
| `authorized_windows` | empty list | When nonempty, calls need a matching time interval |
| `blackout_windows` | empty list | Calls within a blackout interval are refused |

Concurrency and cadence limits apply to middleware calls, not to every socket or
HTTP request created inside a shell command. They are no longer mere advisory
schema fields. Window entries accept `{ "start": "<ISO datetime>", "end":
"<ISO datetime>" }` or a two-element pair; intervals include the start and exclude
the end. Invalid or nonpositive intervals are skipped by the parser, so review
materialized rules rather than assuming every authored interval was accepted.

Scope lists accept strings or `{ "target": "...", "type": "..." }` objects.
The auto kind infers networks, domain globs or literal hosts. `*` matches one
DNS label, not the apex or arbitrary deeper descendants; `?` matches one
character. Host matching is case/trailing-dot tolerant. Target normalization is
host-scoped: a URL path or port is not an independent network permission.

## Decisions and precedence

For a gated call the middleware checks the `.abort` marker, configured rules,
and testing windows, then evaluates the tool's target arguments or literal shell
command. Target denials include forbidden destinations, sensitive TLDs,
`out_of_scope`, then a nonempty `in_scope` allowlist. Command regex refusals
precede parsed shell targets. Invalid command regexes are skipped.

Built-in IMDS denials are `169.254.169.254`, `fd00:ec2::254`,
`metadata.google.internal`, `metadata.azure.com`, and `100.100.100.200`.
`allow_cloud_metadata: true` removes these defaults, not explicit denials.

The current `GATED_TOOL_NAMES` set is:

```text
bash, bash_output, bash_kill, http_request, proxy_send_request,
browser_action, web_fetch, web_search, detect_tech_stack
```

`web_search` is target-exempt OSINT but still passes configuration/time/cadence
checks. Other network tools use their registered URL/argument extractors.
`bash_status` is outside this set. A custom `gated_tools` set changes coverage;
a tool outside the active set passes through the guardrail.

Refusals return an error ToolMessage with `[ROE_REFUSED]`; the handler is not
called. Decisions and throttle/abort events are written through the configured
[audit sink](audit-ledger.md). Sink errors are logged and do not stop execution.

## Example machine block

This is the machine block within a complete, operator-provisioned RoE, not a
complete planning-document schema:

```json
{
  "machine_enforcement": {
    "in_scope": ["*.example.test", "192.0.2.0/24"],
    "out_of_scope": ["billing.example.test"],
    "forbidden_command_patterns": ["\\bhydra\\s.*-t\\s*[5-9]\\d+"],
    "max_concurrent_connections": 2,
    "min_inter_request_delay_ms": 250
  }
}
```

`app.example.test` matches the allowlist; the apex and deeper names do not.
`billing.example.test` is refused even though it matches the wildcard.
A command targeting another address is refused when parsed by the guardrail.
The real target authorization comes from the operator, not these example values.

## Network enforcement and limits

OpenShell is the only network-boundary authority. The trusted admission
resolver compiles the same machine block into an OpenShell `SandboxPolicy`
(`decepticon.middleware.policy_compiler`) and attaches it at sandbox
creation — the sandbox is born confined. Provisioning on each agent turn
*verifies* the admitted policy hash against the freshly compiled RoE hash
and records an `egress_provisioned` audit event; a mismatch, an
uncompilable RoE, or a backend without OpenShell authority fails loudly
and retries (there is no nftables fallback to quietly run on). The retired
`DECEPTICON_EGRESS_DISABLE=1` flag only logs a warning. A successful
model/tool call therefore still does not prove the sandbox started with
the policy: exercise the kernel boundary and inspect the admission hash
before claiming confinement.

Shell target extraction is best-effort and can miss indirection, encodings or
traffic initiated by an invoked program. Parser refusal prevents that handler
from running; it cannot guarantee that every out-of-scope packet is blocked.
Network policy, topology, daemon privileges and any trusted native transport
must be assessed separately. An operator's HITL allow decision does not disable
the RoE guardrail.
