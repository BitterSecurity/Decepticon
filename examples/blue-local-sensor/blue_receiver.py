import hashlib
import itertools
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DB_PATH = Path(os.environ.get("BLUE_EVENT_DB", "/blue-data/observations.sqlite3"))
BODY_DIR = Path(os.environ.get("BLUE_BODY_DIR", "/body-spool"))
BODY_MAX_DISK_BYTES = int(os.environ.get("BLUE_BODY_MAX_DISK_BYTES", str(2 * 1024**3)))
BODY_TTL_SECONDS = int(os.environ.get("BLUE_BODY_TTL_SECONDS", "86400"))
EVENT_MAX_ROWS = int(os.environ.get("BLUE_EVENT_MAX_ROWS", "100000"))
EVENT_TTL_SECONDS = int(os.environ.get("BLUE_EVENT_TTL_SECONDS", str(7 * 86400)))
REJECTED_MAX_ROWS = int(os.environ.get("BLUE_REJECTED_MAX_ROWS", "10000"))
TARGET_ID = os.environ.get("BLUE_TARGET_ID", "local-web")
PROXY_LOG_PREFIX = os.environ.get("BLUE_PROXY_LOG_PREFIX", "/sensor-logs/access")
MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_PAGE_SIZE = 1000
SEARCH_FIELDS = {
    "request_id": "$.request_id",
    "trace_id": "$.trace_id",
    "source": "$.source",
}
COLLECTOR_METRICS_URL = os.environ.get(
    "BLUE_COLLECTOR_METRICS_URL", "http://blue-collector:2020/api/v2/metrics/prometheus"
)
COLLECTOR_COUNTERS = {
    "fluentbit_input_records_total": "collector_input_records_total",
    "fluentbit_output_proc_records_total": "collector_output_records_total",
    "fluentbit_input_long_line_skipped_total": "collector_long_lines_skipped_total",
    "fluentbit_output_dropped_records_total": "collector_dropped_records_total",
    "fluentbit_output_retries_failed_total": "collector_retries_failed_total",
    "fluentbit_routing_logs_drop_records_total": "collector_routing_dropped_records_total",
    "fluentbit_input_ingestion_paused": "collector_paused_inputs",
}
COLLECTOR_SOURCE_METRICS = {
    "fluentbit_input_records_total": "records_total",
    "fluentbit_input_files_opened_total": "files_opened_total",
    "fluentbit_input_long_line_skipped_total": "long_lines_skipped_total",
    "fluentbit_input_ingestion_paused": "paused",
}
COLLECTOR_SOURCE_ALIASES = {
    "blue_proxy": "proxy",
    "blue_target_logs": "target_logs",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timeline_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def log_timestamp(record: dict[str, object]) -> str | None:
    for key in ("@timestamp", "timestamp", "occurred_at", "time", "ts"):
        value = record.get(key)
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                magnitude = abs(value)
                if magnitude > 10**17:
                    seconds = value / 10**9
                elif magnitude > 10**14:
                    seconds = value / 10**6
                elif magnitude > 10**11:
                    seconds = value / 10**3
                else:
                    seconds = value
                return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
            if isinstance(value, str):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            continue
    return None


def log_field(record: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def log_details(record: dict[str, object], original: str) -> dict[str, object]:
    return {
        "raw_record": original,
        "attributes": record,
        "severity_text": log_field(record, "severity_text", "severity", "level", "lvl"),
        "trace_id": log_field(record, "trace_id", "traceId"),
        "span_id": log_field(record, "span_id", "spanId"),
        "request_id": log_field(record, "request_id", "requestId", "req_id"),
    }


def parse_collector_metrics(payload: str) -> dict[str, object]:
    counters = {name: 0 for name in COLLECTOR_COUNTERS.values()}
    sources = {
        source: {name: 0 for name in COLLECTOR_SOURCE_METRICS.values()}
        for source in COLLECTOR_SOURCE_ALIASES.values()
    }
    start_time = None
    for line in payload.splitlines():
        match = re.match(r"^([a-z_]+)(?:\{([^}]*)\})?\s+([0-9.eE+-]+)(?:\s|$)", line)
        if not match:
            continue
        metric, labels, raw_value = match.groups()
        try:
            value = int(float(raw_value))
        except (OverflowError, ValueError):
            continue
        if metric == "fluentbit_process_start_time_seconds":
            start_time = value
        if metric in COLLECTOR_COUNTERS:
            counters[COLLECTOR_COUNTERS[metric]] += value
        source_match = re.search(r'\bname="([^"]+)"', labels or "")
        source = COLLECTOR_SOURCE_ALIASES.get(source_match.group(1)) if source_match else None
        if source and metric in COLLECTOR_SOURCE_METRICS:
            sources[source][COLLECTOR_SOURCE_METRICS[metric]] += value
    return {**counters, "collector_start_time_seconds": start_time, "collector_sources": sources}


def collector_metrics() -> dict[str, object]:
    try:
        with urllib.request.urlopen(COLLECTOR_METRICS_URL, timeout=1) as response:
            payload = response.read(1024 * 1024).decode("utf-8")
        return {"collector_available": True, **parse_collector_metrics(payload)}
    except (OSError, ValueError, UnicodeError, urllib.error.URLError):
        return {"collector_available": False}


def normalize(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or not isinstance(raw.get("sensor_file"), str):
        raise ValueError("missing sensor provenance")
    event_id = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sensor_file = raw["sensor_file"]
    sensor_read_at = raw.get("sensor_read_at")
    provenance = {
        "schema_version": 1,
        "target_id": TARGET_ID,
        "sensor_read_at": sensor_read_at,
        "sensor_file": sensor_file,
        "sensor_offset": raw.get("sensor_offset"),
    }
    if sensor_file.startswith(PROXY_LOG_PREFIX):
        request = raw.get("request")
        if not isinstance(request, dict) or not isinstance(raw.get("path"), str):
            raise ValueError("invalid proxy event")
        request_id = raw.get("blue_request_id")
        if isinstance(request_id, str):
            event_id = hashlib.sha256((TARGET_ID + ":" + request_id).encode()).hexdigest()
        return {
            **provenance,
            "event_id": event_id,
            "event_type": "http_access",
            "signal_type": "http",
            "source": "blue-ingress-proxy",
            "request_id": request_id,
            "occurred_at": datetime.fromtimestamp(float(raw["ts"]), timezone.utc).isoformat(),
            "client_ip": request.get("client_ip") or request.get("remote_ip"),
            "host": request.get("host"),
            "protocol": request.get("proto"),
            "method": request.get("method"),
            "path": raw["path"],
            "uri": request.get("uri"),
            "request_headers": request.get("headers"),
            "response_headers": raw.get("resp_headers"),
            "status": raw.get("status"),
            "duration_seconds": raw.get("duration"),
            "request_bytes": raw.get("bytes_read"),
            "response_bytes": raw.get("size"),
            "request_body_status": raw.get("blue_body_status"),
            "request_body_bytes_read": raw.get("blue_body_bytes_read"),
            "request_body_bytes_stored": raw.get("blue_body_bytes_stored"),
            "request_body_ref": raw.get("blue_body_ref"),
            "request_body_sha256": raw.get("blue_body_sha256"),
            "request_body_base64": raw.get("blue_request_body_base64"),
        }
    line = raw.get("log")
    if not isinstance(line, str):
        raise ValueError("invalid target log line")
    try:
        parsed = json.loads(line)
    except ValueError:
        parsed = None
    if (
        isinstance(parsed, dict)
        and parsed.get("source") == "target-process"
        and isinstance(parsed.get("event_id"), str)
        and isinstance(parsed.get("event_type"), str)
        and isinstance(parsed.get("message"), str)
    ):
        message = parsed["message"]
        try:
            application_record = json.loads(message)
        except ValueError:
            application_record = None
        details = (
            log_details(application_record, message)
            if isinstance(application_record, dict)
            else {"raw_record": message}
        )
        display_message = (
            log_field(application_record, "message", "msg", "log") or message
            if isinstance(application_record, dict)
            else message
        )
        return {
            **provenance,
            **details,
            "collector_record": line,
            "event_id": event_id,
            "source_event_id": parsed["event_id"],
            "event_type": (
                "process_lifecycle"
                if parsed["event_type"] == "process_lifecycle"
                else "process_log"
            ),
            "signal_type": "log",
            "source": "target-log-file",
            "reported_source": "target-process",
            "capture_run_id": parsed.get("capture_run_id"),
            "reported_pid": parsed.get("pid"),
            "exit_code": parsed.get("exit_code"),
            "occurred_at": log_timestamp(parsed) or sensor_read_at,
            "stream": parsed.get("stream"),
            "message": display_message,
        }
    if isinstance(parsed, dict):
        message = log_field(parsed, "message", "msg", "log") or line
        return {
            **provenance,
            **log_details(parsed, line),
            "event_id": event_id,
            "event_type": "application_log",
            "signal_type": "log",
            "source": "target-log-file",
            "occurred_at": log_timestamp(parsed) or sensor_read_at,
            "message": message,
        }
    return {
        **provenance,
        "event_id": event_id,
        "event_type": "log_line",
        "signal_type": "log",
        "source": "target-log-file",
        "occurred_at": sensor_read_at,
        "message": line,
        "raw_record": line,
    }


class EventStore:
    def __init__(self) -> None:
        if min(EVENT_MAX_ROWS, EVENT_TTL_SECONDS, REJECTED_MAX_ROWS) < 1:
            raise ValueError("event storage limits must be positive")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.condition = threading.Condition()
        self.db = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS events ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_id TEXT NOT NULL UNIQUE,"
            "event_type TEXT NOT NULL,"
            "occurred_at TEXT,"
            "received_at TEXT NOT NULL,"
            "event_json TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS events_type_time ON events(event_type, occurred_at);"
            "CREATE INDEX IF NOT EXISTS events_request_id_seq "
            "ON events(json_extract(event_json,'$.request_id'), seq DESC);"
            "CREATE INDEX IF NOT EXISTS events_trace_id_seq "
            "ON events(json_extract(event_json,'$.trace_id'), seq DESC);"
            "CREATE INDEX IF NOT EXISTS events_source_seq "
            "ON events(json_extract(event_json,'$.source'), seq DESC);"
            "CREATE INDEX IF NOT EXISTS events_received_at_seq "
            "ON events(received_at DESC, seq DESC);"
            "CREATE TABLE IF NOT EXISTS rejected ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "reason TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "received_at TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS counter_state ("
            "name TEXT PRIMARY KEY, value INTEGER NOT NULL, initialized_at TEXT NOT NULL);"
        )
        stamp = utc_now()
        with self.db:
            for name, initial in (
                ("events_ingested_total", "SELECT COUNT(*) FROM events"),
                ("events_evicted_total", "SELECT 0"),
                ("rejected_total", "SELECT COUNT(*) FROM rejected"),
            ):
                self.db.execute(
                    f"INSERT OR IGNORE INTO counter_state(name,value,initialized_at) "
                    f"SELECT ?,({initial}),?",
                    (name, stamp),
                )
        self._import_legacy_log()

    def increment(self, name: str, amount: int) -> None:
        if amount:
            self.db.execute("UPDATE counter_state SET value=value+? WHERE name=?", (amount, name))

    def _import_legacy_log(self) -> None:
        legacy_path = DB_PATH.with_name("observations.jsonl")
        if not legacy_path.exists() or self.latest_seq() != 0:
            return
        with legacy_path.open(encoding="utf-8") as source:
            while True:
                chunk = list(itertools.islice(source, 500))
                if not chunk:
                    break
                events = [json.loads(line) for line in chunk if line.strip()]
                self.append(events, [])

    def append(
        self, events: list[dict[str, object]], rejected: list[tuple[str, str]]
    ) -> tuple[int, int]:
        accepted = 0
        with self.condition:
            with self.db:
                for event in events:
                    received_at = utc_now()
                    event["collector_received_at"] = received_at
                    cursor = self.db.execute(
                        "INSERT OR IGNORE INTO events(event_id,event_type,occurred_at,received_at,event_json) "
                        "VALUES(?,?,?,?,?)",
                        (
                            event["event_id"],
                            event["event_type"],
                            event.get("occurred_at"),
                            received_at,
                            json.dumps(event, separators=(",", ":")),
                        ),
                    )
                    accepted += cursor.rowcount
                self.db.executemany(
                    "INSERT INTO rejected(reason,payload_sha256,received_at) VALUES(?,?,?)",
                    [(reason, digest, utc_now()) for reason, digest in rejected],
                )
                self.increment("events_ingested_total", accepted)
                self.increment("rejected_total", len(rejected))
            if accepted:
                self.condition.notify_all()
        return accepted, len(rejected)

    def latest_seq(self) -> int:
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            return int(db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])

    def page(self, after: int, limit: int) -> list[dict[str, object]]:
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            rows = db.execute(
                "SELECT seq,event_json FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)
            ).fetchall()
        return [{**json.loads(payload), "seq": seq} for seq, payload in rows]

    def search(
        self, field: str, value: str, before: int | None, limit: int
    ) -> list[dict[str, object]]:
        path = SEARCH_FIELDS[field]
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            rows = db.execute(
                "SELECT seq,event_json FROM events "
                f"WHERE json_extract(event_json,'{path}')=? "
                "AND (? IS NULL OR seq<?) ORDER BY seq DESC LIMIT ?",
                (value, before, before, limit),
            ).fetchall()
        return [{**json.loads(payload), "seq": seq} for seq, payload in rows]

    def sources(self, limit: int) -> list[dict[str, object]]:
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            rows = db.execute(
                "SELECT json_extract(event_json,'$.source'), "
                "json_extract(event_json,'$.sensor_file'), COUNT(*), MAX(seq), "
                "MAX(received_at) FROM events "
                "GROUP BY json_extract(event_json,'$.source'), "
                "json_extract(event_json,'$.sensor_file') "
                "ORDER BY MAX(seq) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "source": source,
                "sensor_file": file,
                "events_retained": count,
                "latest_seq": latest_seq,
                "last_received_at": last_received_at,
            }
            for source, file, count, latest_seq, last_received_at in rows
        ]

    def timeline(
        self,
        start_at: str,
        end_at: str,
        source: str | None,
        before: int | None,
        limit: int,
    ) -> list[dict[str, object]]:
        clauses = ["received_at>=?", "received_at<=?"]
        values: list[str | int] = [start_at, end_at]
        if source is not None:
            clauses.append("json_extract(event_json,'$.source')=?")
            values.append(source)
        if before is not None:
            clauses.append("seq<?")
            values.append(before)
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            rows = db.execute(
                "SELECT seq,event_json FROM events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY seq DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [{**json.loads(payload), "seq": seq} for seq, payload in rows]

    def metrics(self) -> dict[str, int | str | None]:
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            target_logs = db.execute(
                "SELECT COUNT(*), MAX(received_at) FROM events "
                "WHERE json_extract(event_json,'$.source')='target-log-file'"
            ).fetchone()
            proxy_events = db.execute(
                "SELECT COUNT(*), MAX(received_at) FROM events "
                "WHERE json_extract(event_json,'$.source')='blue-ingress-proxy'"
            ).fetchone()
            counters = {
                name: (int(value), initialized_at)
                for name, value, initialized_at in db.execute(
                    "SELECT name,value,initialized_at FROM counter_state"
                )
            }
            return {
                "events_total": int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
                "events_ingested_total": counters["events_ingested_total"][0],
                "events_evicted_total": counters["events_evicted_total"][0],
                "rejected_total": counters["rejected_total"][0],
                "counters_started_at": counters["events_ingested_total"][1],
                "latest_seq": int(
                    db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
                ),
                "oldest_seq": int(
                    db.execute("SELECT COALESCE(MIN(seq),0) FROM events").fetchone()[0]
                ),
                "target_log_events": int(target_logs[0]),
                "target_log_last_received_at": target_logs[1],
                "proxy_events": int(proxy_events[0]),
                "proxy_last_received_at": proxy_events[1],
            }

    def cleanup(self) -> None:
        cutoff = datetime.fromtimestamp(time.time() - EVENT_TTL_SECONDS, timezone.utc).isoformat()
        with self.condition:
            with self.db:
                expired = self.db.execute(
                    "DELETE FROM events WHERE received_at < ?", (cutoff,)
                ).rowcount
                count = int(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
                excess = max(0, count - EVENT_MAX_ROWS)
                if excess:
                    boundary = int(
                        self.db.execute(
                            "SELECT seq FROM events ORDER BY seq LIMIT 1 OFFSET ?", (excess - 1,)
                        ).fetchone()[0]
                    )
                    self.db.execute("DELETE FROM events WHERE seq <= ?", (boundary,))
                self.increment("events_evicted_total", expired + excess)
                self.db.execute("DELETE FROM rejected WHERE received_at < ?", (cutoff,))
                rejected_count = int(self.db.execute("SELECT COUNT(*) FROM rejected").fetchone()[0])
                rejected_excess = rejected_count - REJECTED_MAX_ROWS
                if rejected_excess > 0:
                    self.db.execute(
                        "DELETE FROM rejected WHERE id IN "
                        "(SELECT id FROM rejected ORDER BY id LIMIT ?)",
                        (rejected_excess,),
                    )


STORE = EventStore()


class BodyStore:
    def __init__(self) -> None:
        if BODY_MAX_DISK_BYTES < 1 or BODY_TTL_SECONDS < 1:
            raise ValueError("body storage limits must be positive")
        BODY_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.evicted_total = 0
        self.cleanup_errors_total = 0
        self.files = 0
        self.bytes = 0
        self.cleanup()

    def cleanup(self) -> None:
        now = time.time()
        live: list[tuple[float, int, Path]] = []
        with self.lock:
            for path in BODY_DIR.iterdir():
                if path.suffix not in (".body", ".part"):
                    continue
                try:
                    stat = path.stat()
                    if path.suffix == ".part":
                        if now - stat.st_mtime > max(BODY_TTL_SECONDS, 86400):
                            path.unlink()
                        continue
                    if now - stat.st_mtime > BODY_TTL_SECONDS:
                        path.unlink()
                        self.evicted_total += 1
                    else:
                        live.append((stat.st_mtime, stat.st_size, path))
                except OSError:
                    self.cleanup_errors_total += 1
            total = sum(size for _, size, _ in live)
            for _, size, path in sorted(live):
                if total <= BODY_MAX_DISK_BYTES:
                    break
                try:
                    path.unlink()
                    total -= size
                    self.evicted_total += 1
                except OSError:
                    self.cleanup_errors_total += 1
            self.files = sum(1 for _, _, path in live if path.exists())
            self.bytes = total

    def metrics(self) -> dict[str, int]:
        self.cleanup()
        with self.lock:
            return {
                "body_files": self.files,
                "body_bytes": self.bytes,
                "body_evicted_total": self.evicted_total,
                "body_cleanup_errors_total": self.cleanup_errors_total,
            }


BODY_STORE = BodyStore()


def clean_bodies_forever() -> None:
    while True:
        time.sleep(60)
        BODY_STORE.cleanup()
        STORE.cleanup()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    role = "query"

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        if self.role != "query":
            self.send_json(404, {"error": "not found"})
            return
        if parsed.path == "/metrics":
            self.send_json(200, {**STORE.metrics(), **BODY_STORE.metrics(), **collector_metrics()})
            return
        if parsed.path == "/sources":
            try:
                limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
                if not 1 <= limit <= 100:
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "invalid source limit"})
                return
            sources = STORE.sources(limit + 1)
            self.send_json(200, {"sources": sources[:limit], "has_more": len(sources) > limit})
            return
        if parsed.path == "/timeline":
            try:
                params = parse_qs(parsed.query)
                start = parse_timeline_time(params.get("start_at", [""])[0])
                end = parse_timeline_time(params.get("end_at", [""])[0])
                source = params.get("source", [None])[0]
                before_param = params.get("before", [""])[0]
                before = int(before_param) if before_param else None
                limit = int(params.get("limit", ["50"])[0])
                if (
                    not 0 < (end - start).total_seconds() <= 3600
                    or not 1 <= limit <= 100
                    or (
                        source is not None
                        and source not in ("blue-ingress-proxy", "target-log-file")
                    )
                    or (before is not None and before < 1)
                ):
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "invalid timeline bounds, source, cursor or limit"})
                return
            events = STORE.timeline(start.isoformat(), end.isoformat(), source, before, limit + 1)
            self.send_json(
                200,
                {
                    "events": events[:limit],
                    "next_before": events[limit - 1]["seq"] if len(events) >= limit else None,
                    "has_more": len(events) > limit,
                },
            )
            return
        if parsed.path == "/search":
            try:
                params = parse_qs(parsed.query)
                field = params.get("field", [""])[0]
                value = params.get("value", [""])[0]
                limit = int(params.get("limit", ["20"])[0])
                before_param = params.get("before", [""])[0]
                before = int(before_param) if before_param else None
                if (
                    field not in SEARCH_FIELDS
                    or not 1 <= len(value) <= 256
                    or not 1 <= limit <= 100
                    or (before is not None and before < 1)
                ):
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "invalid search field, value, cursor or limit"})
                return
            events = STORE.search(field, value, before, limit + 1)
            self.send_json(
                200,
                {
                    "events": events[:limit],
                    "next_before": events[limit - 1]["seq"] if len(events) >= limit else None,
                    "has_more": len(events) > limit,
                },
            )
            return
        if parsed.path.startswith("/bodies/"):
            ref = parsed.path.removeprefix("/bodies/")
            if not re.fullmatch(r"[0-9a-f]{32}", ref):
                self.send_json(400, {"error": "invalid body reference"})
                return
            try:
                body = (BODY_DIR / f"{ref}.body").open("rb")
            except FileNotFoundError:
                self.send_json(404, {"error": "body not found or expired"})
                return
            with body:
                size = os.fstat(body.fileno()).st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                try:
                    while chunk := body.read(64 * 1024):
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
            return
        if parsed.path not in ("/events", "/stream"):
            self.send_json(404, {"error": "not found"})
            return
        try:
            params = parse_qs(parsed.query)
            default_after = (
                self.headers.get("Last-Event-ID", "0") if parsed.path == "/stream" else "0"
            )
            after = int(params.get("after", [default_after])[0])
            limit = int(params.get("limit", ["100"])[0])
            if after < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "invalid cursor or limit"})
            return
        if parsed.path == "/events":
            events = STORE.page(after, limit + 1)
            self.send_json(
                200,
                {
                    "events": events[:limit],
                    "next_after": events[limit - 1]["seq"]
                    if len(events) >= limit
                    else (events[-1]["seq"] if events else after),
                    "has_more": len(events) > limit,
                },
            )
            return
        if "after" not in params and "Last-Event-ID" not in self.headers:
            after = STORE.latest_seq()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.connection.settimeout(10)
        try:
            while True:
                events = STORE.page(after, 200)
                if not events:
                    with STORE.condition:
                        STORE.condition.wait_for(lambda: STORE.latest_seq() > after, timeout=10)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    self.wfile.write(
                        b"id: "
                        + str(event["seq"]).encode()
                        + b"\n"
                        + b"data: "
                        + json.dumps(event, separators=(",", ":")).encode()
                        + b"\n\n"
                    )
                    after = int(event["seq"])
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_POST(self) -> None:
        if self.role != "ingest" or urlsplit(self.path).path != "/ingest":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= MAX_BATCH_BYTES:
            self.send_json(413, {"error": "invalid batch size"})
            return
        lines = self.rfile.read(length).splitlines()
        events = []
        rejected = []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(normalize(json.loads(line)))
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                rejected.append((type(error).__name__, hashlib.sha256(line).hexdigest()))
        accepted, rejected_count = STORE.append(events, rejected)
        self.send_json(202, {"accepted": accepted, "rejected": rejected_count})


class QueryHandler(Handler):
    role = "query"


class IngestHandler(Handler):
    role = "ingest"


if __name__ == "__main__":
    STORE.cleanup()
    threading.Thread(target=clean_bodies_forever, daemon=True).start()
    ingest = ThreadingHTTPServer(("0.0.0.0", 8081), IngestHandler)
    ingest.daemon_threads = True
    threading.Thread(target=ingest.serve_forever, daemon=True).start()
    query = ThreadingHTTPServer(("0.0.0.0", 8082), QueryHandler)
    query.daemon_threads = True
    query.serve_forever()
