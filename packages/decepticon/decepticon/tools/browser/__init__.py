"""Agent browser tools.

* ``AGENT_BROWSER_TOOLS`` (``agent_tools``): the bound, typed ``browser_*``
  tools. They run the configured provider (``DECEPTICON_BROWSER_PROVIDER``,
  default ``obscura``) inside the engagement sandbox through
  ``decepticon.sandbox_browser``. See ``docs/design/browser-providers.md``.
* ``browser_action`` (``tools``): the legacy multiplex Playwright /
  owned-browser-v1 tool, kept for library consumers. It is not bound to any
  standard agent.
"""

from decepticon.tools.browser.agent_tools import AGENT_BROWSER_TOOLS, agent_browser_tools
from decepticon.tools.browser.names import AGENT_BROWSER_TOOL_NAMES
from decepticon.tools.browser.tools import (
    BROWSER_TOOLS,
    BrowserActionError,
    BrowserSessionManager,
    browser_action,
)

__all__ = [
    "AGENT_BROWSER_TOOLS",
    "AGENT_BROWSER_TOOL_NAMES",
    "agent_browser_tools",
    "BROWSER_TOOLS",
    "BrowserActionError",
    "BrowserSessionManager",
    "browser_action",
]
