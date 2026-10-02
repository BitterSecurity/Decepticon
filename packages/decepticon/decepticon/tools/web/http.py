"""HTTP request/response history with replay + diff.

A Burp-lite for the agent: every outgoing request is recorded to a
bounded history buffer so later iterations can replay, tamper, and
diff. The buffer is in-memory but can be serialised to JSON for
persistence in the sandbox workspace.

The actual network I/O uses httpx (already a dependency). This module
focuses on the *record* semantics that make bug hunting reproducible:

- Immutable (HTTPRequest, HTTPResponse) dataclasses
- HTTPSession holds cookies + headers and records every call
- HTTPHistory bounded deque (LRU) with search + replay
- Diff support: inline side-by-side string diff of two responses
"""

from __future__ import annotations

import difflib
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import httpx

MAX_HISTORY = 512


# ── Data types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HTTPRequest:
    id: str
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timestamp: float
    tag: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "url": self.url,
            "headers": dict(self.headers),
            "body": self.body.decode("utf-8", errors="replace"),
            "timestamp": self.timestamp,
            "tag": self.tag,
        }


@dataclass(frozen=True)
class HTTPResponse:
    id: str
    request_id: str
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "status": self.status,
            "headers": dict(self.headers),
            "body": self.body.decode("utf-8", errors="replace"),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "timestamp": self.timestamp,
        }

    def text(self, max_chars: int = 4000) -> str:
        text = self.body.decode("utf-8", errors="replace")
        return (
            text
            if len(text) <= max_chars
            else text[:max_chars] + f"\n[...{len(text) - max_chars} truncated]"
        )


# ── History ─────────────────────────────────────────────────────────────


class HTTPHistory:
    """Bounded request/response history with search + replay support.

    Pairs are indexed by request ID so the agent can call
    ``get_by_id``/``replay`` without scanning the deque.
    """

    def __init__(self, maxlen: int = MAX_HISTORY) -> None:
        self._entries: deque[tuple[HTTPRequest, HTTPResponse | None]] = deque(maxlen=maxlen)
        self._by_id: dict[str, tuple[HTTPRequest, HTTPResponse | None]] = {}

    def record(self, req: HTTPRequest, resp: HTTPResponse | None = None) -> None:
        if self._entries.maxlen is not None and len(self._entries) >= self._entries.maxlen:
            evicted_req, _ = self._entries.popleft()
            self._by_id.pop(evicted_req.id, None)
        self._entries.append((req, resp))
        self._by_id[req.id] = (req, resp)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[tuple[HTTPRequest, HTTPResponse | None]]:
        return iter(self._entries)

    def get_by_id(self, request_id: str) -> tuple[HTTPRequest, HTTPResponse | None] | None:
        return self._by_id.get(request_id)

    def search(
        self,
        *,
        url_substr: str | None = None,
        status: int | None = None,
        method: str | None = None,
        tag: str | None = None,
    ) -> list[tuple[HTTPRequest, HTTPResponse | None]]:
        out: list[tuple[HTTPRequest, HTTPResponse | None]] = []
        for req, resp in self._entries:
            if url_substr and url_substr not in req.url:
                continue
            if method and req.method.upper() != method.upper():
                continue
            if tag and req.tag != tag:
                continue
            if status is not None and (resp is None or resp.status != status):
                continue
            out.append((req, resp))
        return out

    def dump(self) -> list[dict[str, Any]]:
        return [
            {"request": req.to_dict(), "response": resp.to_dict() if resp else None}
            for req, resp in self._entries
        ]

    @classmethod
    def from_dump(cls, payload: Iterable[dict[str, Any]]) -> HTTPHistory:
        hist = cls()
        for entry in payload:
            try:
                r = entry["request"]
                req = HTTPRequest(
                    id=r["id"],
                    method=r["method"],
                    url=r["url"],
                    headers=dict(r["headers"]),
                    body=r["body"].encode("utf-8"),
                    timestamp=r["timestamp"],
                    tag=r.get("tag", ""),
                )
                resp: HTTPResponse | None = None
                if entry.get("response"):
                    rr = entry["response"]
                    resp = HTTPResponse(
                        id=rr["id"],
                        request_id=rr["request_id"],
                        status=rr["status"],
                        headers=dict(rr["headers"]),
                        body=rr["body"].encode("utf-8"),
                        elapsed_ms=rr["elapsed_ms"],
                        timestamp=rr["timestamp"],
                    )
            except KeyError as e:
                raise ValueError(f"Malformed history entry: missing key {e}") from e
            hist.record(req, resp)
        return hist


# ── Session ─────────────────────────────────────────────────────────────


class HTTPSession:
    """HTTP client wrapper that records everything to an HTTPHistory.

    The cookie jar and default headers are shared across calls so the
    agent can script authenticated flows without re-specifying tokens.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        verify: bool = True,
        follow_redirects: bool = True,
        history: HTTPHistory | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            verify=verify,
            follow_redirects=follow_redirects,
            headers=headers or {},
            timeout=30.0,
        )
        self.history = history or HTTPHistory()
        # Operator-seeded auth headers, keyed by lowercase host. Headers are
        # NOT set as httpx client defaults: this tool runs host-side, outside
        # the sandbox egress edge, and follows redirects, so a global default
        # Authorization would be sent to every host the agent touches
        # (redirect targets included). Scoping to the host the credential names
        # keeps a Bearer token from leaking cross-origin. Cookies need no such
        # table: httpx's jar is already domain-scoped.
        self._seeded_headers: dict[str, dict[str, str]] = {}

    def seed_credential(
        self,
        *,
        host: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        """Seed operator-provided auth for one host.

        ``headers`` are sent only on requests to ``host``. ``cookies`` are
        loaded into the httpx jar scoped to ``host``.
        """
        key = host.strip().lower()
        if not key:
            return
        if headers:
            self._seeded_headers.setdefault(key, {}).update(headers)
        for name, value in (cookies or {}).items():
            self._client.cookies.set(name, value, domain=key, path="/")

    def save_cookies(self, path: str | Path) -> None:
        """Persist the current cookie jar to JSON for cross-agent reuse."""
        jar = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self._client.cookies.jar
        ]
        Path(path).write_text(json.dumps(jar, indent=2), encoding="utf-8")

    def load_cookies(self, path: str | Path) -> None:
        """Restore a cookie jar previously written by :meth:`save_cookies`."""
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            return
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            domain = entry.get("domain") or ""
            if isinstance(name, str) and isinstance(value, str) and name:
                self._client.cookies.set(name, value, domain=domain, path=entry.get("path") or "/")

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | str | None = None,
        json_body: Any = None,
        tag: str = "",
        timeout_ms: int | None = None,
    ) -> HTTPResponse:
        full_url = url if url.startswith(("http://", "https://")) else f"{self.base_url}{url}"
        host = httpx.URL(full_url).host.lower()
        seeded = self._seeded_headers.get(host)
        if seeded:
            # Per-call headers win over seeded ones so the agent can override.
            headers = {**seeded, **(headers or {})}
        req_body: bytes = b""
        if json_body is not None:
            req_body = json.dumps(json_body).encode("utf-8")
        elif isinstance(body, str):
            req_body = body.encode("utf-8")
        elif isinstance(body, (bytes, bytearray)):
            req_body = bytes(body)

        req = HTTPRequest(
            id=uuid.uuid4().hex[:12],
            method=method.upper(),
            url=full_url,
            headers=dict(headers or {}),
            body=req_body,
            timestamp=time.time(),
            tag=tag,
        )

        extra: dict[str, Any] = {}
        if timeout_ms is not None:
            extra["timeout"] = timeout_ms / 1000.0

        start = time.monotonic()
        r = await self._client.request(
            method=method.upper(),
            url=full_url,
            params=params,
            headers=headers,
            content=req_body if req_body else None,
            **extra,
        )
        try:
            elapsed = (time.monotonic() - start) * 1000.0
            resp = HTTPResponse(
                id=uuid.uuid4().hex[:12],
                request_id=req.id,
                status=r.status_code,
                headers=dict(r.headers),
                body=r.content,
                elapsed_ms=elapsed,
                timestamp=time.time(),
            )
            self.history.record(req, resp)
            return resp
        except BaseException:
            await r.aclose()
            raise

    async def get(self, url: str, **kwargs: Any) -> HTTPResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> HTTPResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> HTTPResponse:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> HTTPResponse:
        return await self.request("DELETE", url, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HTTPSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()


# ── Diff helpers ────────────────────────────────────────────────────────


def diff_responses(a: HTTPResponse, b: HTTPResponse, *, context: int = 3) -> str:
    """Produce a unified diff between two response bodies."""
    a_lines = a.body.decode("utf-8", errors="replace").splitlines()
    b_lines = b.body.decode("utf-8", errors="replace").splitlines()
    diff = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=f"{a.request_id} ({a.status})",
        tofile=f"{b.request_id} ({b.status})",
        lineterm="",
        n=context,
    )
    return "\n".join(diff)
