"""CLI entrypoint for the open-web engine — invoked INSIDE the sandbox.

The agent tools (``web_search`` / ``web_fetch``) dispatch this over the bash
execution surface:

    python3 -m decepticon.sandbox_web fetch  <url>   [opts] --json
    python3 -m decepticon.sandbox_web search <query> [opts] --json

It builds the RoE ``scope_check`` from ``<workspace>/plan/roe.json`` (the same
file the sandbox nftables allowlist is compiled from), runs the engine, and
prints a JSON envelope to stdout. Large fetched content is offloaded to a
scratch file under the workspace and referenced by path so the bash channel
stays small.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

if not __package__:
    # Script mode from the immutable sandbox bundle (see tools/web/search.py):
    # managed OpenShell sessions inherit neither PYTHONPATH nor
    # DECEPTICON_SKIP_BOOT, and the lean bundle lacks the management-side
    # boot modules.
    os.environ.setdefault("DECEPTICON_SKIP_BOOT", "1")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decepticon.sandbox_web.fetch_chain import ScopeCheck, fetch
from decepticon.sandbox_web.providers import PROVIDERS, SearchResponse, is_allowed_provider

# Content larger than this is written to a scratch file instead of inlined.
_INLINE_CONTENT_LIMIT = 15_000


def _workspace() -> str | None:
    return os.environ.get("DECEPTICON_WORKSPACE_PATH")


def _build_scope_check(workspace: str | None) -> ScopeCheck | None:
    """Construct an RoE scope predicate from ``<workspace>/plan/roe.json``.

    Returns None when no workspace/roe is available (the sandbox nftables
    allowlist remains the authoritative gate in production).
    """
    if not workspace:
        return None
    roe_path = Path(workspace) / "plan" / "roe.json"
    if not roe_path.exists():
        return None
    try:
        from decepticon_core.types.roe import MachineEnforcement, evaluate_target
    except ImportError:
        return None
    try:
        data = json.loads(roe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    block = data.get("machine_enforcement") if isinstance(data, dict) else None
    rules = MachineEnforcement.from_dict(block)

    def _check(url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return evaluate_target(host, rules).allow

    return _check


def _configure_learning_env(workspace: str | None) -> None:
    """Point the engine's per-host route learning at the engagement workspace so
    it never writes to ``~/.insane_search`` in the sandbox. Disabled outright
    when there is no workspace (each fetch is a fresh subprocess; a learned store
    is only useful when it persists under the engagement)."""
    if os.environ.get("INSANE_LEARN") is not None or os.environ.get("INSANE_LEARNED_PATH"):
        return  # caller already configured it
    if workspace:
        os.environ["INSANE_LEARNED_PATH"] = str(Path(workspace) / ".scratch" / "web_learned.json")
    else:
        os.environ["INSANE_LEARN"] = "0"


def _offload_content(content: str, workspace: str | None, tag: str) -> str | None:
    """Write large content to <workspace>/.scratch and return the path, else None."""
    if not workspace:
        return None
    scratch = Path(workspace) / ".scratch"
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        path = scratch / f"web_{tag}.html"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except OSError:
        return None


def _cmd_fetch(args: argparse.Namespace) -> int:
    workspace = args.workspace or _workspace()
    scope_check = _build_scope_check(workspace)
    result = fetch(
        args.url,
        success_selectors=args.selector or None,
        device_class=args.device,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        scope_check=scope_check,
        enable_playwright=not args.no_playwright,
        enable_phase0=not args.no_phase0,
    )
    envelope = result.to_dict()
    content = result.content
    if len(content) > _INLINE_CONTENT_LIMIT:
        tag = str(abs(hash(args.url)))[:12]
        path = _offload_content(content, workspace, tag)
        envelope["content"] = content[:_INLINE_CONTENT_LIMIT]
        envelope["content_truncated"] = True
        envelope["content_path"] = path
    else:
        envelope["content"] = content
        envelope["content_truncated"] = False
    print(json.dumps(envelope, ensure_ascii=False))
    return 0 if result.ok else 1


def _cmd_search(args: argparse.Namespace) -> int:
    provider = args.provider
    if not is_allowed_provider(provider):
        resp = SearchResponse(
            provider=provider,
            query=args.query,
            error=f"provider {provider!r} not in allowlist {sorted(PROVIDERS)}",
        )
        print(json.dumps(resp.to_dict(), ensure_ascii=False))
        return 1

    spec = PROVIDERS[provider]
    query_url = spec["query_url"](args.query)
    # web_search is OSINT: the provider endpoint is allowlisted, so it is exempt
    # from per-target scope. The fetch still runs through the sandbox curl tier.
    result = fetch(
        query_url,
        device_class="desktop",
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        scope_check=None,
        enable_playwright=not args.no_playwright,
        enable_phase0=False,  # a search-provider URL is never a Phase-0 platform
    )
    resp = SearchResponse(provider=provider, query=args.query)
    if not result.ok:
        resp.error = f"provider fetch failed: {result.summary}"
        print(json.dumps(resp.to_dict(), ensure_ascii=False))
        return 1
    resp.hits = spec["parse"](result.content)
    print(json.dumps(resp.to_dict(), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Common options live on a parent parser so they are accepted AFTER the
    # subcommand (e.g. `fetch <url> --workspace ... --no-playwright`), which is
    # how the tool wrapper builds the command line.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=None, help="engagement workspace (else env)")
    common.add_argument("--timeout", type=int, default=25)
    common.add_argument("--max-attempts", type=int, default=12, dest="max_attempts")
    common.add_argument("--no-playwright", action="store_true")
    common.add_argument(
        "--no-phase0",
        action="store_true",
        help="skip the official public-API router (Reddit/X/YouTube)",
    )
    common.add_argument("--json", action="store_true", help="(default; reserved)")

    # Common opts live ONLY on the subparsers (not the main parser): putting
    # them on both makes the subparser's default clobber a value given before
    # the subcommand. So all common opts come AFTER the subcommand.
    parser = argparse.ArgumentParser(prog="decepticon.sandbox_web", description="open-web engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("fetch", help="fetch a URL", parents=[common])
    pf.add_argument("url")
    pf.add_argument("--selector", action="append", help="success CSS selector (repeatable)")
    pf.add_argument("--device", choices=["auto", "desktop", "mobile"], default="auto")
    pf.set_defaults(func=_cmd_fetch)

    ps = sub.add_parser(
        "search", help="keyword search via an allowlisted provider", parents=[common]
    )
    ps.add_argument("query")
    ps.add_argument("--provider", default="duckduckgo")
    ps.set_defaults(func=_cmd_search)

    args = parser.parse_args(argv)
    _configure_learning_env(args.workspace or _workspace())
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
