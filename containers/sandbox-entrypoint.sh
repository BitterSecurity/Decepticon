#!/bin/bash
# Make /workspace world-accessible so the host user can read findings,
# reports, and engagement files without sudo.
# Security boundary is the container itself, not file permissions.
chmod -R 777 /workspace 2>/dev/null || true
# Ensure all NEW files are also world-readable/writable
umask 0000

# HTTP daemon mode (default ON). The container runs the FastAPI sandbox
# server on ``localhost:9999`` for every deployment target — dev,
# local-docker, GCE Spot VMs, and Cloud Run multi-container. The agent
# process (HTTPSandbox in decepticon/backends/http_sandbox.py) talks to
# it over HTTP. The previous default of ``0`` (tail -f keep-alive) is
# kept as an opt-out for legacy ``docker exec`` workflows: set
# ``SANDBOX_DAEMON=0`` to disable the daemon.
if [ "${SANDBOX_DAEMON:-1}" = "1" ]; then
    exec python3 -m decepticon_sandbox.server
fi

exec "$@"
