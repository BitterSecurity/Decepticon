"""Web research tools — general-purpose live web search for agents.

Provides tools for agents to search the web for security-relevant info:
CVE details, WAF bypasses, tool usage, exploit techniques, and documentation.
Distinct from exploit_intel (which generates dork queries) — this performs
actual live searches.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


# Security-tuned system prompt for search context
SECURITY_SEARCH_CONTEXT = (
    "You are a security researcher searching for: CVE details, exploit techniques, "
    "WAF/IDS bypass methods, vulnerability PoCs, tool documentation, security advisories, "
    "and attack methodology. Prioritize technical accuracy and version-specific information."
)


@tool
async def web_research(query: str, max_results: int = 5) -> str:
    """Search the web for security-relevant information.

    WHEN TO USE: When you need external knowledge during an engagement:
    CVE details, exploit techniques, WAF bypass methods, tool usage,
    or security documentation not in your training data.

    This uses available search providers (Exa, Perplexity, or DuckDuckGo).

    Args:
        query: Search query (be specific: include CVE IDs, tool names, versions)
        max_results: Maximum results to return (default 5)
    """
    # Try Exa first
    exa_key = os.environ.get("EXA_API_KEY")
    if exa_key:
        return await _search_exa(query, max_results, exa_key)

    # Try Perplexity
    pplx_key = os.environ.get("PERPLEXITY_API_KEY")
    if pplx_key:
        return await _search_perplexity(query, pplx_key)

    # Fallback: generate search URLs
    return _json(
        {
            "provider": "none",
            "note": "No search API key configured (EXA_API_KEY or PERPLEXITY_API_KEY)",
            "manual_urls": [
                f"https://www.google.com/search?q={query.replace(' ', '+')}",
                f"https://duckduckgo.com/?q={query.replace(' ', '+')}",
                f"https://cve.mitre.org/cgi-bin/cvekey.cgi?keyword={query.replace(' ', '+')}",
            ],
        }
    )


async def _search_exa(query: str, max_results: int, api_key: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.exa.ai/search",
                json={
                    "query": query,
                    "numResults": min(max_results, 10),
                    "type": "auto",
                    "useAutoprompt": True,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for r in data.get("results", []):
                    results.append(
                        {
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("text", r.get("highlights", [""]))[0]
                            if isinstance(r.get("text", r.get("highlights")), list)
                            else r.get("text", "")[:500],
                            "published": r.get("publishedDate", ""),
                        }
                    )
                return _json({"provider": "exa", "query": query, "results": results})
            return _json({"error": f"Exa API returned {resp.status_code}", "body": resp.text[:200]})
    except Exception as e:
        return _json({"error": f"Exa search failed: {e}"})


async def _search_perplexity(query: str, api_key: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                json={
                    "model": "sonar",
                    "messages": [
                        {"role": "system", "content": SECURITY_SEARCH_CONTEXT},
                        {"role": "user", "content": query},
                    ],
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                citations = data.get("citations", [])
                return _json(
                    {
                        "provider": "perplexity",
                        "query": query,
                        "answer": content,
                        "citations": citations,
                    }
                )
            return _json({"error": f"Perplexity API returned {resp.status_code}"})
    except Exception as e:
        return _json({"error": f"Perplexity search failed: {e}"})


@tool
async def web_get_contents(urls_json: str) -> str:
    """Fetch and extract text content from up to 10 URLs.

    WHEN TO USE: After web_research returns URLs, fetch the full page
    content for detailed analysis.

    Args:
        urls_json: JSON array of URLs to fetch (max 10)
    """
    try:
        urls = json.loads(urls_json)
    except json.JSONDecodeError:
        return _json({"error": "invalid urls_json"})

    if not isinstance(urls, list):
        urls = [urls]
    urls = urls[:10]

    # Try Exa contents API first
    exa_key = os.environ.get("EXA_API_KEY")
    if exa_key:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.exa.ai/contents",
                    json={"ids": urls, "text": True},
                    headers={"Authorization": f"Bearer {exa_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pages = []
                    for r in data.get("results", []):
                        pages.append(
                            {
                                "url": r.get("url", ""),
                                "title": r.get("title", ""),
                                "text": r.get("text", "")[:5000],
                            }
                        )
                    return _json({"pages": pages, "fetched": len(pages)})
        except Exception:
            pass

    # Fallback: direct fetch
    pages = []
    failed = []
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                text = resp.text[:5000]
                pages.append({"url": url, "status": resp.status_code, "text": text})
            except Exception as e:
                failed.append({"url": url, "error": str(e)})

    return _json({"pages": pages, "fetched": len(pages), "failed": failed})


WEB_RESEARCH_TOOLS = [web_research, web_get_contents]
