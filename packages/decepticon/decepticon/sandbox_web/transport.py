"""Per-host curl_cffi Session pool + root warmup + browser→curl cookie bridge.

Why (multi-AI review 2026-06-21):
  * v1 issued a brand-new `curl_cffi.requests.get()` per attempt, so cookies
    set by a WAF (e.g. an Akamai `_abck` sensor or a CF `cf_clearance`) and
    the warm TLS/connection were thrown away between attempts and between
    pages of the same host. That caps both success rate (sensor cookies never
    mature) and throughput (handshake per request).
  * A browser fallback that punches through a JS challenge produces exactly the
    cookies + User-Agent a plain HTTP client needs — but v1 discarded them
    (`_FakeResp` kept only HTML). The bridge here lets one expensive browser
    pass convert into cheap curl_cffi throughput (the FlareSolverr pattern).

No-Site-Name Rule: keys are hashed hosts; no site names are stored or branched.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "unknown").lower()


def _root_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


@dataclass
class _Entry:
    session: Any
    warmed: bool = False
    injected_ua: Optional[str] = None
    requests_made: int = 0


@dataclass
class SessionPool:
    """Thread-safe pool of curl_cffi Sessions keyed by (host, impersonate)."""

    _entries: dict = field(default_factory=dict)
    _lock: Any = field(default_factory=threading.Lock)

    def _key(self, host: str, impersonate: str) -> tuple:
        return (host, impersonate)

    def get(self, host: str, impersonate: str) -> Optional[_Entry]:
        """Return (creating if needed) the pool entry, or None if curl_cffi
        is unavailable."""
        key = self._key(host, impersonate)
        with self._lock:
            ent = self._entries.get(key)
            if ent is not None:
                return ent
            try:
                from curl_cffi import requests as cffi_requests
            except ImportError:
                return None
            try:
                sess = cffi_requests.Session(impersonate=impersonate)
            except Exception:
                # Some impersonate names need a newer curl_cffi; let caller
                # fall back to a one-shot get by returning None.
                return None
            ent = _Entry(session=sess)
            self._entries[key] = ent
            return ent

    def warmup(self, host: str, impersonate: str, root_url: str, timeout: int = 15) -> bool:
        """Hit the site root once per (host, impersonate) so a WAF sensor can
        set a resolved session cookie before the real (deep) request. Idempotent."""
        ent = self.get(host, impersonate)
        if ent is None or ent.warmed:
            return False
        from . import safety

        ok, _reason = safety.classify_url(root_url, safety.allow_private_default())
        if not ok:
            ent.warmed = True  # don't retry a blocked root
            return False
        ent.warmed = True  # mark first to avoid duplicate warmups under race
        try:
            ent.session.get(
                root_url,
                timeout=timeout,
                allow_redirects=True,
                http_version=safety.http_version_for(root_url),
            )
            ent.requests_made += 1
            return True
        except Exception:
            return False

    def inject_cookies(
        self, host: str, impersonate: str, cookies: list[dict], user_agent: Optional[str] = None
    ) -> bool:
        """Seed a session with cookies harvested by a real browser. Subsequent
        requests on this (host, impersonate) reuse the browser-cleared state."""
        ent = self.get(host, impersonate)
        if ent is None:
            return False
        ok = False
        for c in cookies or []:
            name = c.get("name")
            value = c.get("value")
            if not name:
                continue
            try:
                ent.session.cookies.set(name, value, domain=c.get("domain") or host)
                ok = True
            except Exception:
                try:
                    ent.session.cookies.set(name, value)
                    ok = True
                except Exception:
                    continue
        if user_agent:
            ent.injected_ua = user_agent
        return ok

    def request(
        self,
        url: str,
        *,
        impersonate: str,
        referer: str = "",
        timeout: int = 25,
        extra_headers: Optional[dict] = None,
        allow_private: Optional[bool] = None,
        max_redirects: Optional[int] = None,
    ) -> tuple[Any, Optional[str]]:
        """GET via the pooled session (cookie + connection reuse), with an SSRF
        guard: the initial URL and EVERY redirect hop are validated against the
        private/loopback/link-local/metadata block-list before being fetched.
        Falls back to a one-shot get if no session could be created."""
        from . import safety

        if allow_private is None:
            allow_private = safety.allow_private_default()
        if max_redirects is None:
            max_redirects = safety.DEFAULT_MAX_REDIRECTS

        ok, reason = safety.classify_url(url, allow_private)
        if not ok:
            return None, f"ssrf_blocked:{reason}"

        host = _host_of(url)
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)

        ent = self.get(host, impersonate)
        if ent is None:
            try:
                from curl_cffi import requests as cffi_requests
            except ImportError:
                return None, "curl_cffi not installed"

            def _oneshot_get(u):
                return cffi_requests.get(
                    u,
                    impersonate=impersonate,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                    http_version=safety.http_version_for(u),
                )

            return self._fetch_following(_oneshot_get, url, allow_private, max_redirects, None)

        if ent.injected_ua:
            headers.setdefault("User-Agent", ent.injected_ua)

        def _do_get(u):
            return ent.session.get(
                u,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                http_version=safety.http_version_for(u),
            )

        return self._fetch_following(_do_get, url, allow_private, max_redirects, ent)

    @staticmethod
    def _fetch_following(
        do_get, url: str, allow_private: bool, max_redirects: int, ent
    ) -> tuple[Any, Optional[str]]:
        """Manually follow redirects so each hop is SSRF-validated (curl_cffi's
        own allow_redirects=True would skip the per-hop check)."""
        from . import safety

        cur = url
        for _ in range(max_redirects + 1):
            try:
                resp = do_get(cur)
            except Exception as e:
                return None, f"{type(e).__name__}:{str(e)[:200]}"
            if ent is not None:
                ent.requests_made += 1
            if safety.is_redirect(resp):
                loc = safety.location_of(resp)
                if not loc:
                    return resp, None  # redirect w/o Location → return as-is
                nxt = safety.resolve_redirect(cur, loc)
                ok, reason = safety.classify_url(nxt, allow_private)
                if not ok:
                    return None, f"ssrf_redirect_blocked:{reason}"
                cur = nxt
                continue
            return resp, None
        return None, "too_many_redirects"

    def stats(self) -> dict:
        with self._lock:
            return {
                "sessions": len(self._entries),
                "warmed": sum(1 for e in self._entries.values() if e.warmed),
                "requests": sum(e.requests_made for e in self._entries.values()),
            }

    def reset(self) -> None:
        with self._lock:
            for e in self._entries.values():
                try:
                    e.session.close()
                except Exception:
                    pass
            self._entries.clear()


# Process-wide pool. Disable via INSANE_NO_SESSION_POOL=1 (one-shot mode).
POOL = SessionPool()


def pool_enabled() -> bool:
    return os.environ.get("INSANE_NO_SESSION_POOL", "") not in ("1", "true", "yes")
