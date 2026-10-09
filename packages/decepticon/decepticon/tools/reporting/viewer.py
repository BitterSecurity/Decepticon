"""Local engagement viewer — lightweight HTTP server for run inspection.

Serves engagement data (findings, coverage, agent graph, notes) as a JSON
API. Designed to be consumed by a future SPA dashboard or by curl/browser.

Usage: from the CLI, `decepticon view [run-name]` starts the server.
No external dependencies beyond stdlib.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


class ViewerHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving engagement data."""

    workspace: Path = Path("/workspace")
    auth_token: str = ""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Token auth check
        if self.auth_token:
            token = params.get("token", [""])[0]
            if token != self.auth_token:
                self._respond(401, {"error": "unauthorized"})
                return

        routes: dict[str, Any] = {
            "/api/status": self._status,
            "/api/findings": self._findings,
            "/api/coverage": self._coverage,
            "/api/agents": self._agents,
            "/api/notes": self._notes,
            "/api/report": self._report,
            "/api/threat-models": self._threat_models,
        }

        handler = routes.get(path)
        if handler:
            try:
                data = handler()
                self._respond(200, data)
            except Exception as e:
                self._respond(500, {"error": str(e)})
        else:
            self._respond(404, {"error": "not found", "routes": list(routes.keys())})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/steer":
            # Live steering from viewer
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            try:
                msg = json.loads(body)
                # Write to guidance inbox
                inbox = self.workspace / "guidance" / "inbox.jsonl"
                inbox.parent.mkdir(parents=True, exist_ok=True)
                with open(inbox, "a", encoding="utf-8") as f:
                    msg["timestamp"] = time.time()
                    msg["source"] = "viewer"
                    f.write(json.dumps(msg, default=str) + "\n")
                self._respond(200, {"steered": True})
            except json.JSONDecodeError:
                self._respond(400, {"error": "invalid JSON"})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, data: Any) -> None:
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _status(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "exists": self.workspace.exists(),
            "timestamp": time.time(),
        }

    def _findings(self) -> dict:
        d = self.workspace / "findings"
        if not d.exists():
            return {"findings": [], "total": 0}
        findings = []
        for p in sorted(d.glob("vuln-*.json")):
            try:
                f = json.loads(p.read_text("utf-8"))
                if not f.get("deleted"):
                    findings.append(f)
            except Exception:
                pass
        severity_counts: dict[str, int] = {}
        for f in findings:
            s = f.get("severity", "unknown")
            severity_counts[s] = severity_counts.get(s, 0) + 1
        return {"findings": findings, "total": len(findings), "severity_counts": severity_counts}

    def _coverage(self) -> dict:
        p = self.workspace / "coverage.json"
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                pass
        return {"entries": []}

    def _agents(self) -> dict:
        p = self.workspace / "agents" / "graph.json"
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                pass
        return {"agents": {}}

    def _notes(self) -> dict:
        p = self.workspace / "notes.json"
        if p.exists():
            try:
                return {"notes": json.loads(p.read_text("utf-8"))}
            except Exception:
                pass
        return {"notes": []}

    def _report(self) -> dict:
        p = self.workspace / "final_report.json"
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                pass
        return {"status": "in_progress"}

    def _threat_models(self) -> dict:
        d = self.workspace / "threat_models"
        if not d.exists():
            return {"models": []}
        models = []
        for p in d.glob("*.json"):
            try:
                models.append(json.loads(p.read_text("utf-8")))
            except Exception:
                pass
        return {"models": models}

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default logging


def start_viewer(
    workspace: str = "",
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Start the local viewer HTTP server."""
    ws = Path(workspace) if workspace else _workspace()
    token = secrets.token_urlsafe(32)

    handler_class = type("Handler", (ViewerHandler,), {"workspace": ws, "auth_token": token})
    server = HTTPServer((host, port), handler_class)
    actual_port = server.server_address[1]

    url = f"http://{host}:{actual_port}/?token={token}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    return {
        "url": url,
        "host": host,
        "port": actual_port,
        "token": token,
        "workspace": str(ws),
    }


@tool
def start_engagement_viewer(host: str = "127.0.0.1", port: int = 0) -> str:
    """Start a local web viewer for the current engagement.

    WHEN TO USE: When the operator wants to inspect findings, coverage,
    agent status, or steer the engagement from a browser.

    Args:
        host: Bind address (default 127.0.0.1 for security)
        port: Port (default 0 = ephemeral)
    """
    result = start_viewer(host=host, port=port, open_browser=False)
    return _json(result)


VIEWER_TOOLS = [start_engagement_viewer]
