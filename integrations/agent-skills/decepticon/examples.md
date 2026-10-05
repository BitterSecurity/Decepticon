# Decepticon operator examples

These examples separate host commands, Decepticon's interactive slash commands,
and MCP tool calls. Confirm target scope before active testing.

## Inspect a running CLI engagement from Claude Code or Codex

The operator started `decepticon start`, selected an engagement, and began a
run in the interactive CLI. From the coding agent:

```text
decepticon_list_engagements(limit=20)
  -> [{thread_id: "th_1", engagement_name: "example-assessment", ...}]
decepticon_engagement_state(thread_id="th_1")
decepticon_transcript(thread_id="th_1", after_index=0, limit=40)
  -> {next_index: 8, messages: [...], run_status: "running"}
decepticon_transcript(thread_id="th_1", after_index=8, limit=40)
```

Report only new, observed activity. A `decepticon_watch` call can sample a
short live window; it is not a durable event subscription.

## Steer after the current run

```text
decepticon_send_message(
  thread_id="th_1",
  message="Within the approved scope, focus on /api/v2 before other endpoints."
)
```

This queues a later turn. Check the subsequent transcript before claiming that
the agent changed its current activity. `decepticon_cancel_engagement` stops an
active run when the operator requests that.

## Start a local Blue Cell

Inside the Decepticon interactive CLI, the operator can enter:

```text
/blue up 127.0.0.1:3000
/blue verify
/blue events 20
/blue incidents 20
/blue analyze
```

Tell the operator to send test traffic to `http://127.0.0.1:18080`. The slash
commands run in the interactive CLI. Once the sensor is running, the coding
agent can inspect it through MCP:

```text
decepticon_blue_status()
decepticon_blue_events(after=0, limit=20)
  -> {events: [...], next_after: 23, has_more: false}
decepticon_blue_incidents(limit=20)
decepticon_blue_notifications(after=0, limit=20)
  -> {notifications: [...], next_after: 4, has_more: false}
```

Use the returned cursors for later polls. MCP reads did not start the sensor.

## Read results without hiding a gap

`decepticon_engagement_findings(engagement_name="example-assessment")` reads
`graph.json` only. If it returns `available=false`, inspect the selected host
workspace's `findings/FIND-*.md` and `report/` files. The installed MCP bridge
cannot currently read those files from its container. Report the actual
finding IDs, evidence, verification status, and affected targets from files
you opened; do not turn `available=false` into a no-findings claim.

## A separately mapped LangGraph server

For a deployment that explicitly maps the MCP engagement name to its own
workspace, the agent can call `decepticon_start_engagement` with the user's
approved targets and a full in-scope and out-of-scope instruction. Save its
`thread_id`, then use the transcript cursor, status, and cancel tools as above.
Confirm that mapping before using this flow on an installed Docker stack.
