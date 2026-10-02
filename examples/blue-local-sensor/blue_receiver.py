import hashlib
import itertools
import json
import os
import re
import sqlite3
import threading
import time
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
MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_PAGE_SIZE = 1000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or not isinstance(raw.get("sensor_file"), str):
        raise ValueError("missing sensor provenance")
    stable_record = {key: value for key, value in raw.items() if key != "sensor_read_at"}
    event_id = hashlib.sha256(
        json.dumps(stable_record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sensor_file = raw["sensor_file"]
    sensor_read_at = raw.get("sensor_read_at")
    provenance = {
        "target_id": TARGET_ID,
        "sensor_read_at": sensor_read_at,
        "sensor_file": sensor_file,
        "sensor_offset": raw.get("sensor_offset"),
    }
    if sensor_file.startswith("/sensor-logs/access"):
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
        return {**parsed, **provenance}
    return {
        **provenance,
        "event_id": event_id,
        "event_type": "log_line",
        "source": "target-log-file",
        "occurred_at": sensor_read_at,
        "message": line,
    }


class EventStore:
    def __init__(self) -> None:
        if min(EVENT_MAX_ROWS, EVENT_TTL_SECONDS, REJECTED_MAX_ROWS) < 1:
            raise ValueError("event storage limits must be positive")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.condition = threading.Condition()
        self.evicted_total = 0
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
            "CREATE TABLE IF NOT EXISTS rejected ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "reason TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "received_at TEXT NOT NULL);"
        )
        self._import_legacy_log()

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

    def append(self, events: list[dict[str, object]], rejected: list[tuple[str, str]]) -> tuple[int, int]:
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

    def metrics(self) -> dict[str, int]:
        with sqlite3.connect(DB_PATH, timeout=10) as db:
            return {
                "events_total": int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
                "events_evicted_total": self.evicted_total,
                "rejected_total": int(db.execute("SELECT COUNT(*) FROM rejected").fetchone()[0]),
                "latest_seq": int(db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]),
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
                self.evicted_total += expired + excess
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
            self.send_json(200, {**STORE.metrics(), **BODY_STORE.metrics()})
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
                    pass
            return
        if parsed.path not in ("/events", "/stream"):
            self.send_json(404, {"error": "not found"})
            return
        try:
            params = parse_qs(parsed.query)
            default_after = self.headers.get("Last-Event-ID", "0") if parsed.path == "/stream" else "0"
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
                    "next_after": events[limit - 1]["seq"] if len(events) >= limit else (events[-1]["seq"] if events else after),
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
                        b"id: " + str(event["seq"]).encode() + b"\n" +
                        b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"
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
