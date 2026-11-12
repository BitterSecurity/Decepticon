# RoE machine enforcement

The operator/client owns reviewed `plan/roe.json`. Soundwave can prepare a
separate candidate; it cannot rewrite that protected scope. See
[provisioning](../roe-provisioning.md) and the
[extension contract](../design/extension-contract.md).

The `machine_enforcement` block is enforced by exactly two components:

| Control | Enforced by |
| --- | --- |
| Network scope (`in_scope`, `out_of_scope`, `forbidden_destinations`, `allow_cloud_metadata`, `allow_sensitive_tlds`) | The engagement sandbox's OpenShell policy, compiled by [`policy_compiler`](../../packages/decepticon/decepticon/middleware/policy_compiler.py) |
| Execution time (`authorized_windows`, `blackout_windows`) | [`TimeWindowMiddleware`](../../packages/decepticon/decepticon/middleware/time_window.py) |

There is no application-layer target or command check. The former RoE guardrail
middleware (target extraction, `forbidden_command_patterns`, request pacing,
concurrency limits, the `.abort` marker and the `audit/roe-decisions.jsonl`
decision ledger) was removed; `decepticon.middleware.roe` remains importable for
one release as an inert, deprecated shim.

## Supported fields

| Field | Default | Runtime use |
| --- | --- | --- |
| `in_scope` | empty list | Compiled into allowed OpenShell endpoints; an empty list is a compile error |
| `out_of_scope` | empty list | Must not intersect `in_scope` (compile error) |
| `forbidden_destinations` | empty list | Must not be covered by `in_scope` (compile error) |
| `allow_cloud_metadata` | false | Controls the built-in IMDS denials checked at compile time |
| `allow_sensitive_tlds` | false | Controls the `.gov`, `.mil`, `.edu`, `.int` compile-time check |
| `authorized_windows` | empty list | When nonempty, runs execute only inside a listed interval |
| `blackout_windows` | empty list | Runs never execute inside a listed interval |
| `forbidden_command_patterns`, `max_concurrent_connections`, `min_inter_request_delay_ms` | unset | **Not enforced.** Compiling an RoE that sets them logs a warning and returns it in `CompiledRoePolicy.warnings`; remove them |

`MachineEnforcement` has no `mode` field; a legacy `mode` key is ignored.

Scope lists accept strings or `{ "target": "...", "type": "..." }` objects. `*`
matches one DNS label, not the apex or deeper descendants. Endpoint ports are
pinned by `DECEPTICON_OPENSHELL_ROE_PORTS`; binaries by
`DECEPTICON_OPENSHELL_ROE_BINARIES` (default: any binary).

Window entries accept `{ "start": "<ISO datetime>", "end": "<ISO datetime>" }`
or a two-element pair; intervals include the start and exclude the end.
Invalid or nonpositive intervals are skipped by the parser.

## Network scope

OpenShell is the only network-boundary authority. The trusted admission
resolver compiles the machine block into an OpenShell `SandboxPolicy` and
attaches it at sandbox creation, so the sandbox is born confined. The policy
applies to every connection any process in the sandbox opens, including
redirects, subresources and traffic started by tools the agent runs. Admission
verifies that the effective policy matches the compiled RoE and is rechecked
before every execution; a changed RoE revokes the binding.

## Execution time windows

`TimeWindowMiddleware` runs right after the engagement context in the
orchestrator and every target-acting specialist (the `time-window` slot). It
checks the current time:

- at the very start of a run (`before_agent`): outside the window the run ends
  before any model or tool call;
- before every model call (`before_model`): when the window closes during a
  run, the run ends before the next model turn. A tool call that is already
  executing finishes first.

A stopped run ends with an `AIMessage` whose `additional_kwargs` carry
`decepticon_time_window_stop` (`phase`, `code`, `detail`). Codes are
`OUTSIDE_TESTING_WINDOW` and `BLACKOUT_WINDOW`. Planning, reporting and
source-analysis roles (soundwave, finding_reporter, detector, vulnresearch,
blue_cell) do not carry the slot, so an engagement can be planned and reported
outside its testing window.

## Example machine block

```json
{
  "machine_enforcement": {
    "in_scope": ["*.example.test", "192.0.2.0/24"],
    "out_of_scope": ["billing.example.test"],
    "authorized_windows": [
      {"start": "2026-11-12T00:00:00Z", "end": "2026-11-12T09:00:00Z"}
    ]
  }
}
```

The real target authorization comes from the operator, not these example values.
