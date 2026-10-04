# Blue Cell in OSS

Blue Cell is an independent defensive agent for a user's local web service. It continuously consumes target observations from a local sensor, opens incidents for suspicious activity, investigates the evidence with read-only tools, and sends findings into the interactive CLI chat during a Red engagement. This stage does not automatically block or remediate.

The current path is:

```text
local web service → Caddy ingress and body spool → Fluent Bit → SQLite receiver
                                          target logs ────────┘
SQLite receiver → incident monitor → Blue Cell agent → CLI notification feed
Red Cell ──────────────────→ monitored proxy ───────→ local web service
```

The proxy observes only requests sent through its listener. Fluent Bit also tails the target log directory; the included process wrapper captures stdout and stderr for a service launched through it. The agent's `blue_sensor_scan`, `blue_sensor_events`, `blue_sensor_search`, `blue_sensor_timeline`, and `blue_sensor_body` tools read the receiver and monitor. Blue's default graph has no shell or containment tools. Its prompt treats HTTP and target log content as untrusted evidence.

The receiver now retains raw target log lines and extracts common fields from JSON logs, including severity, trace, span, request ID, and event time. Its `/metrics` endpoint also reports Fluent Bit skip, drop, retry failure, and paused-input counters when the collector is reachable. See the [telemetry and evidence design](blue-cell-telemetry-design.md) for the coverage contract and the path from local sensors to production sources.

Start a local service, then use `/blue up 127.0.0.1:3000 --logs /absolute/host/log-directory` in the interactive CLI when its logs are available. Omit `--logs` for proxy-only mode. Send Red in its Docker sandbox to `http://host.docker.internal:18080`. The CLI receives immediate `[Blue Cell]` detection messages, `[Blue Cell 조사]` agent assessments, and separate collection-gap notices in the same conversation. `/blue status` shows the enrolled log source and known collector loss; `/blue verify` distinguishes files opened from records delivered during the current collector run, and `/blue sources` lists files that produced retained events. `/blue incidents`, `/blue events`, `/blue metrics`, and `/blue analyze` expose incident details, telemetry, health, and manual investigation. The complete setup, APIs, retention settings, and coverage limits are in [the local sensor README](../../examples/blue-local-sensor/README.md).

Detection rules are currently generic web attack indicators and target log anomaly terms. A rule match creates an investigation lead. It cannot alone prove exploit success or full target coverage. The proxy cannot observe bypass traffic and the log collector cannot read events the application never exposes. Additional local log sources can be attached with `BLUE_TARGET_LOG_DIR`; broader telemetry integrations and response tools are later steps.

Legacy `decepticon.blue_cell` session and rule replay modules remain available as library code, but they are not the runtime evidence source for the Blue agent described here.
