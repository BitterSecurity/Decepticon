import hashlib
import json
import socket
import time
import uuid
from collections.abc import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TARGET = "http://127.0.0.1:18080"
BLUE = "http://127.0.0.1:18081"
MAX_LATENCY_SECONDS = 3.0


def send(request: Request, expected_status: int) -> None:
    try:
        with urlopen(request, timeout=5) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
        error.close()
    assert status == expected_status, (status, expected_status)


def assert_body(event: dict[str, object], expected: bytes) -> None:
    ref = event["request_body_ref"]
    assert isinstance(ref, str)
    assert event["request_body_bytes_stored"] == len(expected)
    assert event["request_body_sha256"] == hashlib.sha256(expected).hexdigest()
    with urlopen(BLUE + "/bodies/" + ref, timeout=10) as response:
        assert response.read() == expected


def next_event(stream) -> dict[str, object]:
    while True:
        line = stream.readline()
        if line.startswith(b"data: "):
            return json.loads(line[6:])


def all_events() -> list[dict[str, object]]:
    events = []
    after = 0
    while True:
        with urlopen(BLUE + f"/events?after={after}&limit=100", timeout=5) as response:
            page = json.load(response)
        events.extend(page["events"])
        after = page["next_after"]
        if not page["has_more"]:
            return events


def await_events(
    stream, started_at: float, predicates: dict[str, Callable[[dict[str, object]], bool]]
) -> dict[str, dict[str, object]]:
    matched = {}
    while len(matched) < len(predicates):
        event = next_event(stream)
        for name, predicate in predicates.items():
            if name not in matched and predicate(event):
                matched[name] = event
                latency = time.monotonic() - started_at
                assert latency < MAX_LATENCY_SECONDS, (name, latency)
                print(
                    json.dumps(
                        {
                            "channel": name,
                            "event_id": event["event_id"],
                            "latency_seconds": round(latency, 3),
                        }
                    )
                )
    return matched


def main() -> None:
    try:
        with socket.create_connection(("127.0.0.1", 18082), timeout=1):
            raise AssertionError("fixture direct HTTP port is still open")
    except OSError:
        pass
    observed = []
    with urlopen(BLUE + "/stream", timeout=MAX_LATENCY_SECONDS) as stream:
        marker = str(uuid.uuid4())
        path = "/probe/" + marker
        query = "?marker=" + marker
        started_at = time.monotonic()
        send(Request(TARGET + path + query, headers={"User-Agent": "blue-sensor-smoke"}), 404)
        pair = await_events(
            stream,
            started_at,
            {
                "proxy": lambda event: (
                    event.get("event_type") == "http_access" and event.get("path") == path
                ),
                "process": lambda event: (
                    event.get("event_type") == "process_log" and path in event.get("message", "")
                ),
            },
        )
        assert pair["proxy"]["status"] == 404
        assert pair["proxy"]["uri"] == path + query
        assert pair["proxy"]["request_headers"]["User-Agent"] == ["blue-sensor-smoke"]
        observed.extend(pair.values())

        for method, path, status in (
            ("GET", "/", 200),
            ("GET", "/admin", 403),
            ("POST", "/login", 401),
        ):
            started_at = time.monotonic()
            body = b'{"username":"demo","password":"never-log-this"}' if method == "POST" else None
            headers = {"Content-Type": "application/json"} if body else {}
            send(Request(TARGET + path, data=body, headers=headers, method=method), status)
            event = await_events(
                stream,
                started_at,
                {
                    "proxy": lambda item: (
                        item.get("event_type") == "http_access"
                        and item.get("path") == path
                        and item.get("status") == status
                    )
                },
            )["proxy"]
            assert event["method"] == method
            if method == "POST":
                assert event["request_body_status"] == "captured"
                assert_body(event, body)
            observed.append(event)

    large_body = json.dumps({"payload": "x" * (9 * 1024 * 1024)}).encode()
    large_path = "/large/" + str(uuid.uuid4())
    with urlopen(BLUE + "/stream", timeout=MAX_LATENCY_SECONDS) as stream:
        started_at = time.monotonic()
        send(
            Request(
                TARGET + large_path,
                data=large_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            404,
        )
        large_event = await_events(
            stream,
            started_at,
            {
                "proxy": lambda event: (
                    event.get("event_type") == "http_access" and event.get("path") == large_path
                )
            },
        )["proxy"]
        assert large_event["request_body_status"] == "captured"
        assert_body(large_event, large_body)

    for body, content_type in (
        (b'{"password":"never-log-this"}', "application/x-www-form-urlencoded"),
        (b"username=demo&password=never-log-this", "application/x-www-form-urlencoded"),
        (bytes(range(256)) + b"\x00\xff", "application/octet-stream"),
    ):
        path = "/body/" + str(uuid.uuid4())
        with urlopen(BLUE + "/stream", timeout=MAX_LATENCY_SECONDS) as stream:
            started_at = time.monotonic()
            send(
                Request(
                    TARGET + path, data=body, headers={"Content-Type": content_type}, method="POST"
                ),
                404,
            )
            event = await_events(
                stream,
                started_at,
                {
                    "proxy": lambda item: (
                        item.get("event_type") == "http_access" and item.get("path") == path
                    )
                },
            )["proxy"]
            assert event["request_body_status"] == "captured"
            assert_body(event, body)

    burst_paths = {"/burst/" + str(uuid.uuid4()) for _ in range(10)}
    for path in burst_paths:
        send(Request(TARGET + path), 404)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        persisted = all_events()
        proxy_paths = {
            event.get("path") for event in persisted if event.get("event_type") == "http_access"
        }
        process_messages = [
            event.get("message", "")
            for event in persisted
            if event.get("event_type") == "process_log"
        ]
        if burst_paths <= proxy_paths and all(
            any(path in message for message in process_messages) for path in burst_paths
        ):
            break
        time.sleep(0.05)
    assert burst_paths <= proxy_paths
    assert all(any(path in message for message in process_messages) for path in burst_paths)

    persisted = all_events()
    ids = {event["event_id"] for event in persisted}
    assert all(event["event_id"] in ids for event in observed)
    assert all(event["target_id"] == "local-web" for event in observed)
    print("PASS: live events and all 10 burst requests reached Blue on both channels")


if __name__ == "__main__":
    main()
