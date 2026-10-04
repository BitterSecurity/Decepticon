# Local Blue Cell web sensor

Blue Cell observes a local web service while Decepticon Red can continue attacking through its own workflow. The sensor runs as a local reverse proxy, Fluent Bit collector, SQLite receiver, and continuously running incident monitor. The resident Blue agent reviews all target events in ordered windows, including events that match no rule. Rule detections are still immediate. Evidence-backed AI alerts and incident investigations appear in the same interactive CLI chat. This stage reports findings; it does not block traffic or change the target.

## Use it from the interactive CLI

Start a web service on a local port, then enter:

```text
/blue up 127.0.0.1:3000 --logs /absolute/host/log-directory
/blue status
/blue verify
/blue sources
/blue events
/blue incidents
```

Send host traffic you want Blue to observe to `http://127.0.0.1:18080`. Point Red inside its Docker sandbox at `http://host.docker.internal:18080`; the bundled sandbox maps that name to the Docker host. A service already exposed on its original port still accepts bypass traffic; the proxy cannot see those requests. For complete ingress visibility, make the original listener private and route all clients through the proxy. `--logs` binds an existing directory on the Docker host into the collector read-only, without requiring a Compose file for the target service. A missing directory makes startup fail instead of silently creating an empty source. Omit `--logs` for proxy-only mode. If you launch the service yourself, `capture_process.py` can capture its stdout and stderr without changing application code:

```sh
mkdir -p /tmp/decepticon-target-logs
python3 capture_process.py /tmp/decepticon-target-logs/process.jsonl python3 app.py
```

For this wrapper example, use `/blue up 127.0.0.1:3000 --logs /tmp/decepticon-target-logs` from the CLI after the service starts. The wrapper writes `process_lifecycle` start and exit records with a capture run ID, reported PID, and exit code, as well as `process_log` records for stdout and stderr. Send a request through the Blue proxy that makes the application emit a log, then run `/blue verify` and `/blue sources`. Verification distinguishes a mounted directory, a file opened by Fluent Bit, and a record delivered to the receiver during the current collector run. A replayed old line can still satisfy delivery; confirm fresh target activity by checking the actual event and its timestamp.

The CLI starts the sensor stack with Docker Compose. It expects the running Decepticon management network and LangGraph server, plus Linux host networking for the proxy to reach a service bound to `127.0.0.1`. The CLI discovers the Docker bridge gateway; the proxy binds only to that gateway and host loopback. Detection messages appear in the interactive chat as `[Blue Cell]` events while Red runs. The monitor queues incidents for the Blue Cell agent, posts `[Blue Cell 조사]` findings, and independently asks the agent to review every new event window. It also sends a distinct collection-gap message if the collector stays unavailable, restarts, or reports skipped/dropped records. The monitor and agent keep working when the CLI is closed; retained notifications appear when the CLI is reopened. `/blue status` shows the mounted log source, per-input file and record counts, last target-log and proxy arrivals, collector availability, known drops, AI watch backlog, and failed windows. `/blue sources` lists the actual files that delivered retained records. The Blue agent can search exact request IDs, trace IDs, and trusted sources, or inspect a one-hour receiver-time window across sources. `/blue analyze` requests a separate manual investigation. `/blue stop` stops the stack and retains Docker volumes.

The target can be local even if Red normally accepts broader URLs. Blue only connects to a local target in this OSS workflow. The command accepts `localhost`, `127.0.0.1`, or `[::1]` with an explicit port.

## Run the isolated fixture

The fixture requires no existing Compose file or SIEM:

```sh
cd examples/blue-local-sensor
docker compose --profile fixture -p decepticon-blue-sensor-poc up -d --build
python3 smoke_test.py
python3 watch_smoke_test.py
```

The test service listens on a Unix socket shared only with the proxy, with no direct HTTP port. Use `http://127.0.0.1:18080` for requests. The local APIs are:

`watch_smoke_test.py` uses a local fake agent API to check event review, alert delivery, and thread recovery after a monitor restart. It stops the fixture containers when finished. It does not assess model detection quality.

```sh
curl 'http://127.0.0.1:18081/events?after=0&limit=100'
curl 'http://127.0.0.1:18081/sources?limit=20'
curl http://127.0.0.1:18081/metrics
curl 'http://127.0.0.1:18085/incidents?limit=20'
curl 'http://127.0.0.1:18085/notifications?after=0&limit=100'
curl http://127.0.0.1:18083/api/v2/metrics/prometheus
```

The fixture monitor has no agent connection by default. Set `BLUE_AGENT_URL` to the reachable LangGraph API base URL to enable automatic analysis and resident AI watch, for example `http://host.docker.internal:2027` for a host development server. The runtime stack launched by `/blue up` connects to `http://langgraph:2024` on the Decepticon network. When no new events arrive, the resident process waits without calling the model. With new events, it batches up to 25 events and reviews them after at most 15 seconds by default. `BLUE_WATCH_BATCH_SIZE` (1–100) and `BLUE_WATCH_INTERVAL_SECONDS` adjust the model call rate. If model analysis is slower than sustained ingestion, cleanup trims the queue to `BLUE_WATCH_MAX_ROWS` pending events and sends explicit gap notifications for older unreviewed events. `/blue metrics` reports backlog, retrying windows, and failed review windows.

## What is stored

Every proxied request produces access metadata with a request ID, method, URI, headers, response status, body byte count, SHA-256 digest, capture status, and an opaque `request_body_ref`. The Caddy module streams the bytes actually read by the upstream into a spool file; the receiver serves the complete original bytes at `/bodies/<ref>`. The body is not parsed or masked. A successful request of more than 8 MiB is captured without a per-request body limit. If the upstream does not read the body, the status says `unread` or `incomplete`; Blue does not invent unseen bytes.

The body spool defaults to a 2 GiB cleanup target and 24 hour retention. Cleanup runs every minute; a very large or simultaneous in-flight upload can temporarily exceed that target, so provision the Docker volume accordingly. The event database defaults to 100,000 events, 10,000 rejected-input records, and seven day retention. The monitor stores up to 10,000 incidents and 20,000 notifications for seven days. Older records are evicted periodically, and the receiver reports eviction counters. The proxy access log rolls at 100 MiB with ten retained files; Fluent Bit persists pending chunks and exposes retry, error, and drop metrics. These are bounded local retention settings, not archival storage. Raw bodies can include passwords and tokens, so use a local machine and volume access appropriate to the target data.

The monitor scans URI, request headers, streamed request bodies, and target log lines for general web attack signals. It opens an incident with source event and request IDs, then the Blue agent verifies against live event and body tools and reports its assessment. Separately, the resident AI watch reviews every new event window and alerts only when it cites event sequences from that window. Rule matches and AI alerts are leads, not proof of compromise. The monitor persists its ingestion and AI review cursors, decisions, target-specific agent thread IDs, and notification feed in SQLite so restarts continue from the last completed window. Temporary agent outages keep the pending window queued for retry. After three invalid AI decisions, the window is marked failed and an explicit gap notification is sent. The CLI keeps a notification cursor while it is open and replays retained notifications when reopened.

## Coverage boundaries

The proxy sees HTTP requests that pass through it. Fluent Bit sees files in the configured top level log directory, including stdout and stderr captured by the wrapper. It cannot infer application actions that produce no accessible signal, see TLS before decryption outside its ingress, or see traffic sent directly to another listener. Request body files reflect only bytes the target read. This stack does not collect kernel, database, or application audit events by default and does not claim complete SIEM coverage. Add target specific log sources through `BLUE_TARGET_LOG_DIR` as needed.

JSON application logs are stored with the original line and parsed attributes. Common timestamp, severity, trace, span, and request IDs are extracted for Blue investigations. `/blue metrics` and the Blue agent's sensor scan include collector availability, per-input file and record counts, and Fluent Bit counters for skipped long lines, dropped records, failed retries, and paused inputs. Receiver accepted, rejected, and evicted counters persist in SQLite across restarts; `counters_started_at` marks the initial counter migration point. An increasing loss counter creates a chat notification even when no attack rule matches. A zero counter does not prove that the target emitted every useful event. The [telemetry design](../../docs/features/blue-cell-telemetry-design.md) records the source coverage and production extension plan.

To stop the isolated fixture without deleting its stored data:

```sh
docker compose --profile fixture -p decepticon-blue-sensor-poc down
```
