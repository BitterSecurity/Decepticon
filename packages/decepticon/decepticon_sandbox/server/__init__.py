"""Sandbox daemon — the server peer of `HTTPTransportSandbox`.

Runs inside the sandbox container, wraps a `LocalShellBackend` (which
already implements the BaseSandbox semantics for direct-host execution),
and exposes `execute`, `upload_files`, `download_files` over HTTP.

Entry point: `python -m decepticon_sandbox.server`.
"""

from decepticon_sandbox.server.app import app

__all__ = ["app"]
