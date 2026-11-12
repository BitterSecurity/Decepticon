"""Agent browser tool names (dependency-free, for middleware and agent build)."""

from __future__ import annotations

AGENT_BROWSER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "browser_navigate",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_snapshot",
        "browser_markdown",
        "browser_links",
        "browser_forms",
        "browser_search_text",
        "browser_click",
        "browser_fill",
        "browser_type_text",
        "browser_press",
        "browser_select",
        "browser_fill_form",
        "browser_scroll",
        "browser_wait",
        "browser_evaluate",
        "browser_network_list",
        "browser_console_list",
        "browser_cookies_get",
        "browser_cookie_set",
        "browser_cookies_clear",
        "browser_storage_export",
        "browser_storage_import",
        "browser_tab_new",
        "browser_tab_list",
        "browser_tab_switch",
        "browser_tab_close",
        "browser_screenshot",
        "browser_pdf",
        "browser_close",
        "browser_capabilities",
    }
)
