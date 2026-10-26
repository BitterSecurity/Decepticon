"""Bounded stdio bridge, executed INSIDE an OpenShell-managed workload.

There is no configurable upstream URL: only the workload's own loopback daemon.
The caller must launch this module through an admitted OpenShell session.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
SESSION_PATHS = frozenset(
    {
        "/execute",
        "/upload_files",
        "/download_files",
        "/execute_tmux_async",
        "/start_background",
        "/poll_completion",
        "/kill_session",
        "/read_session_log_diff",
        "/reset_session_log_offset",
        "/session_log_path",
    }
)


def exchange(message: dict) -> dict:
    path = message.get("path")
    if path not in SESSION_PATHS:
        raise ValueError("unsupported session operation")
    timeout = message.get("timeout", 120)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 3600
    ):
        raise ValueError("invalid session timeout")
    payload = json.dumps(message.get("body", {}), allow_nan=False).encode()
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("session request too large")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("SANDBOX_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        "http://127.0.0.1:9999" + path,
        data=payload,
        headers=headers,
        method="POST",
    )
    # Never use an environment-configured HTTP proxy for local control traffic.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(MAX_MESSAGE_BYTES + 1)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("session response too large")
        return {"status": response.code, "body": json.loads(raw)}


def main() -> None:
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("session request too large")
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise ValueError("session request must be an object")
    print(json.dumps(exchange(message), allow_nan=False))


if __name__ == "__main__":
    main()
