"""Obscura browser provider — drives ``obscura mcp --http`` inside the sandbox.

One Obscura MCP server runs per engagement workspace, bound to 127.0.0.1 on
an ephemeral port with a random bearer token. Its state (pid, port, token,
log, cookie/storage profile) lives in ``<workspace>/.browser/obscura`` with
owner-only permissions. The server is started on first use and reused by
later calls; a dead or unhealthy server is replaced.

Obscura's own private-network guard is disabled
(``OBSCURA_ALLOW_PRIVATE_NETWORK=1``): in-scope internal targets must be
reachable, and the sandbox OpenShell policy compiled from ``plan/roe.json`` is
the single network authority for every connection the browser makes.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from decepticon_sandbox.browser.contract import Artifact, BrowserRequest, BrowserResult

NAME = "obscura"
BINARY_ENV = "DECEPTICON_OBSCURA_BIN"
_DEFAULT_TIMEOUT_S = 60.0
_START_TIMEOUT_S = 20.0
_PROXY_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


class ObscuraError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _ref_or_selector(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if args.get("ref"):
        out["ref"] = args["ref"]
    if args.get("selector"):
        out["selector"] = args["selector"]
    if not out:
        raise ObscuraError("invalid_request", "pass `ref` (from browser_snapshot) or `selector`")
    return out


def _pick(
    args: dict[str, Any], *names: str, rename: dict[str, str] | None = None
) -> dict[str, Any]:
    rename = rename or {}
    return {rename.get(n, n): args[n] for n in names if args.get(n) is not None}


def _wait(args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    timeout_s = float(args.get("timeout_s") or 30)
    if args.get("selector"):
        return "browser_wait_for", {"selector": args["selector"], "timeout": timeout_s}
    if args.get("text"):
        return "browser_wait_for_text", {"text": args["text"], "timeout": timeout_s}
    if args.get("function"):
        return "browser_wait_for_function", {
            "function": args["function"],
            "timeout": int(timeout_s * 1000),
        }
    raise ObscuraError("invalid_request", "wait needs one of `selector`, `text` or `function`")


# action -> (MCP tool name, argument mapper)
_MAP: dict[str, Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]] = {
    "navigate": lambda a: (
        "browser_navigate",
        _pick(a, "url", "wait_until", rename={"wait_until": "waitUntil"}),
    ),
    "back": lambda a: ("browser_back", {}),
    "forward": lambda a: ("browser_forward", {}),
    "reload": lambda a: ("browser_reload", {}),
    "snapshot": lambda a: ("browser_snapshot", _pick(a, "max_chars")),
    "markdown": lambda a: ("browser_markdown", _pick(a, "max_chars")),
    "links": lambda a: ("browser_links", _pick(a, "limit", "internal_only")),
    "forms": lambda a: ("browser_detect_forms", {}),
    "search_text": lambda a: (
        "browser_search",
        _pick(a, "query", "case_sensitive", "limit", "context_chars"),
    ),
    "click": lambda a: ("browser_click", _ref_or_selector(a)),
    "fill": lambda a: ("browser_fill", {**_ref_or_selector(a), "value": a.get("value", "")}),
    "type_text": lambda a: ("browser_type", {**_ref_or_selector(a), "text": a.get("text", "")}),
    "press": lambda a: ("browser_press_key", _pick(a, "key", "selector")),
    "select": lambda a: ("browser_select_option", _pick(a, "selector", "value")),
    "fill_form": lambda a: (
        "browser_fill_form",
        _pick(a, "fields", "submit_ref", "submit_selector"),
    ),
    "scroll": lambda a: ("browser_scroll", _pick(a, "direction", "amount", "ref", "selector")),
    "wait": _wait,
    "evaluate": lambda a: (
        "browser_evaluate",
        _pick(a, "expression", "timeout_ms", rename={"timeout_ms": "timeoutMs"}),
    ),
    "network_list": lambda a: ("browser_network_requests", {}),
    "console_list": lambda a: ("browser_console_messages", {}),
    "cookies_get": lambda a: ("browser_get_cookies", _pick(a, "domain")),
    "cookie_set": lambda a: (
        "browser_set_cookie",
        _pick(a, "name", "value", "domain", "path", "secure", "http_only"),
    ),
    "cookies_clear": lambda a: ("browser_clear_cookies", {}),
    "tab_new": lambda a: ("browser_tab_new", _pick(a, "url")),
    "tab_list": lambda a: ("browser_tab_list", {}),
    "tab_switch": lambda a: ("browser_tab_switch", _pick(a, "tab_id")),
    "tab_close": lambda a: ("browser_tab_close", _pick(a, "tab_id")),
    "screenshot": lambda a: ("browser_screenshot", _pick(a, "width", "height")),
    "pdf": lambda a: ("browser_pdf", _pick(a, "landscape", "print_background")),
    "close": lambda a: ("browser_close", {}),
}

_LOCAL_ACTIONS = frozenset({"capabilities", "storage_export", "storage_import"})
CAPABILITIES: frozenset[str] = frozenset(_MAP) | _LOCAL_ACTIONS


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A terminated child that its parent has not reaped still answers
    # kill(pid, 0); treat zombies as dead.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return stat.rpartition(")")[2].split()[:1] != ["Z"]


class ObscuraProvider:
    name = NAME

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.state_dir = workspace / ".browser" / NAME
        self.evidence_dir = workspace / "evidence" / "browser"
        self._state_file = self.state_dir / "server.json"
        self._rpc_id = 0
        # loopback only: never route the control channel through a proxy
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ── lifecycle ────────────────────────────────────────────────────────
    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        _private_dir(self.state_dir)
        with open(self.state_dir / ".lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _binary(self) -> str:
        configured = os.environ.get(BINARY_ENV)
        binary = configured or shutil.which("obscura")
        if not binary or not Path(binary).exists():
            raise ObscuraError(
                "provider_unavailable",
                "obscura binary not installed in the sandbox image (INSTALL_OBSCURA=false?)",
            )
        return binary

    def _read_state(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _healthy(self, state: dict[str, Any]) -> bool:
        pid = state.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            return False
        try:
            self._rpc(state, "tools/list", {}, timeout=5.0)
        except ObscuraError:
            return False
        return True

    def _start(self) -> dict[str, Any]:
        binary = self._binary()
        port = _free_port()
        token = secrets.token_hex(32)
        profile = _private_dir(self.state_dir / "profile")
        env = dict(os.environ)
        env.update(
            {
                "OBSCURA_MCP_TOKEN": token,
                "OBSCURA_ALLOW_PRIVATE_NETWORK": "1",
                "OBSCURA_STORAGE_DIR": str(profile),
            }
        )
        cmd = [binary, "mcp", "--http", "--host", "127.0.0.1", "--port", str(port)]
        proxy = next((env[k] for k in _PROXY_ENV if env.get(k)), None)
        if proxy:
            cmd += ["--proxy", proxy]
        log_path = self.state_dir / "server.log"
        with open(log_path, "ab") as log:
            os.chmod(log_path, 0o600)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=env,
                cwd=str(self.state_dir),
                start_new_session=True,
            )
        state = {"pid": proc.pid, "port": port, "token": token, "started_at": time.time()}
        deadline = time.monotonic() + _START_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise ObscuraError(
                    "provider_unavailable",
                    f"obscura mcp exited with {proc.returncode}; see {log_path}",
                )
            try:
                self._rpc(
                    state,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "decepticon", "version": "1"},
                    },
                    timeout=2.0,
                )
                break
            except ObscuraError:
                time.sleep(0.2)
        else:
            self._kill(state)
            raise ObscuraError("provider_unavailable", f"obscura mcp did not start; see {log_path}")
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.chmod(tmp, 0o600)
        tmp.replace(self._state_file)
        return state

    def _ensure(self) -> dict[str, Any]:
        state = self._read_state()
        if state and self._healthy(state):
            return state
        if state:
            self._kill(state)
        return self._start()

    @staticmethod
    def _kill(state: dict[str, Any]) -> None:
        pid = state.get("pid")
        if isinstance(pid, int) and _pid_alive(pid):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGTERM)
            for _ in range(25):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGKILL)

    def shutdown(self) -> None:
        with self._locked():
            state = self._read_state()
            if state:
                self._kill(state)
            with contextlib.suppress(FileNotFoundError):
                self._state_file.unlink()

    # ── transport ────────────────────────────────────────────────────────
    def _rpc(
        self, state: dict[str, Any], method: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        self._rpc_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
        ).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{state['port']}/mcp",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {state['token']}",
            },
        )
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ObscuraError("transport_error", f"obscura mcp {method}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ObscuraError("protocol_error", "obscura mcp returned a non-object")
        if payload.get("error"):
            raise ObscuraError("protocol_error", json.dumps(payload["error"])[:2000])
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    # ── actions ──────────────────────────────────────────────────────────
    def invoke(self, request: BrowserRequest) -> BrowserResult:
        action, args = request.action, request.args
        try:
            if action == "capabilities":
                return BrowserResult(
                    ok=True,
                    action=action,
                    provider=NAME,
                    text=json.dumps(sorted(CAPABILITIES)),
                )
            with self._locked():
                state = self._ensure()
                if action == "storage_export":
                    return self._storage_export(state, args)
                if action == "storage_import":
                    return self._storage_import(state, args)
                if action not in _MAP:
                    return BrowserResult.failure(
                        action, NAME, "unsupported_action", f"{NAME} does not implement {action}"
                    )
                tool, arguments = _MAP[action](args)
                return self._call(state, action, tool, arguments, self._timeout(args))
        except ObscuraError as exc:
            return BrowserResult.failure(action, NAME, exc.code, str(exc))

    @staticmethod
    def _timeout(args: dict[str, Any]) -> float:
        extra = float(args.get("timeout_s") or 0) + float(args.get("timeout_ms") or 0) / 1000
        return _DEFAULT_TIMEOUT_S + extra

    def _call(
        self,
        state: dict[str, Any],
        action: str,
        tool: str,
        arguments: dict[str, Any],
        timeout: float,
    ) -> BrowserResult:
        result = self._rpc(
            state, "tools/call", {"name": tool, "arguments": arguments}, timeout=timeout
        )
        texts: list[str] = []
        artifacts: list[Artifact] = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                texts.append(str(item.get("text", "")))
            elif kind == "image" and item.get("data"):
                artifacts.append(
                    self._write_artifact(action, item["data"], item.get("mimeType", "image/png"))
                )
            elif kind == "resource" and isinstance(item.get("resource"), dict):
                res = item["resource"]
                if res.get("blob"):
                    artifacts.append(
                        self._write_artifact(
                            action, res["blob"], res.get("mimeType", "application/octet-stream")
                        )
                    )
                elif res.get("text"):
                    texts.append(str(res["text"]))
        text = "\n".join(texts)
        if result.get("isError"):
            return BrowserResult.failure(action, NAME, "action_failed", text or "action failed")
        return BrowserResult(ok=True, action=action, provider=NAME, text=text, artifacts=artifacts)

    def _write_artifact(self, action: str, b64: str, mime: str) -> Artifact:
        data = base64.b64decode(b64)
        digest = hashlib.sha256(data).hexdigest()
        ext = {"image/png": "png", "application/pdf": "pdf"}.get(mime, "bin")
        out_dir = self.evidence_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = out_dir / f"{stamp}-{action}-{digest[:12]}.{ext}"
        path.write_bytes(data)
        return Artifact(kind=action, path=str(path), mime_type=mime, sha256=digest, bytes=len(data))

    def _storage_path(self, args: dict[str, Any]) -> Path:
        name = str(args.get("name") or "default")
        if not name.replace("-", "").replace("_", "").isalnum() or len(name) > 64:
            raise ObscuraError("invalid_request", "storage name must be [A-Za-z0-9_-]{1,64}")
        return _private_dir(self.workspace / ".browser" / "storage") / f"{name}.json"

    def _storage_export(self, state: dict[str, Any], args: dict[str, Any]) -> BrowserResult:
        result = self._call(
            state, "storage_export", "browser_storage_state", {}, _DEFAULT_TIMEOUT_S
        )
        if not result.ok:
            return result
        try:
            parsed = json.loads(result.text)
        except ValueError as exc:
            raise ObscuraError("protocol_error", f"storage state is not JSON: {exc}") from exc
        path = self._storage_path(args)
        path.write_text(json.dumps(parsed))
        os.chmod(path, 0o600)
        cookies = parsed.get("cookies") if isinstance(parsed, dict) else None
        summary = {
            "saved_to": str(path),
            "cookies": len(cookies) if isinstance(cookies, list) else None,
            "keys": sorted(parsed) if isinstance(parsed, dict) else None,
        }
        return BrowserResult(
            ok=True, action="storage_export", provider=NAME, text=json.dumps(summary)
        )

    def _storage_import(self, state: dict[str, Any], args: dict[str, Any]) -> BrowserResult:
        path = self._storage_path(args)
        try:
            parsed = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ObscuraError("invalid_request", f"cannot read saved state {path}: {exc}") from exc
        return self._call(
            state,
            "storage_import",
            "browser_set_storage_state",
            {"state": parsed},
            _DEFAULT_TIMEOUT_S,
        )
