# Local Blue web sensor

This Linux Docker example gives Blue an independent view of a local web service. Caddy owns the HTTP ingress, a small Caddy module records the request body bytes that the upstream actually reads, Fluent Bit collects proxy and target log files, and a local receiver persists events in SQLite and serves a live stream. No existing Compose file, SIEM, or Decepticon application stack is required.

## Run the isolated fixture

From this directory:

```sh
docker compose --profile fixture -p decepticon-blue-sensor-poc up -d --build
python3 smoke_test.py
```

The first run builds Caddy with the local body capture module. Send requests to `http://127.0.0.1:18080`. The fixture listens on a Unix socket shared only with the proxy; it has no direct HTTP port. The Blue query API is bound to `127.0.0.1:18081`.

```sh
curl -N http://127.0.0.1:18081/stream
curl 'http://127.0.0.1:18081/events?after=0&limit=100'
curl http://127.0.0.1:18081/metrics
curl http://127.0.0.1:18083/api/v1/metrics/prometheus
```

The event query returns `events`, `next_after`, and `has_more`. The stream starts with new events by default; pass `?after=<seq>` or an SSE `Last-Event-ID` header to replay from a cursor. The Fluent Bit metrics endpoint exposes retries, errors, dropped records, and storage state.

## Connect an existing local service

The proxy uses Linux host networking so it can reach a service bound to `127.0.0.1:3000`. It also accepts a reachable local HTTPS upstream.

```sh
mkdir -p /tmp/decepticon-target-logs
BLUE_TARGET_UPSTREAM=127.0.0.1:3000 \
BLUE_TARGET_LOG_DIR=/tmp/decepticon-target-logs \
docker compose -p decepticon-blue-sensor-poc up -d --build
```

Send traffic to `127.0.0.1:18080`. Place target log files directly inside `BLUE_TARGET_LOG_DIR`, or launch a process through the wrapper to capture its stdout and stderr without modifying application code:

```sh
python3 capture_process.py /tmp/decepticon-target-logs/process.jsonl python3 app.py
```

An already running service may still accept requests on its original address. Those requests bypass the proxy HTTP record, although any target log line they produce can still reach Blue. Complete ingress coverage requires moving the target behind a private listener or Unix socket and sending all clients through Blue.

## Request body records

For each proxied request, the event includes `request_body_status`, `request_body_bytes_read`, and `request_body_base64`. Decode the Base64 field to recover the exact observed bytes. There is no parsing or masking of JSON, form fields, or binary data. The original body can therefore contain passwords, tokens, or other private data in the local Caddy log and Blue SQLite database.

The default body capture limit is 65,536 bytes per request. Set `BLUE_BODY_MAX_BYTES` to a larger byte count, up to 8,388,608, before `docker compose up`. Bodies that exceed the configured limit retain the first configured number of bytes and are marked `truncated`; bodies the upstream does not fully read are marked `incomplete` or `unread`. The proxy streams the request to the target and keeps only the configured prefix in memory.

The proxy access log rolls at 100 MiB and keeps ten uncompressed rotated files. Fluent Bit watches active and rotated access logs and uses the proxy generated request ID to deduplicate replayed records. Target process logs contain producer generated event IDs. Raw external target log files have weaker deduplication because they may not supply stable event IDs.

## Observation and operating limits

The receiver's ingest port is available only on the private Blue network. The target runs on a separate network; the query API and collector metrics ports are published on host loopback. The receiver stores events in SQLite WAL mode, supports paginated reads, and records rejected input counts. Fluent Bit persists pending chunks, retries delivery, and caps its pending output storage at 1 GiB; reaching that cap can drop the oldest queued records, visible in its metrics.

The proxy observes HTTP requests that pass through it. The wrapper observes stdout and stderr lines emitted by a process launched through it. Fluent Bit reads files at the top level of the mounted target log directory. Application actions that emit no accessible signal cannot be inferred from proxy and process logs. This example has no host kernel sensor, target specific application audit adapter, detection rules, indexed SIEM search, automatic database retention, or defensive AI agent. It is a local web sensor foundation, not a claim of complete SIEM coverage.

The smoke test checks live proxy and process events, original request body bytes, an oversized body's `truncated` state, ten distinct requests on both channels, and the absence of the fixture's direct HTTP port.

To stop the example without deleting stored data:

```sh
docker compose --profile fixture -p decepticon-blue-sensor-poc down
```
