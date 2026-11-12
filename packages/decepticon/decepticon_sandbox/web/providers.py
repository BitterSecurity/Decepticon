"""web_search provider allowlist + result parsing.

``web_search`` is OSINT: it queries an **allowlisted** search provider (not an
arbitrary target), so it is exempt from per-target RoE scope — but the provider
set is closed (only providers in ``PROVIDERS`` may be queried) and every query
is audited upstream. This module owns the provider abstraction and the HTML
result parsing; the actual fetch reuses the engine's curl tier so it runs inside
the sandbox.

The DuckDuckGo HTML parser + redirect unwrapper are salvaged from PR #650.
No site-specific bias beyond the allowlisted provider endpoints themselves
(the agreed OSINT entry points, analogous to ADR-0010's Phase-0 API stance).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

from bs4 import BeautifulSoup, Tag


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResponse:
    provider: str
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "query": self.query,
            "hits": [h.to_dict() for h in self.hits],
            "error": self.error,
            "count": len(self.hits),
        }


# --- DuckDuckGo HTML provider -------------------------------------------------
# The provider endpoint/host literals below are the OSINT search allowlist, not
# target sites — the No-Site-Name rule's Phase-0 exemption (see bias_check.py).

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"  # NOTE-BIAS-OK provider allowlist
_MAX_HITS = 25

# DDG wraps result links as /l/?uddg=<encoded-target>. Unwrap to the real URL,
# verifying the redirect host is exactly DDG (salvaged from #650).
_DDG_REDIRECT_HOSTS = {"duckduckgo.com", "html.duckduckgo.com"}  # NOTE-BIAS-OK provider allowlist


def ddg_query_url(query: str) -> str:
    return f"{_DDG_ENDPOINT}?q={quote_plus(query)}"


def unwrap_ddg_href(href: str) -> str:
    """Return the real target URL from a DDG redirect wrapper, else ``href``.

    Only unwraps when the redirect host is exactly DuckDuckGo (an attacker
    cannot smuggle a lookalike host through the ``uddg`` param).
    """
    parts = urlsplit(href)
    host = (parts.hostname or "").lower()
    if host and host not in _DDG_REDIRECT_HOSTS:
        return href
    if parts.path.startswith("/l/") or "uddg=" in (parts.query or ""):
        uddg = parse_qs(parts.query).get("uddg", [])
        if uddg:
            return uddg[0]
    # Protocol-relative DDG redirect (//duckduckgo.com/l/?uddg=...)  # NOTE-BIAS-OK provider allowlist
    if href.startswith("//"):
        return unwrap_ddg_href("https:" + href)
    return href


def parse_ddg_html(body: str) -> list[SearchHit]:
    """Parse a DuckDuckGo HTML results page into ``SearchHit`` rows (bs4)."""
    soup = BeautifulSoup(body, "html.parser")
    hits: list[SearchHit] = []
    for link in soup.select("a.result__a"):
        if not isinstance(link, Tag):
            continue
        href = str(link.get("href") or "")
        url = unwrap_ddg_href(href)
        if not url.startswith(("http://", "https://")):
            continue
        title = link.get_text(" ", strip=True)
        snippet = ""
        # The snippet anchor lives in the same result container as the title.
        container = link.find_parent(class_="result") or link.parent
        if isinstance(container, Tag):
            snip = container.select_one("a.result__snippet, .result__snippet")
            if isinstance(snip, Tag):
                snippet = snip.get_text(" ", strip=True)
        if title or url:
            hits.append(SearchHit(title=title, url=url, snippet=snippet))
        if len(hits) >= _MAX_HITS:
            break
    return hits


# --- Provider registry (the allowlist) ---------------------------------------

PROVIDERS: dict[str, dict[str, Any]] = {
    "duckduckgo": {
        "query_url": ddg_query_url,
        "parse": parse_ddg_html,
    },
}

DEFAULT_PROVIDER = "duckduckgo"


def is_allowed_provider(name: str) -> bool:
    return name in PROVIDERS
