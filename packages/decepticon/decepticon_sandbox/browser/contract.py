"""Provider-neutral browser action contract.

Shared by the management-side agent tools (``decepticon.tools.browser``) and
the sandbox-side runner (``python3 -m decepticon_sandbox.browser``). Standard
library only: this module is copied into the sandbox image.

A provider adapter maps these actions onto its own native interface (Obscura
MCP, a CLI such as agent-browser, ...). Providers declare the subset they
implement; an undeclared action fails with ``unsupported_action`` instead of
being emulated.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

#: Every action the agent tool surface can request.
ACTIONS: frozenset[str] = frozenset(
    {
        "navigate",
        "back",
        "forward",
        "reload",
        "snapshot",
        "markdown",
        "links",
        "forms",
        "search_text",
        "click",
        "fill",
        "type_text",
        "press",
        "select",
        "fill_form",
        "scroll",
        "wait",
        "evaluate",
        "network_list",
        "console_list",
        "cookies_get",
        "cookie_set",
        "cookies_clear",
        "storage_export",
        "storage_import",
        "tab_new",
        "tab_list",
        "tab_switch",
        "tab_close",
        "screenshot",
        "pdf",
        "close",
        "capabilities",
    }
)

#: Actions whose ``url`` argument names a network destination.
URL_ACTIONS: frozenset[str] = frozenset({"navigate", "tab_new"})

#: Upper bound for one request document (argv-safe after base64).
MAX_REQUEST_BYTES = 256 * 1024
#: Upper bound for text returned to the agent per action.
MAX_TEXT_CHARS = 40_000


class BrowserContractError(ValueError):
    """Invalid request shape; reported as ``invalid_request``."""


@dataclass(frozen=True)
class BrowserRequest:
    action: str
    args: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> BrowserRequest:
        if self.action not in ACTIONS:
            raise BrowserContractError(f"unknown action: {self.action!r}")
        if not isinstance(self.args, dict):
            raise BrowserContractError("args must be an object")
        if self.action in URL_ACTIONS and "url" in self.args:
            url = self.args["url"]
            if url is not None and (
                not isinstance(url, str) or not url.lower().startswith(("http://", "https://"))
            ):
                raise BrowserContractError("url must be an absolute http(s) URL")
        return self

    def to_json(self) -> str:
        return json.dumps({"action": self.action, "args": self.args}, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes) -> BrowserRequest:
        if len(raw) > MAX_REQUEST_BYTES:
            raise BrowserContractError("request too large")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise BrowserContractError(f"request is not JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("action"), str):
            raise BrowserContractError("request must be an object with an action")
        args = data.get("args")
        if args is None:
            args = {}
        return cls(action=data["action"], args=args).validate()


@dataclass(frozen=True)
class Artifact:
    """A file the action wrote under the engagement workspace."""

    kind: str
    path: str
    mime_type: str
    sha256: str
    bytes: int


@dataclass
class BrowserResult:
    ok: bool
    action: str
    provider: str
    text: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    error_code: str = ""
    error: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        if len(data["text"]) > MAX_TEXT_CHARS:
            data["text"] = data["text"][:MAX_TEXT_CHARS] + "\n…[truncated]"
            data["truncated"] = True
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def failure(cls, action: str, provider: str, code: str, message: str) -> BrowserResult:
        return cls(ok=False, action=action, provider=provider, error_code=code, error=message)
