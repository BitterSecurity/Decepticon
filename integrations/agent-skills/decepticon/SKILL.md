---
name: decepticon
description: "Drive Decepticon — an autonomous multi-agent red-team framework — over MCP to run authorized penetration tests and bug-bounty engagements end to end, then watch and steer them live from chat. Launch an engagement against a target, poll its transcript to narrate progress, send messages to refocus it, and pull findings as SARIF. Use when the user asks to run a pentest/red-team engagement, hunt a bug bounty, do recon, exploit/scan a host, web app, API, network, cloud, Active Directory, mobile app, or smart contract WITH Decepticon — or to check/resume a running engagement or report what Decepticon found. Triggers: run a decepticon engagement, pentest this with decepticon, bug bounty, recon this target, red team this, scan this host, resume the engagement, what did decepticon find, decepticon status. Do NOT use for ad-hoc local tool runs (running nmap/sqlmap/ffuf directly) when no Decepticon server is involved — this drives the Decepticon orchestrator, not raw tools."
license: Apache-2.0
metadata:
  version: 2.0.0
  homepage: "https://github.com/PurpleAILAB/Decepticon"
  hermes:
    tags: [decepticon, red-teaming, penetration-testing, bug-bounty, mcp, autonomous-agents, recon, exploitation, sarif]
    related_skills: [pentest-recon, offensive-reporting, reconnaissance]
---

# Decepticon engagements (over MCP)

Drive **Decepticon** — an autonomous multi-agent red-team framework — as if its
CLI were in this chat. Run an authorized engagement end to end (recon →
exploitation → post-exploitation → reporting) across web, API, network, Active
Directory, cloud, mobile, smart-contract, and binary targets, then **watch it
progress and steer it as it runs**.

You interact through the `decepticon_*` MCP tools (listed below). The heavy
work runs inside the Decepticon server; you are the operator at the console.

## Mental model — read this first

- An **engagement is a thread.** `decepticon_start_engagement` returns a
  `thread_id`. That is the handle for *every* other tool — there are no run
  ids to track.
- The **orchestrator** (`decepticon` graph) builds an OPPLAN and delegates to
  specialist sub-agents (recon, exploit, postexploit, analyst, reverser,
  cloud_hunter, ad_operator, mobile_operator, …) via a `task()` tool. You watch
  that narrative and nudge it.
- Engagements are **long and asynchronous** (minutes to hours). `start` returns
  immediately; you **poll** and narrate. **Never block** waiting for completion.

## Authorization — non-negotiable

- Only start engagements against assets the user has **explicitly confirmed are
  in scope**. If scope is unclear or missing, **ask before starting** — do not
  guess a target.
- ALWAYS pass scope + rules of engagement in `instruction`: name the in-scope
  hosts/domains/paths **and the explicit out-of-scope** items. The orchestrator
  enforces RoE on every tool call, but you are responsible for giving it
  correct scope.
- Decline targets that are plainly not the user's to test.

## Prerequisites (verify on first failure)

- A Decepticon **LangGraph server** must be running. For an installed CLI,
  `decepticon mcp serve` connects through the running LangGraph container. For
  a Python package or source checkout, the bridge uses `DECEPTICON_API_URL`
  (default `http://localhost:2024`). If a tool reports a connection failure,
  tell the user to run `decepticon start` or `langgraph dev` as appropriate.
- The `decepticon` MCP server must be registered. See the
  [integration docs](../../../docs/integrations/external-agents.md).

## Tools

| Tool | Use it to | Key args | Returns (key fields) |
|------|-----------|----------|----------------------|
| `decepticon_list_graphs` | see available graphs | — | `[{graph_id, name}]` |
| `decepticon_list_engagements` | browse / resume | `limit` | `[{thread_id, engagement_name, status}]` |
| `decepticon_start_engagement` | launch | `targets[]`, `instruction`, `scan_mode`, `engagement_name?` | `{thread_id, engagement_name, run_id, status}` |
| `decepticon_transcript` | watch the narrative | `thread_id`, `after_index`, `limit` | `{messages[], next_index, total, run_status}` |
| `decepticon_watch` | live sub-agent burst | `thread_id`, `max_seconds`, `max_events` | `{events[], run_status}` |
| `decepticon_send_message` | steer / answer / `/model` | `thread_id`, `message` | `{run_id, status}` |
| `decepticon_engagement_state` | OPPLAN / scope / phase | `thread_id` | `{engagement_name, message_count, values}` |
| `decepticon_engagement_status` | run status + findings ready | `thread_id`, `engagement_name?` | `{status, findings_available}` |
| `decepticon_engagement_findings` | pull results | `engagement_name`, `include_sarif?` | `{available, result_count, level_counts, sarif?}` |
| `decepticon_cancel_engagement` | stop the run | `thread_id` | text |

Full parameters, defaults, clamps, and return schemas are in
[`reference.md`](reference.md). Worked end-to-end runs are in
[`examples.md`](examples.md).

## The core loop

1. **Pick a graph.** Usually `decepticon` (full kill chain). Use `recon` for
   recon-only, `soundwave` for planning. `decepticon_list_graphs()` if unsure.
2. **Start.** `decepticon_start_engagement(targets=[…], instruction="In scope: …;
   Out of scope: …", scan_mode="standard")`. Save `thread_id` **and**
   `engagement_name`.
3. **Watch + narrate.** Loop `decepticon_transcript(thread_id,
   after_index=<previous next_index>)`; summarise only the NEW messages for the
   user (coordinator decisions, `task(<specialist>)` delegations, results). For
   a live burst use `decepticon_watch(thread_id)`. Check
   `decepticon_engagement_status(thread_id, engagement_name)`; stop polling when
   `status` is terminal **or** `findings_available` is true.
4. **Steer** when useful: `decepticon_send_message(thread_id, "skip the staging
   host, focus on the API")`, answer the coordinator, or switch models with
   `/model anthropic/claude-opus-4-8`.
5. **Report.** When findings exist:
   `decepticon_engagement_findings(engagement_name, include_sarif=true)` →
   present severity, counts, and reproduction. `decepticon_engagement_state` for
   the OPPLAN/phase.
6. **Resume later.** `decepticon_list_engagements()` → reuse any `thread_id`.

## Polling cadence (phone / chat friendly)

- Don't spam tools. While running, poll the transcript every ~15–30s and give
  the user a **1–2 line update per poll**, not raw dumps.
- Use the returned `next_index` as your cursor so each update covers only new
  activity.
- `decepticon_watch` blocks up to `max_seconds` (≤45) — use it for a quick live
  glimpse, not as your main loop.

## Interpreting results (fast guide)

- **transcript.messages**: `role` is user/assistant/tool. `tool_calls` like
  `task(recon)` means a specialist was dispatched. `tool` messages carry results.
- **status**: `pending`/`running` = working; `success` = finished;
  `error`/`timeout`/`interrupted` = stopped (say why; offer resume/restart);
  `none` = no run yet.
- **findings**: `available=false` → not persisted yet, keep polling.
  `level_counts` maps SARIF level → count (`error` = critical/high, `warning` =
  medium, `note` = low). Pass `include_sarif=true` to mine reproduction details.
- **engagement_state.values**: OPPLAN, objectives, scope, phase, working files.

## Errors & recovery

- **Connection failure** → the Decepticon server isn't up at `DECEPTICON_API_URL`.
  Ask the user to start it; don't loop.
- **`findings_available=false` for a while** → normal early on; keep watching the
  transcript and report progress.
- **`status=error`/`timeout`** → read the last transcript messages for the cause,
  summarise it, and offer to `send_message` a fix or start fresh.
- **No active run on `watch`/`cancel`** → the engagement is idle/finished; use
  `transcript`/`findings` instead.

See [`reference.md`](reference.md) and [`examples.md`](examples.md) for depth.
