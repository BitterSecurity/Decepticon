"""``web_search`` / ``web_fetch`` agent tools — management-side wrappers.

These are thin wrappers: they dispatch the open-web engine
(``decepticon.sandbox_web``) **inside the sandbox** over the existing execution
surface (``HTTPSandbox.execute``), so every byte of egress happens in
``sandbox-net`` behind the RoE-compiled nftables allowlist. No new transport,
no side channel — same pattern as the bash tool reaching the sandbox.

RoE enforcement is layered:
  1. ``RoEGuardrailMiddleware`` gates these by name (``GATED_TOOL_NAMES`` +
     ``NETWORK_TARGET_EXTRACTORS``) on the management side, fail-closed —
     ``web_fetch`` is target-gated on its ``url``; ``web_search`` is OSINT
     (allowlisted provider) so it is audited but target-exempt.
  2. The engine re-checks every transformed/redirected hop against
     ``<workspace>/plan/roe.json`` (defense-in-depth).
  3. The sandbox nftables/DNS allowlist is the authoritative backstop.

Tool output is quarantined via ``UNTRUSTED_TOOL_NAMES`` (open-web content is
attacker-influenceable). See ADR-0010.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from decepticon.tools.bash.bash import _workspace_path_from_config, get_sandbox

# Engine invocation. ``python3 -m decepticon.sandbox_web`` runs inside the
# sandbox image (which mounts decepticon + carries curl_cffi); the verb/args
# follow decepticon/sandbox_web/__main__.py.
_ENGINE = "python3 -m decepticon.sandbox_web"
_FETCH_TIMEOUT = 90
_SEARCH_TIMEOUT = 60


def _extract_json(output: str) -> dict[str, Any] | None:
    """Pull the engine's JSON envelope out of combined stdout/stderr.

    The engine prints exactly one JSON object (last line); importing the parent
    package emits log noise around it, so scan from the end for the first line
    that parses as a JSON object.
    """
    if not output:
        return None
    text = output.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass  # not a bare JSON blob (log noise around it) — fall through to the line scan
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


async def _dispatch(cmd: str, *, timeout: int) -> tuple[dict[str, Any] | None, str]:
    """Run an engine command in the sandbox. Returns (parsed_json, raw_output)."""
    sandbox = get_sandbox()
    if sandbox is None:
        return None, "[ERROR] no sandbox available"
    try:
        result = await asyncio.to_thread(sandbox.execute, cmd, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - surface transport errors to the agent
        return None, f"[ERROR] sandbox execute failed: {type(exc).__name__}: {exc}"
    return _extract_json(result.output), result.output


def _truncate(text: str, limit: int = 12_000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]"


@tool
async def web_fetch(
    url: str,
    selector: str = "",
    device: str = "auto",
    config: RunnableConfig | None = None,
) -> str:
    """Fetch a URL's content, escalating past WAF/anti-bot defenses if needed.

    Runs the sandbox-side open-web engine: a curl-impersonation grid that only
    declares success after a 4-layer validation (an HTTP 200 challenge page is
    NOT success), falling back to a headless browser on JS challenges. RoE-gated
    on the target host, fail-closed.

    Args:
        url: Absolute http(s) URL to fetch (must be in engagement scope).
        selector: Optional CSS selector that proves the real content loaded
            (e.g. "article", "#main"). Strongly recommended — without it a 200
            with a tiny/challenge body is treated as suspect.
        device: "auto" | "desktop" | "mobile".
    """
    workspace = _workspace_path_from_config(config)
    parts = [_ENGINE, "fetch", shlex.quote(url), "--workspace", shlex.quote(workspace)]
    if selector:
        parts += ["--selector", shlex.quote(selector)]
    if device in ("desktop", "mobile"):
        parts += ["--device", device]
    data, raw = await _dispatch(" ".join(parts), timeout=_FETCH_TIMEOUT)
    if data is None:
        return raw or "[ERROR] web_fetch: no parseable result from engine"

    verdict = data.get("verdict", "unknown")
    if not data.get("ok"):
        # Surface the engine's failure gate so the agent applies the
        # "a give-up is not exhaustion" discipline (see the open-web skill):
        # only a TERMINAL stop_reason (auth_required / not_found / paywall) is a
        # real wall — everything else (challenge, blocked, rate_limited, a
        # non-exhausted grid) still has escalation left. The local browser tier
        # has already run inside the sandbox, so the next move is the agent's
        # own retry, not an out-of-band MCP browser.
        stop = data.get("stop_reason", "")
        exhausted = data.get("grid_exhausted", False)
        msg = [
            f"[web_fetch FAILED] verdict={verdict} url={data.get('final_url', url)} "
            f"stop_reason={stop} grid_exhausted={exhausted}",
            data.get("summary", ""),
        ]
        if stop == "roe_refused":
            msg.append("RoE refused this target. Check the signed engagement scope before continuing.")
        elif stop in ("auth_required", "not_found"):
            msg.append(
                "TERMINAL (login/paywall/404) — record it and move on; retrying will not help."
            )
        else:
            msg.append(
                "NOT a terminal wall — escalation remains. Retry with "
                'device="mobile" and/or a success `selector`; on rate-limit back '
                "off then retry. Do NOT treat the page as unreadable until "
                "stop_reason is terminal."
            )
        return "\n".join(s for s in msg if s)
    content = data.get("content", "")
    note = ""
    if data.get("content_truncated"):
        note = f"\n[full content offloaded to {data.get('content_path')}]"
    return (
        f"[web_fetch OK] verdict={verdict} final_url={data.get('final_url', url)} "
        f"bytes={data.get('content_length', len(content))}\n\n"
        f"{_truncate(content)}{note}"
    )


@tool
async def web_search(
    query: str,
    provider: str = "duckduckgo",
    config: RunnableConfig | None = None,
) -> str:
    """Keyword web search via an allowlisted provider (OSINT).

    Returns ranked result URLs + titles + snippets. The provider set is closed
    (allowlisted); the query is audited. Use this to discover URLs, then
    ``web_fetch`` to read a specific in-scope page.

    Args:
        query: Search keywords.
        provider: Allowlisted provider id (default "duckduckgo").
    """
    workspace = _workspace_path_from_config(config)
    parts = [
        _ENGINE,
        "search",
        shlex.quote(query),
        "--provider",
        shlex.quote(provider),
        "--workspace",
        shlex.quote(workspace),
    ]
    data, raw = await _dispatch(" ".join(parts), timeout=_SEARCH_TIMEOUT)
    if data is None:
        return raw or "[ERROR] web_search: no parseable result from engine"
    if data.get("error"):
        return f"[web_search FAILED] provider={data.get('provider', provider)}: {data['error']}"
    hits = data.get("hits", [])
    if not hits:
        return f"[web_search] provider={data.get('provider', provider)} — no results for {query!r}"
    lines = [f"[web_search] {data.get('count', len(hits))} results for {query!r}:"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h.get('title', '')}\n   {h.get('url', '')}")
        snippet = h.get("snippet", "")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


OPEN_WEB_TOOLS = [web_search, web_fetch]
