"""Typed, provider-neutral agent browser tools.

Each tool sends one :mod:`decepticon_sandbox.browser.contract` action to the
sandbox runner (``python3 -I -m decepticon_sandbox.browser call``) over the same
execution surface as the bash tool. The browser itself runs inside the
engagement sandbox, so the sandbox OpenShell network policy compiled from
``plan/roe.json`` enforces every destination it reaches (top-level
navigations, redirects, subresources and scripts alike); it is the only network
scope authority. All output is quarantined (``UNTRUSTED_TOOL_NAMES``).

The provider is selected with ``DECEPTICON_BROWSER_PROVIDER`` (default
``obscura``; ``none`` removes these tools). See
``docs/design/browser-providers.md``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shlex
from typing import Any, Literal

from decepticon_sandbox.browser.contract import BrowserRequest
from decepticon_sandbox.browser.provider import DEFAULT_PROVIDER
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from decepticon.tools.bash.bash import _workspace_path_from_config, get_sandbox
from decepticon.tools.browser.names import AGENT_BROWSER_TOOL_NAMES

PROVIDER_ENV = "DECEPTICON_BROWSER_PROVIDER"
#: The sandbox image registers its immutable runtime bundle on sys.path, so the
#: runner resolves without PYTHONPATH; ``-I`` keeps workspace files from
#: shadowing imports.
RUNNER_MODULE = "decepticon_sandbox.browser"
_RUNNER = f"python3 -I -m {RUNNER_MODULE}"
_BASE_TIMEOUT_S = 90


def selected_provider() -> str:
    return (os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()


def _last_json(output: str) -> dict[str, Any] | None:
    for line in reversed((output or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def _format(data: dict[str, Any]) -> str:
    head = f"[browser {data.get('action')} via {data.get('provider')}]"
    if not data.get("ok"):
        return f"{head} ERROR {data.get('error_code', 'error')}: {data.get('error', '')}"
    parts = [f"{head} OK"]
    if data.get("text"):
        parts.append(str(data["text"]))
    for art in data.get("artifacts") or []:
        parts.append(
            f"artifact: {art.get('path')} ({art.get('mime_type')}, {art.get('bytes')} bytes, "
            f"sha256={art.get('sha256')})"
        )
    return "\n".join(parts)


async def run_browser_action(
    action: str, args: dict[str, Any], config: RunnableConfig | None
) -> str:
    request = BrowserRequest(action=action, args={k: v for k, v in args.items() if v is not None})
    request.validate()
    sandbox = get_sandbox(config)
    if sandbox is None:
        return f"[browser {action}] ERROR no_sandbox: no sandbox available"
    workspace = _workspace_path_from_config(config)
    encoded = base64.urlsafe_b64encode(request.to_json().encode()).decode()
    cmd = " ".join(
        [
            _RUNNER,
            "call",
            "--workspace",
            shlex.quote(workspace),
            "--provider",
            shlex.quote(selected_provider()),
            "--request-b64",
            encoded,
        ]
    )
    timeout = _BASE_TIMEOUT_S + int(
        float(args.get("timeout_s") or 0) + float(args.get("timeout_ms") or 0) / 1000
    )
    try:
        result = await asyncio.to_thread(sandbox.execute, cmd, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - surface transport errors to the agent
        return f"[browser {action}] ERROR transport: {type(exc).__name__}: {exc}"
    data = _last_json(result.output)
    if data is None:
        tail = (result.output or "")[-2000:]
        return f"[browser {action}] ERROR runner: no result from sandbox runner\n{tail}"
    return _format(data)


# ── argument schemas ────────────────────────────────────────────────────


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class NavigateArgs(_Args):
    url: str = Field(description="Absolute in-scope http(s) URL")
    wait_until: Literal["load", "domcontentloaded", "networkidle0"] | None = None


class TextBudgetArgs(_Args):
    max_chars: int | None = Field(default=None, ge=0, le=40_000)


class LinksArgs(_Args):
    limit: int | None = Field(default=None, ge=1, le=1000)
    internal_only: bool | None = None


class SearchTextArgs(_Args):
    query: str
    case_sensitive: bool | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    context_chars: int | None = Field(default=None, ge=0, le=1000)


class TargetArgs(_Args):
    ref: str | None = Field(
        default=None, description="Element ref (e.g. 'e3') from browser_snapshot"
    )
    selector: str | None = Field(default=None, description="CSS selector when no ref is available")


class FillArgs(TargetArgs):
    value: str


class TypeTextArgs(TargetArgs):
    text: str


class PressArgs(_Args):
    key: str = Field(description="Key name, e.g. Enter, Tab, Escape")
    selector: str | None = None


class SelectArgs(_Args):
    selector: str
    value: str


class FormField(_Args):
    ref: str | None = None
    selector: str | None = None
    value: str
    type: Literal["text", "check", "uncheck", "select"] | None = None


class FillFormArgs(_Args):
    fields: list[FormField]
    submit_ref: str | None = None
    submit_selector: str | None = None


class ScrollArgs(_Args):
    direction: Literal["top", "bottom", "up", "down", "left", "right"] | None = None
    amount: int | None = Field(default=None, ge=1, le=100_000)
    ref: str | None = None
    selector: str | None = None


class WaitArgs(_Args):
    selector: str | None = None
    text: str | None = None
    function: str | None = Field(default=None, description="JS expression that becomes truthy")
    timeout_s: float | None = Field(default=None, gt=0, le=120)


class EvaluateArgs(_Args):
    expression: str
    timeout_ms: int | None = Field(default=None, ge=1, le=120_000)


class CookiesGetArgs(_Args):
    domain: str | None = None


class CookieSetArgs(_Args):
    name: str
    value: str
    domain: str
    path: str | None = None
    secure: bool | None = None
    http_only: bool | None = None


class StorageArgs(_Args):
    name: str = Field(default="default", pattern=r"^[A-Za-z0-9_-]{1,64}$")


class TabNewArgs(_Args):
    url: str | None = Field(default=None, description="Optional in-scope http(s) URL")


class TabIdArgs(_Args):
    tab_id: str


class TabCloseArgs(_Args):
    tab_id: str | None = None


class ScreenshotArgs(_Args):
    width: int | None = Field(default=None, ge=1, le=32768)
    height: int | None = Field(default=None, ge=1, le=32768)


class PdfArgs(_Args):
    landscape: bool | None = None
    print_background: bool | None = None


# (tool name, contract action, schema, description)
_SPECS: list[tuple[str, str, type[_Args], str]] = [
    (
        "browser_navigate",
        "navigate",
        NavigateArgs,
        "Open an in-scope URL in the engagement browser and wait for it to load.",
    ),
    ("browser_back", "back", NoArgs, "Go back in the active tab's history."),
    ("browser_forward", "forward", NoArgs, "Go forward in the active tab's history."),
    ("browser_reload", "reload", NoArgs, "Reload the active tab."),
    (
        "browser_snapshot",
        "snapshot",
        TextBudgetArgs,
        "Read URL, title, actionable element refs (e1, e2, ...), control states and visible text. Take a fresh snapshot before using refs.",
    ),
    ("browser_markdown", "markdown", TextBudgetArgs, "Read the current page as Markdown."),
    ("browser_links", "links", LinksArgs, "List anchor links on the current page."),
    ("browser_forms", "forms", NoArgs, "List forms with action, method and inputs."),
    (
        "browser_search_text",
        "search_text",
        SearchTextArgs,
        "Find text matches with surrounding context on the current page.",
    ),
    ("browser_click", "click", TargetArgs, "Click an element by ref (preferred) or CSS selector."),
    ("browser_fill", "fill", FillArgs, "Set an input's value by ref or selector."),
    (
        "browser_type_text",
        "type_text",
        TypeTextArgs,
        "Type text into an input (appends) by ref or selector.",
    ),
    ("browser_press", "press", PressArgs, "Dispatch a key press on an element or the document."),
    ("browser_select", "select", SelectArgs, "Choose an option of a <select> element."),
    (
        "browser_fill_form",
        "fill_form",
        FillFormArgs,
        "Fill several fields and optionally click a submit element.",
    ),
    ("browser_scroll", "scroll", ScrollArgs, "Scroll the page or bring an element into view."),
    ("browser_wait", "wait", WaitArgs, "Wait for a selector, a text, or a JS condition."),
    (
        "browser_evaluate",
        "evaluate",
        EvaluateArgs,
        "Evaluate JavaScript in the page and return the result.",
    ),
    (
        "browser_network_list",
        "network_list",
        NoArgs,
        "List network requests made by the current page.",
    ),
    ("browser_console_list", "console_list", NoArgs, "List console messages of the current page."),
    ("browser_cookies_get", "cookies_get", CookiesGetArgs, "List cookies in the browser jar."),
    ("browser_cookie_set", "cookie_set", CookieSetArgs, "Add or replace a cookie."),
    ("browser_cookies_clear", "cookies_clear", NoArgs, "Remove every cookie."),
    (
        "browser_storage_export",
        "storage_export",
        StorageArgs,
        "Save cookies + local/session storage to a private workspace file (returns the path, not the secrets).",
    ),
    (
        "browser_storage_import",
        "storage_import",
        StorageArgs,
        "Restore a session saved by browser_storage_export.",
    ),
    ("browser_tab_new", "tab_new", TabNewArgs, "Open a new tab, optionally at an in-scope URL."),
    ("browser_tab_list", "tab_list", NoArgs, "List open tabs."),
    ("browser_tab_switch", "tab_switch", TabIdArgs, "Make a tab active."),
    ("browser_tab_close", "tab_close", TabCloseArgs, "Close a tab (default: the active tab)."),
    (
        "browser_screenshot",
        "screenshot",
        ScreenshotArgs,
        "Capture the viewport as PNG evidence under <workspace>/evidence/browser/.",
    ),
    (
        "browser_pdf",
        "pdf",
        PdfArgs,
        "Export the page as PDF evidence under <workspace>/evidence/browser/.",
    ),
    ("browser_close", "close", NoArgs, "Close the current page and reset its state."),
    (
        "browser_capabilities",
        "capabilities",
        NoArgs,
        "List the actions the configured browser provider implements.",
    ),
]


def _make_tool(name: str, action: str, schema: type[_Args], description: str) -> BaseTool:
    async def _run(config: RunnableConfig, **kwargs: Any) -> str:
        payload = schema(**kwargs).model_dump(exclude_none=True)
        return await run_browser_action(action, payload, config)

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=description,
        args_schema=schema,
    )


AGENT_BROWSER_TOOLS: list[BaseTool] = [_make_tool(*spec) for spec in _SPECS]
if frozenset(t.name for t in AGENT_BROWSER_TOOLS) != AGENT_BROWSER_TOOL_NAMES:
    raise RuntimeError("agent browser tool specs and names.AGENT_BROWSER_TOOL_NAMES diverged")


def agent_browser_tools() -> list[BaseTool]:
    """Tools for the configured provider; empty when the provider is ``none``."""
    if selected_provider() == "none":
        return []
    return list(AGENT_BROWSER_TOOLS)
