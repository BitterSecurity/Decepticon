"""Phase 0 — official public-API router (the SANCTIONED exception to No-Site-Name).

Per SKILL.md R5, platforms that publish official no-auth public endpoints get a
deterministic route tried BEFORE the generic WAF grid. This is the *enforced,
in-engine* version of what used to be agent-driven curl snippets in SKILL.md —
so the agent can no longer silently skip it (which is exactly how Reddit/X were
wrongly declared "blocked": the grid 403'd on `.json` and nobody tried `.rss`).

This file is the ONLY engine/ module allowed to name platform hosts; it is
exempted in `bias_check.EXPLICIT_ALLOW_FILES`. Do NOT add per-site logic to any
other engine file — generic WAF handling stays site-agnostic.

Contract:
    route(url) -> Optional[dict]
      None              → url is not a recognised Phase-0 platform; caller runs
                          the generic grid as usual.
      {"platform","ok","route","content","final_url","attempts":[...]}
                        → recognised platform. `ok` says whether an official
                          route succeeded. Even on ok=False the caller should
                          fall through to the grid, but `attempts` is recorded
                          so failure is never silent.

Each attempt dict: {"route","platform","ok","status","bytes","note"}.
"""

from __future__ import annotations

import re
import subprocess
from typing import Callable, Optional
from urllib.parse import urlsplit


class _PhaseBlocked(Exception):
    """Raised when a Phase-0 fetch URL is refused by SSRF safety or scope_check.
    Routers already catch Exception and record the note, so a block is surfaced
    as a failed attempt rather than crashing the router."""


# --- low-level helpers -------------------------------------------------------
def _cffi_get(
    url: str,
    *,
    impersonate: str = "safari",
    timeout: int = 15,
    scope_check: Optional[Callable[[str], bool]] = None,
):
    # Decepticon security adaptation: Phase-0 reaches THIRD-PARTY hosts
    # (syndication CDNs, oEmbed). Gate every fetch by RoE scope (when supplied)
    # and ALWAYS by SSRF safety so an attacker-influenced URL cannot pivot the
    # sandbox onto internal/metadata addresses.
    if scope_check is not None and not scope_check(url):
        raise _PhaseBlocked(f"roe_out_of_scope:{url}")
    from . import safety

    allowed, reason = safety.classify_url(url, allow_private=safety.allow_private_default())
    if not allowed:
        raise _PhaseBlocked(f"ssrf_blocked:{reason}")
    from curl_cffi import requests as r  # lazy: engine works even if missing

    return r.get(
        url,
        impersonate=impersonate,  # type: ignore[arg-type]
        timeout=timeout,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        },
        allow_redirects=True,
        http_version=safety.http_version_for(url),
    )


def _host(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h  # strip the literal "www." prefix only


def _attempt(platform: str, route: str, ok: bool, status: int, body: str, note: str = "") -> dict:
    return {
        "platform": platform,
        "route": route,
        "ok": ok,
        "status": status,
        "bytes": len(body or ""),
        "note": note,
    }


# --- platform detectors ------------------------------------------------------
def _detect(url: str) -> Optional[str]:
    # Match on the exact host or a real subdomain suffix — NEVER a substring.
    # `"reddit.com" in h` would also match a spoofed `reddit.com.evil.tld`
    # (CWE-020 incomplete URL substring sanitization).
    h = _host(url)
    if not h:
        return None
    if h == "reddit.com" or h.endswith(".reddit.com") or h == "redd.it":
        return "reddit"
    if h in ("x.com", "twitter.com") or h.endswith(".x.com") or h.endswith(".twitter.com"):
        return "x"
    if h == "youtube.com" or h.endswith(".youtube.com") or h == "youtu.be":
        return "youtube"
    if h == "github.com":
        return "github"
    if h == "npmjs.com":
        return "npm"
    if h == "pypi.org":
        return "pypi"
    return None


# --- reddit ------------------------------------------------------------------
def _reddit(url: str, timeout: int, scope_check=None) -> dict:
    attempts: list[dict] = []
    base = url.split("?", 1)[0].rstrip("/")
    # Build an .rss / .json target from the path (works for /r/<sub> and post URLs).
    rss_url = base + ("/.rss" if "/comments/" not in base else ".rss")
    json_url = base + ("/.json" if "/comments/" not in base else ".json")

    # Route 1: RSS (the route that actually survives — Reddit gates the JSON API).
    try:
        x = _cffi_get(rss_url, timeout=timeout, scope_check=scope_check)
        ok = x.status_code == 200 and ("<rss" in x.text or "<feed" in x.text)
        attempts.append(
            _attempt(
                "reddit", "rss", ok, x.status_code, x.text, "feed" if ok else "no-feed-markers"
            )
        )
        if ok:
            return {
                "platform": "reddit",
                "ok": True,
                "route": "rss",
                "content": x.text,
                "final_url": rss_url,
                "attempts": attempts,
            }
    except Exception as e:
        attempts.append(_attempt("reddit", "rss", False, 0, "", f"{type(e).__name__}"))

    # Route 2: JSON via curl_cffi (often 403 now, but try — cheap).
    try:
        x = _cffi_get(json_url, timeout=timeout, scope_check=scope_check)
        ok = x.status_code == 200 and x.text.lstrip().startswith(("{", "["))
        attempts.append(
            _attempt(
                "reddit",
                "json",
                ok,
                x.status_code,
                x.text,
                "json" if ok else f"status={x.status_code}",
            )
        )
        if ok:
            return {
                "platform": "reddit",
                "ok": True,
                "route": "json",
                "content": x.text,
                "final_url": json_url,
                "attempts": attempts,
            }
    except Exception as e:
        attempts.append(_attempt("reddit", "json", False, 0, "", f"{type(e).__name__}"))

    return {
        "platform": "reddit",
        "ok": False,
        "route": None,
        "content": "",
        "final_url": url,
        "attempts": attempts,
    }


# --- x / twitter -------------------------------------------------------------
_TWEET_ID_RE = re.compile(r"/status(?:es)?/(\d+)")


def _x(url: str, timeout: int, scope_check=None) -> dict:
    attempts: list[dict] = []
    m = _TWEET_ID_RE.search(url)

    if m:  # single tweet → tweet-result + oembed (both no-auth, reliable)
        tid = m.group(1)
        try:
            x = _cffi_get(
                f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=a",
                timeout=timeout,
                scope_check=scope_check,
            )
            d = x.json() if x.status_code == 200 else {}
            ok = bool(d.get("text"))
            attempts.append(
                _attempt(
                    "x",
                    "tweet-result",
                    ok,
                    x.status_code,
                    x.text,
                    "has-text" if ok else f"status={x.status_code}",
                )
            )
            if ok:
                return {
                    "platform": "x",
                    "ok": True,
                    "route": "tweet-result",
                    "content": x.text,
                    "final_url": url,
                    "attempts": attempts,
                }
        except Exception as e:
            attempts.append(_attempt("x", "tweet-result", False, 0, "", f"{type(e).__name__}"))
        try:
            ourl = f"https://publish.twitter.com/oembed?url=https://twitter.com/i/status/{tid}&omit_script=1"
            x = _cffi_get(ourl, timeout=timeout, scope_check=scope_check)
            d = x.json() if x.status_code == 200 else {}
            ok = bool(d.get("html"))
            attempts.append(
                _attempt(
                    "x",
                    "oembed",
                    ok,
                    x.status_code,
                    x.text,
                    "has-html" if ok else f"status={x.status_code}",
                )
            )
            if ok:
                return {
                    "platform": "x",
                    "ok": True,
                    "route": "oembed",
                    "content": x.text,
                    "final_url": ourl,
                    "attempts": attempts,
                }
        except Exception as e:
            attempts.append(_attempt("x", "oembed", False, 0, "", f"{type(e).__name__}"))
    else:  # profile timeline → syndication (rate-limit-prone; retry once)
        handle = urlsplit(url).path.strip("/").split("/")[0]
        _reserved = {
            "i",
            "search",
            "home",
            "explore",
            "messages",
            "notifications",
            "settings",
            "hashtag",
        }
        if handle and handle.lower() not in _reserved:
            surl = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
            for attempt_no in range(2):
                try:
                    x = _cffi_get(surl, timeout=timeout, scope_check=scope_check)
                    ok = x.status_code == 200 and "__NEXT_DATA__" in x.text
                    attempts.append(
                        _attempt(
                            "x",
                            f"syndication-timeline#{attempt_no + 1}",
                            ok,
                            x.status_code,
                            x.text,
                            "timeline" if ok else f"status={x.status_code}",
                        )
                    )
                    if ok:
                        return {
                            "platform": "x",
                            "ok": True,
                            "route": "syndication-timeline",
                            "content": x.text,
                            "final_url": surl,
                            "attempts": attempts,
                        }
                except Exception as e:
                    attempts.append(
                        _attempt(
                            "x",
                            f"syndication-timeline#{attempt_no + 1}",
                            False,
                            0,
                            "",
                            f"{type(e).__name__}",
                        )
                    )

    return {
        "platform": "x",
        "ok": False,
        "route": None,
        "content": "",
        "final_url": url,
        "attempts": attempts,
    }


# --- youtube -----------------------------------------------------------------
def _youtube(url: str, timeout: int, scope_check=None) -> dict:
    attempts: list[dict] = []
    # yt-dlp fetches the raw URL itself — gate it (RoE + SSRF) before handing the
    # attacker-influenceable URL to the subprocess.
    if scope_check is not None and not scope_check(url):
        attempts.append(_attempt("youtube", "yt-dlp", False, 0, "", "roe_out_of_scope"))
        return {
            "platform": "youtube",
            "ok": False,
            "route": None,
            "content": "",
            "final_url": url,
            "attempts": attempts,
        }
    from . import safety

    _allowed, _reason = safety.classify_url(url, allow_private=safety.allow_private_default())
    if not _allowed:
        attempts.append(_attempt("youtube", "yt-dlp", False, 0, "", f"ssrf_blocked:{_reason}"))
        return {
            "platform": "youtube",
            "ok": False,
            "route": None,
            "content": "",
            "final_url": url,
            "attempts": attempts,
        }
    try:
        p = subprocess.run(
            ["yt-dlp", "--dump-json", "--skip-download", url],
            capture_output=True,
            text=True,
            timeout=max(timeout, 60),
        )
        ok = p.returncode == 0 and p.stdout.strip().startswith("{")
        note = "json" if ok else (p.stderr or "").strip()[:80]
        attempts.append(_attempt("youtube", "yt-dlp", ok, 200 if ok else 0, p.stdout, note))
        if ok:
            return {
                "platform": "youtube",
                "ok": True,
                "route": "yt-dlp",
                "content": p.stdout,
                "final_url": url,
                "attempts": attempts,
            }
    except FileNotFoundError:
        attempts.append(_attempt("youtube", "yt-dlp", False, 0, "", "yt-dlp not installed"))
    except Exception as e:
        attempts.append(_attempt("youtube", "yt-dlp", False, 0, "", f"{type(e).__name__}"))
    return {
        "platform": "youtube",
        "ok": False,
        "route": None,
        "content": "",
        "final_url": url,
        "attempts": attempts,
    }


# --- github -----------------------------------------------------------------
# github.com/<owner>/<repo> first-segment reserved words (no repo API behind them).
_GH_RESERVED = {
    "orgs",
    "settings",
    "marketplace",
    "features",
    "about",
    "pricing",
    "login",
    "join",
    "sponsors",
    "topics",
    "search",
    "explore",
    "notifications",
    "new",
    "apps",
    "contact",
    "site",
    "collections",
}


def _github(url: str, timeout: int, scope_check=None) -> dict:
    attempts: list[dict] = []
    segs = [s for s in urlsplit(url).path.split("/") if s]
    # Only /<owner>/<repo> URLs have a repo API; a profile or /features falls
    # through to the grid.
    if len(segs) < 2 or segs[0].lower() in _GH_RESERVED:
        return {
            "platform": "github",
            "ok": False,
            "route": None,
            "content": "",
            "final_url": url,
            "attempts": attempts,
        }
    owner, repo = segs[0], segs[1].removesuffix(".git")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        x = _cffi_get(api, timeout=timeout, scope_check=scope_check)
        d = x.json() if x.status_code == 200 else {}
        ok = x.status_code == 200 and bool(d.get("full_name"))
        attempts.append(
            _attempt(
                "github",
                "repos-api",
                ok,
                x.status_code,
                x.text,
                "repo" if ok else f"status={x.status_code}",
            )
        )
        if ok:
            return {
                "platform": "github",
                "ok": True,
                "route": "repos-api",
                "content": x.text,
                "final_url": api,
                "attempts": attempts,
            }
    except Exception as e:
        attempts.append(_attempt("github", "repos-api", False, 0, "", f"{type(e).__name__}"))
    return {
        "platform": "github",
        "ok": False,
        "route": None,
        "content": "",
        "final_url": url,
        "attempts": attempts,
    }


# --- npm ---------------------------------------------------------------------
def _npm(url: str, timeout: int, scope_check=None) -> dict:
    attempts: list[dict] = []
    path = urlsplit(url).path
    marker = "/package/"
    if marker not in path:
        return {
            "platform": "npm",
            "ok": False,
            "route": None,
            "content": "",
            "final_url": url,
            "attempts": attempts,
        }
    # npmjs.com/package/<pkg>  (pkg may be scoped @scope/name); drop /v/<version>.
    pkg = path.split(marker, 1)[1].strip("/").split("/v/", 1)[0]
    if not pkg:
        return {
            "platform": "npm",
            "ok": False,
            "route": None,
            "content": "",
            "final_url": url,
            "attempts": attempts,
        }
    api = f"https://registry.npmjs.org/{pkg}"
    try:
        x = _cffi_get(api, timeout=timeout, scope_check=scope_check)
        d = x.json() if x.status_code == 200 else {}
        ok = x.status_code == 200 and bool(d.get("name"))
        attempts.append(
            _attempt(
                "npm",
                "registry",
                ok,
                x.status_code,
                x.text,
                "pkg" if ok else f"status={x.status_code}",
            )
        )
        if ok:
            return {
                "platform": "npm",
                "ok": True,
                "route": "registry",
                "content": x.text,
                "final_url": api,
                "attempts": attempts,
            }
    except Exception as e:
        attempts.append(_attempt("npm", "registry", False, 0, "", f"{type(e).__name__}"))
    return {
        "platform": "npm",
        "ok": False,
        "route": None,
        "content": "",
        "final_url": url,
        "attempts": attempts,
    }


# --- pypi --------------------------------------------------------------------
def _pypi(url: str, timeout: int, scope_check=None) -> dict:
    attempts: list[dict] = []
    segs = [s for s in urlsplit(url).path.split("/") if s]
    # pypi.org/project/<pkg>/
    if len(segs) < 2 or segs[0].lower() != "project":
        return {
            "platform": "pypi",
            "ok": False,
            "route": None,
            "content": "",
            "final_url": url,
            "attempts": attempts,
        }
    pkg = segs[1]
    api = f"https://pypi.org/pypi/{pkg}/json"
    try:
        x = _cffi_get(api, timeout=timeout, scope_check=scope_check)
        d = x.json() if x.status_code == 200 else {}
        ok = x.status_code == 200 and bool(d.get("info"))
        attempts.append(
            _attempt(
                "pypi",
                "json-api",
                ok,
                x.status_code,
                x.text,
                "pkg" if ok else f"status={x.status_code}",
            )
        )
        if ok:
            return {
                "platform": "pypi",
                "ok": True,
                "route": "json-api",
                "content": x.text,
                "final_url": api,
                "attempts": attempts,
            }
    except Exception as e:
        attempts.append(_attempt("pypi", "json-api", False, 0, "", f"{type(e).__name__}"))
    return {
        "platform": "pypi",
        "ok": False,
        "route": None,
        "content": "",
        "final_url": url,
        "attempts": attempts,
    }


_ROUTERS = {
    "reddit": _reddit,
    "x": _x,
    "youtube": _youtube,
    "github": _github,
    "npm": _npm,
    "pypi": _pypi,
}


# --- public entrypoint -------------------------------------------------------
def route(url: str, *, timeout: int = 15, scope_check=None) -> Optional[dict]:
    platform = _detect(url)
    if platform is None:
        return None
    return _ROUTERS[platform](url, timeout, scope_check=scope_check)
