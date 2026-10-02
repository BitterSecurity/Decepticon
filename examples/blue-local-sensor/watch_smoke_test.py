import json
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

BASE = "http://127.0.0.1"
SENSOR = BASE + ":18081"
MONITOR = BASE + ":18085"
PROXY = BASE + ":18080"
PROJECT = "decepticon-blue-sensor-poc"


def get_json(url: str) -> dict:
    with urlopen(url, timeout=10) as response:
        return json.load(response)


class AgentHandler(BaseHTTPRequestHandler):
    threads: list[str] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert request["assistant_id"] == "blue_cell"
        assert request["if_not_exists"] == "create"
        self.threads.append(self.path.split("/")[2])
        prompt = request["input"]["messages"][0]["content"]
        match = re.search(r"blue_sensor_events\(after=(\d+), limit=(\d+)\)", prompt)
        assert match is not None
        after, limit = map(int, match.groups())
        page = get_json(f"{SENSOR}/events?after={after}&limit={limit}")
        observed = page["events"]
        flagged = [item["seq"] for item in observed if "/watch-probe/" in item.get("uri", "")]
        verdict = (
            {
                "decision": "alert",
                "severity": "medium",
                "summary": "Fixture probe was observed; impact is unverified.",
                "event_seqs": flagged,
            }
            if flagged
            else {
                "decision": "no_alert",
                "summary": "No fixture probe in this window.",
                "event_seqs": [],
            }
        )
        body = json.dumps(
            {
                "messages": [
                    {"type": "human", "content": prompt},
                    {
                        "type": "ai",
                        "tool_calls": [
                            {
                                "id": "sensor-read",
                                "name": "blue_sensor_events",
                                "args": {"after": after, "limit": limit},
                            }
                        ],
                    },
                    {"type": "tool", "tool_call_id": "sensor-read", "content": json.dumps(page)},
                    {"type": "ai", "content": json.dumps(verdict)},
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def wait_for(predicate, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except OSError:
            pass
        time.sleep(0.5)
    raise AssertionError("timed out waiting for Blue watch")


def compose(*args: str) -> None:
    env = os.environ.copy()
    env.update(
        BLUE_AGENT_URL="http://host.docker.internal:18224",
        BLUE_WATCH_INTERVAL_SECONDS="2",
    )
    subprocess.run(
        ["docker", "compose", "--profile", "fixture", "-p", PROJECT, "-f", "compose.yaml", *args],
        check=True,
        env=env,
        cwd=Path(__file__).resolve().parent,
    )


def send_probe() -> None:
    try:
        with urlopen(f"{PROXY}/watch-probe/{uuid.uuid4()}", timeout=10):
            pass
    except HTTPError as error:
        assert error.code == 404


def alerts(after: int) -> list[dict]:
    return [
        notice
        for notice in get_json(f"{MONITOR}/notifications?after={after}&limit=100")["notifications"]
        if notice["kind"] == "watch_alert"
    ]


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 18224), AgentHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        compose("up", "-d", "--force-recreate", "--wait", "blue-monitor")
        initial = get_json(f"{MONITOR}/metrics")["watch_cursor"]
        notice_cursor = get_json(f"{MONITOR}/notifications?after=0&limit=100")["next_after"]
        send_probe()
        first = wait_for(lambda: alerts(notice_cursor))
        assert get_json(f"{MONITOR}/metrics")["watch_cursor"] > initial
        with urlopen(f"{PROXY}/", timeout=10):
            pass
        wait_for(lambda: len(AgentHandler.threads) >= 2)
        first_thread = AgentHandler.threads[0]
        assert all(thread == first_thread for thread in AgentHandler.threads)
        compose("up", "-d", "--force-recreate", "--wait", "blue-monitor")
        send_probe()
        wait_for(lambda: len(AgentHandler.threads) >= 3)
        assert AgentHandler.threads[-1] == first_thread
        wait_for(lambda: len(alerts(notice_cursor)) >= len(first) + 1)
        print(f"PASS: {len(first)} alert, 3 agent runs, stable thread across restart")
    finally:
        compose("down")
        server.shutdown()


if __name__ == "__main__":
    main()
