"""Daemon entry point.

Run with `python -m decepticon_sandbox.server` (and that is what the
sandbox container's Dockerfile invokes when `SANDBOX_DAEMON=1`).

Env:
    SANDBOX_DAEMON_HOST   bind host (default 0.0.0.0)
    SANDBOX_DAEMON_PORT   bind port (default 9999)
    SANDBOX_ROOT_DIR      LocalShellBackend root (default /workspace)
    SANDBOX_DEFAULT_TIMEOUT  per-command timeout in seconds (default 120)
    SANDBOX_TOKEN         bearer token required on every request
                          when set (recommended even on loopback)
"""

from __future__ import annotations

import os

import uvicorn

from decepticon_sandbox.server.app import app


def main() -> None:
    host = os.environ.get("SANDBOX_DAEMON_HOST", "0.0.0.0")  # noqa: S104 # nosec B104 -- binds inside sandbox container only
    port = int(os.environ.get("SANDBOX_DAEMON_PORT", "9999"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
