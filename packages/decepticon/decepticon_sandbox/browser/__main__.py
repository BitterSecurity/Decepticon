"""CLI entry for the sandbox browser runner: ``python3 -I -m decepticon_sandbox.browser``.

    call --workspace W --provider P --request-b64 B   run one action
    shutdown --workspace W --provider P               stop the workspace browser
    providers                                         list installed providers

Always prints exactly one JSON object on the last stdout line.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path

from decepticon_sandbox.browser.contract import BrowserContractError, BrowserRequest, BrowserResult
from decepticon_sandbox.browser.provider import (
    DEFAULT_PROVIDER,
    UnknownProviderError,
    available_providers,
    load_provider,
)


def _workspace(raw: str) -> Path:
    # Managed OpenShell sessions bind the engagement at /workspace itself;
    # shared HTTP sandboxes use /workspace/<slug>.
    path = Path(raw).resolve()
    root = Path("/workspace")
    if path != root and root not in path.parents:
        raise BrowserContractError("workspace must be /workspace or a directory under it")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="decepticon_sandbox.browser")
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("call")
    call.add_argument("--workspace", required=True)
    call.add_argument("--provider", default=DEFAULT_PROVIDER)
    call.add_argument("--request-b64", required=True)
    stop = sub.add_parser("shutdown")
    stop.add_argument("--workspace", required=True)
    stop.add_argument("--provider", default=DEFAULT_PROVIDER)
    sub.add_parser("providers")
    args = parser.parse_args(argv)

    if args.command == "providers":
        print(json.dumps({"ok": True, "providers": available_providers()}))
        return 0

    action = "shutdown"
    try:
        workspace = _workspace(args.workspace)
        provider = load_provider(args.provider, workspace)
        if args.command == "shutdown":
            provider.shutdown()
            result = BrowserResult(ok=True, action=action, provider=provider.name)
        else:
            try:
                raw = base64.urlsafe_b64decode(args.request_b64.encode())
            except (binascii.Error, ValueError) as exc:
                raise BrowserContractError(f"request is not base64url: {exc}") from exc
            request = BrowserRequest.from_json(raw)
            action = request.action
            if action != "capabilities" and action not in provider.capabilities():
                result = BrowserResult.failure(
                    action,
                    provider.name,
                    "unsupported_action",
                    f"provider {provider.name!r} does not implement {action!r}",
                )
            else:
                result = provider.invoke(request)
    except BrowserContractError as exc:
        result = BrowserResult.failure(action, args.provider, "invalid_request", str(exc))
    except (UnknownProviderError, TypeError) as exc:
        result = BrowserResult.failure(action, args.provider, "provider_unavailable", str(exc))
    print(result.to_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
