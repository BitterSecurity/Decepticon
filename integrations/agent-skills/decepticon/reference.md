# Decepticon MCP — tool reference

Exact parameters, defaults, clamps, and return schemas for every `decepticon_*`
tool. Read this when you need precise field names or edge-case behaviour. All
tools are async. Engagement tools use LangGraph; Blue observation tools use
the local receiver and monitor HTTP APIs.

## Conventions

- **`thread_id`** is the engagement handle (from `start_engagement` /
  `list_engagements`). The active run for a thread is resolved automatically —
  you never pass a `run_id`.
- **`engagement_name`** is the thread's human slug. The installed stack binds
  the CLI-selected workspace at `/workspace`; an MCP-created name does not
  change that bind mount or create a matching host directory.
- Tools return structured objects (shown below). Counts/cursors are integers.

---

## decepticon_list_graphs()

List the engagement graphs (assistants) the connected server exposes.

- **Args:** none.
- **Returns:** `[{ assistant_id, graph_id, name }]`.
- Common `graph_id`s: `decepticon` (full kill-chain orchestrator), `recon`
  (recon only), `soundwave` (engagement planning). Others may exist per install.

## decepticon_list_engagements(limit=20)

Recent engagements, most-recently-updated first — for browse / resume.

- **Args:** `limit` (int, clamped 1–100).
- **Returns:** `[{ thread_id, engagement_name, status, created_at, updated_at }]`.

## decepticon_start_engagement(targets, instruction="", scan_mode="standard", engagement_name=None, assistant=None)

Launch a **background** engagement. Returns immediately. On the installed
Docker stack, this tool does not select or mount a new engagement workspace;
use the CLI-selected engagement unless workspace mapping has been verified.

- **Args:**
  - `targets` (list[str], required) — URLs, hostnames/CIDRs, repo URLs, or paths.
  - `instruction` (str) — **scope + rules of engagement**; in-scope AND
    out-of-scope. Always supply this.
  - `scan_mode` (`"quick"` | `"standard"` | `"deep"`) — depth/timeout profile.
  - `engagement_name` (str, optional) — defaults to `mcp-<UTC timestamp>`.
  - `assistant` (str, optional) — graph id; defaults to the server's default
    (`decepticon`).
- **Returns:** `{ engagement_name, thread_id, run_id, assistant, status, langgraph_url }`.

## decepticon_transcript(thread_id, after_index=0, limit=40)

The orchestrator narrative — poll this to watch progress.

- **Args:** `thread_id`; `after_index` (int — start at this message index);
  `limit` (int, clamped 1–200).
- **Returns:** `{ thread_id, run_status, total, next_index, messages: [{ index,
  role, text, tool_calls, name }] }`.
  - `role`: `user` | `assistant` | `tool` | `system`.
  - `tool_calls`: list of labels, e.g. `task(recon)` (a specialist dispatch),
    `create_objective`, `write_file`.
  - `name`: set on `tool` messages (the tool that produced the result).
  - `next_index`: pass as the next call's `after_index` to get only new messages.
  - `text` is truncated per message (long results are clipped with a `+N chars`
    marker).

## decepticon_watch(thread_id, max_seconds=20, max_events=40)

Bounded live tail of the run's stream (sub-agent activity), then returns.

- **Args:** `thread_id`; `max_seconds` (clamped 1–45); `max_events` (clamped 1–100).
- **Returns:** `{ thread_id, run_id, run_status, events: [{ event, data }],
  truncated }`.
  - `event`: `custom` (sub-agent activity), `updates` (node updates), `messages`.
  - `data`: compact JSON string (clipped at ~600 chars).
  - `truncated`: true if `max_events` was hit before the window/closed run.
  - Returns `events: []` when no run is active (idle/finished) — use `transcript`.

## decepticon_send_message(thread_id, message, assistant=None)

Send an operator message onto the engagement thread — steer, answer, or switch.

- **Args:** `thread_id`; `message` (str); `assistant` (optional — defaults to the
  thread's existing graph).
- **Behaviour:** enqueued after any active run (`multitask_strategy="enqueue"`),
  then dispatched in the background. Confirm its effect in a later transcript.
- **Special:** start `message` with `/model <provider/model-id>` to switch the
  orchestrator's model mid-engagement (e.g. `/model anthropic/claude-opus-4-8`).
- **Returns:** `{ thread_id, run_id, assistant, status }`.

## decepticon_engagement_state(thread_id)

Engagement context minus the message log.

- **Args:** `thread_id`.
- **Returns:** `{ thread_id, engagement_name, run_status, message_count, values }`.
  - `values`: orchestrator working state — OPPLAN/objectives, `scan_scope`,
    phase, working files. Large values are replaced with an `<N chars omitted>`
    placeholder to keep the payload small.

## decepticon_engagement_status(thread_id, engagement_name="")

Latest run status + whether findings have been persisted.

- **Args:** `thread_id`; `engagement_name` (optional — needed to check findings).
- **Returns:** `{ thread_id, run_id, status, findings_available }`.
  - `status`: `pending`/`running`/`success`/`error`/`timeout`/`interrupted`/`none`.
  - `findings_available`: true once this bridge can see `graph.json` for the
    engagement. False does not mean `FIND-*.md` or reports are absent.

## decepticon_engagement_findings(engagement_name, include_sarif=False)

Findings summary, optionally with the full SARIF v2.1.0 document.

- **Args:** `engagement_name`; `include_sarif` (bool — full SARIF when true).
- **Returns:** `{ engagement_name, available, result_count, level_counts, sarif }`.
  - `available`: false unless this bridge can read `graph.json`. The installed
    LangGraph container does not mount the CLI-selected workspace, so check
    canonical host artifacts separately.
  - `level_counts`: SARIF level → count. `error` ≈ critical/high, `warning` ≈
    medium, `note` ≈ low.
  - `sarif`: full SARIF doc when `include_sarif=true`, else null. Mine
    `runs[0].results[]` for `ruleId`, `message`, and `locations` (reproduction).

## decepticon_cancel_engagement(thread_id)

Cancel the active run on a thread.

- **Args:** `thread_id`.
- **Returns:** text (`cancelled <run_id>` or `no active run to cancel`).

## Blue Cell observation tools

These read the running local receiver and monitor. They do not start a sensor.
All reads are bounded and return the service's JSON fields without inventing
events when a service is unavailable.

- `decepticon_blue_status()` returns `{sensor, monitor}` metrics. Check
  `collector_available`, source delivery counters, `watch_enabled`, and backlog.
- `decepticon_blue_sources(limit=20)` returns `{sources, has_more}`.
- `decepticon_blue_events(after=0, limit=20)` returns
  `{events, next_after, has_more}`. Pass `next_after` back as `after`.
- `decepticon_blue_incidents(limit=20)` returns `{incidents}`.
- `decepticon_blue_notifications(after=0, limit=20)` returns
  `{notifications, next_after, has_more}`. Its cursor is separate from events.
- `decepticon_blue_body(ref, max_bytes=65536)` accepts a 32-character lowercase
  hex reference and returns `{ref, total_bytes, preview_bytes, preview_utf8,
  preview_limited}`. `max_bytes` is restricted to 1–262144.
- `decepticon_blue_search(field, value, limit=20, before=None)` returns an
  event page. `field` is `request_id`, `trace_id`, or `source`; `before` is a
  positive sequence for backward pagination.
- `decepticon_blue_timeline(start_at, end_at, source=None, limit=50,
  before=None)` reads a timezone-aware window of at most one hour. `source` is
  `blue-ingress-proxy` or `target-log-file`.

Limits are 1–100. Invalid arguments and receiver/monitor errors surface as MCP
tool errors, so they must not be interpreted as empty evidence.

---

## Notes & gotchas

- **Long engagements:** `start`/`send_message` never block. Poll `transcript` +
  `status`; do not call a blocking wait.
- **Cursoring:** always pass the previous `next_index` as `after_index` so you
  narrate only new activity and avoid repeating yourself.
- **Services required:** engagement tools need LangGraph. Blue observation
  tools need the receiver and monitor. The installed launcher runs the bridge
  inside the LangGraph container; Python installs use `DECEPTICON_API_URL` for
  engagement tools and `BLUE_SENSOR_URL` / `BLUE_MONITOR_URL` for Blue reads.
- **Canonical results:** inspect the selected host workspace's
  `findings/FIND-*.md` and `report/` files. An empty `graph.json` response is
  not evidence that the engagement found nothing.
