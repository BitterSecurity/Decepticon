# Local Blue Cell web sensor

Blue Cell observes a local web service while Decepticon Red can continue attacking through its own workflow. The sensor runs as a local reverse proxy, Fluent Bit collector, SQLite receiver, and continuously running incident monitor. Blue's defensive agent reads the observed target events, investigates detections, and posts findings into the same interactive CLI chat. This stage reports findings; it does not block traffic or change the target.

## Use it from the interactive CLI

Start a web service on a local port, then enter:

```text
/blue up 127.0.0.1:3000
/blue status
/blue events
/blue incidents
```

Send host traffic you want Blue to observe to `http://127.0.0.1:18080`. Point Red inside its Docker sandbox at `http://host.docker.internal:18080`; the bundled sandbox maps that name to the Docker host. A service already exposed on its original port still accepts bypass traffic; the proxy cannot see those requests. For complete ingress visibility, make the original listener private and route all clients through the proxy. The optional `BLUE_TARGET_LOG_DIR` environment variable points Fluent Bit at a directory containing the target's logs. If you launch the service yourself, `capture_process.py` can capture its stdout and stderr without changing application code:

```sh
mkdir -p /tmp/decepticon-target-logs
python3 capture_process.py /tmp/decepticon-target-logs/process.jsonl python3 app.py
```

The CLI starts the sensor stack with Docker Compose. It expects the running Decepticon management network and LangGraph server, plus Linux host networking for the proxy to reach a service bound to `127.0.0.1`. The CLI discovers the Docker bridge gateway; the proxy binds only to that gateway and host loopback. Detection messages appear in the interactive chat as `[Blue Cell]` events while Red runs. The monitor queues incidents for the Blue Cell agent, then posts `[Blue Cell 조사]` findings to the same chat. `/blue analyze` requests a separate manual investigation. `/blue stop` stops the stack and retains Docker volumes.

The target can be local even if Red normally accepts broader URLs. Blue only connects to a local target in this OSS workflow. The command accepts `localhost`, `127.0.0.1`, or `[::1]` with an explicit port.

## Run the isolated fixture

The fixture requires no existing Compose file or SIEM:

```sh
cd examples/blue-local-sensor
docker compose --profile fixture -p decepticon-blue-sensor-poc up -d --build
python3 smoke_test.py
```

The test service listens on a Unix socket shared only with the proxy, with no direct HTTP port. Use `http://127.0.0.1:18080` for requests. The local APIs are:

```sh
curl 'http://127.0.0.1:18081/events?after=0&limit=100'
curl http://127.0.0.1:18081/metrics
curl 'http://127.0.0.1:18085/incidents?limit=20'
curl 'http://127.0.0.1:18085/notifications?after=0&limit=100'
curl http://127.0.0.1:18083/api/v1/metrics/prometheus
```

The fixture monitor has no agent connection by default. Set `BLUE_AGENT_URL` to the reachable LangGraph API base URL to enable automatic analysis, for example `http://host.docker.internal:2027` for a host development server. The runtime stack launched by `/blue up` connects to `http://langgraph:2024` on the Decepticon network.

## What is stored

Every proxied request produces access metadata with a request ID, method, URI, headers, response status, body byte count, SHA-256 digest, capture status, and an opaque `request_body_ref`. The Caddy module streams the bytes actually read by the upstream into a spool file; the receiver serves the complete original bytes at `/bodies/<ref>`. The body is not parsed or masked. A successful request of more than 8 MiB is captured without a per-request body limit. If the upstream does not read the body, the status says `unread` or `incomplete`; Blue does not invent unseen bytes.

The body spool defaults to a 2 GiB cleanup target and 24 hour retention. Cleanup runs every minute; a very large or simultaneous in-flight upload can temporarily exceed that target, so provision the Docker volume accordingly. The event database defaults to 100,000 events, 10,000 rejected-input records, and seven day retention. The monitor stores up to 10,000 incidents and 20,000 notifications for seven days. Older records are evicted periodically, and the receiver reports eviction counters. The proxy access log rolls at 100 MiB with ten retained files; Fluent Bit persists pending chunks and exposes retry, error, and drop metrics. These are bounded local retention settings, not archival storage. Raw bodies can include passwords and tokens, so use a local machine and volume access appropriate to the target data.

The monitor scans URI, request headers, streamed request bodies, and target log lines for general web attack signals. It opens an incident with source event and request IDs, then the Blue agent verifies against live event and body tools and reports its assessment. Rule matches are leads, not proof of compromise. The monitor persists its read cursor and notification feed in SQLite so restarts do not replay already processed events; the CLI keeps its own last displayed notification cursor in `DECEPTICON_HOME`.

## Coverage boundaries

The proxy sees HTTP requests that pass through it. Fluent Bit sees files in the configured top level log directory, including stdout and stderr captured by the wrapper. It cannot infer application actions that produce no accessible signal, see TLS before decryption outside its ingress, or see traffic sent directly to another listener. Request body files reflect only bytes the target read. This stack does not collect kernel, database, or application audit events by default and does not claim complete SIEM coverage. Add target specific log sources through `BLUE_TARGET_LOG_DIR` as needed.

To stop the isolated fixture without deleting its stored data:

```sh
docker compose --profile fixture -p decepticon-blue-sensor-poc down
```
