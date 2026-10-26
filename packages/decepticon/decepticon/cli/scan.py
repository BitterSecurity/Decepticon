"""``decepticon-cli scan`` — headless DAST entry point.

Modeled after Strix's CLI for surface familiarity, with Decepticon's
engagement / OPPLAN / RoE discipline preserved:

  decepticon-cli scan --target https://app.example.com --scan-mode standard

Modes
-----
- ``standard`` — full scan with a bounded wall clock.
- ``deep`` — long-running; includes dynamic analysis where supported.

Targets are network assets (HTTP URLs, git remotes, hosts, CIDRs, globs).
The standalone SAST / CI code-scanning entry (diff-scoped ``quick`` mode,
SARIF output, severity gating and the GitHub Actions composite) was removed
— see issue #1059. Code analysis inside an engagement — analyzing source the
operation itself obtains — is unchanged.

Outputs
-------
- A line-buffered JSONL of run events to stdout when ``--non-interactive``
  is set (so logs stay useful in headless environments).
- Exit code: 0 = run dispatched and completed; 2 = config / invocation
  error; 3 = scan internal error. Findings are read from the engagement
  (``decepticon_engagement_findings`` over MCP, or the workspace graph).

The actual scan is driven by the existing ``decepticon`` orchestrator via
the LangGraph SDK. This module is a thin shell: argument parsing, target
validation, one-shot run dispatch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from decepticon.roe_materialization import materialize_engagement_roe

log = logging.getLogger(__name__)


def _bluecell():
    """Resolve the blue engagement-configuration module (optional dependency).

    The module moved to the AutoBot package (migration stage 2c). Without it
    installed the blue engagement surfaces fail with a clean install hint
    instead of breaking the CLI import — Blue observation is optional.
    """
    try:
        from autobot.bluecell import engagement_config
    except ImportError as exc:  # pragma: no cover - install-shape dependent
        raise RuntimeError(
            "Blue engagement configuration requires the AutoBot package. "
            "Install it with: pip install decepticon[blue]  (or install "
            "autobot from the BitterSecurity/AutoBot repository)"
        ) from exc
    return engagement_config


EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_CONFIG = 2
EXIT_INTERNAL = 3


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decepticon-cli scan",
        description="Run a headless Decepticon DAST engagement.",
    )
    p.add_argument(
        "--target",
        "-t",
        action="append",
        required=False,
        default=[],
        help="Network target (HTTP URL, git URL, host, CIDR, or glob). Repeatable.",
    )
    p.add_argument(
        "--scan-mode",
        choices=["standard", "deep"],
        default="standard",
        help="Scan depth/timeout profile.",
    )
    p.add_argument(
        "--instruction",
        default="",
        help="Free-form scope or focus instruction passed to the orchestrator.",
    )
    p.add_argument(
        "--instruction-file",
        type=Path,
        default=None,
        help="Path to a file containing rules of engagement / scope notes.",
    )
    p.add_argument(
        "--non-interactive",
        "-n",
        action="store_true",
        help="Disable interactive UI; print JSONL events to stdout instead.",
    )
    p.add_argument(
        "--langgraph-url",
        default=os.environ.get("DECEPTICON_API_URL", "http://localhost:2024"),
        help="LangGraph platform API URL (default: $DECEPTICON_API_URL).",
    )
    p.add_argument(
        "--assistant",
        default="decepticon",
        help="LangGraph assistant graph name (default: decepticon).",
    )
    p.add_argument(
        "--engagement-name",
        default=None,
        help="Override engagement slug; defaults to a timestamped scan name.",
    )
    p.add_argument(
        "--out-of-scope",
        action="append",
        default=[],
        help=(
            "Out-of-scope entry (host, CIDR, glob, or URL) the scan must never "
            "touch; takes precedence over any --target. Repeatable or "
            "comma-separated."
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-scan wall-clock timeout in seconds; mode default if unset.",
    )
    p.add_argument(
        "--blue-target",
        default=None,
        help="Optionally materialize a Blue engagement config for this local upstream (127.0.0.1:port).",
    )
    p.add_argument(
        "--blue-logs",
        default=None,
        help="Absolute host log directory for the Blue sensor (requires --blue-target).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging to stderr.",
    )
    return p


_MODE_DEFAULTS = {
    "standard": {"timeout": 3600, "reasoning_effort": "high"},
    "deep": {"timeout": 14400, "reasoning_effort": "high"},
}


def _validate_targets(targets: list[str]) -> str | None:
    if not targets:
        return "at least one --target is required"
    for t in targets:
        if t.startswith(("http://", "https://", "git@", "git+", "ssh://")):
            continue
        if Path(t).exists():
            return (
                f"--target {t!r} looks like a filesystem path; the standalone "
                "code-scan entry was removed (issue #1059) — Decepticon "
                "engagements scope network assets, and in-engagement code "
                "analysis happens on source the operation obtains"
            )
    return None


def _resolve_engagement_name(supplied: str | None) -> str:
    if supplied:
        return supplied
    import datetime  # noqa: PLC0415

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"scan-{stamp}"


def _emit_jsonl_event(event: dict[str, Any]) -> None:
    print(json.dumps(event, default=str, ensure_ascii=False), flush=True)


def _instruction_text(args: argparse.Namespace) -> str:
    pieces: list[str] = []
    if args.instruction:
        pieces.append(args.instruction.strip())
    if args.instruction_file:
        try:
            pieces.append(args.instruction_file.read_text(encoding="utf-8").strip())
        except OSError as exc:
            raise RuntimeError(
                f"failed to read --instruction-file {args.instruction_file}: {exc}"
            ) from exc
    return "\n\n".join(pieces)


def _dispatch_scan_via_sdk(
    *,
    langgraph_url: str,
    assistant: str,
    engagement_name: str,
    targets: list[str],
    instruction: str,
    scan_mode: str,
    timeout_seconds: int,
    non_interactive: bool,
) -> dict[str, Any]:
    """Drive a one-shot scan through the LangGraph SDK; return a result envelope.

    Wraps the SDK in a defensive import so a missing langgraph-sdk yields
    EXIT_CONFIG rather than a Python traceback in headless logs.
    """
    try:
        from langgraph_sdk import get_client  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "langgraph-sdk is not installed in this Python environment. "
            "Install with: pip install langgraph-sdk"
        ) from exc

    client = get_client(url=langgraph_url)

    scope_payload = {
        "targets": targets,
        "scope_mode": "full",
        "diff_files": [],
        "scan_mode": scan_mode,
        "instruction": instruction,
    }
    state_input = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Run a one-shot security scan. Scope and instructions "
                    f"are attached as JSON:\n\n{json.dumps(scope_payload, indent=2)}"
                ),
            }
        ],
        "engagement_name": engagement_name,
        "scan_scope": scope_payload,
    }
    config = {
        "configurable": {
            "engagement_name": engagement_name,
            "scan_mode": scan_mode,
        }
    }

    import asyncio  # noqa: PLC0415

    async def _run() -> dict[str, Any]:
        thread = await client.threads.create()
        events_collected: list[dict[str, Any]] = []
        last_event_kind = ""
        async for chunk in client.runs.stream(
            thread["thread_id"],
            assistant_id=assistant,
            input=state_input,
            config=config,
            stream_mode=["values", "updates", "custom"],
        ):
            event = {
                "event": chunk.event,
                "data": chunk.data,
            }
            events_collected.append(event)
            last_event_kind = chunk.event
            if non_interactive:
                _emit_jsonl_event({"type": chunk.event, "data": chunk.data})
        return {
            "thread_id": thread["thread_id"],
            "last_event": last_event_kind,
            "event_count": len(events_collected),
        }

    return asyncio.run(asyncio.wait_for(_run(), timeout=timeout_seconds))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if err := _validate_targets(args.target):
        print(f"error: {err}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        instruction = _instruction_text(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    engagement_name = _resolve_engagement_name(args.engagement_name)

    # Generate the enforced RoE from the creation scope before any run is
    # dispatched — the CLI is a first-class engagement surface and must leave
    # the same plan/roe.json the MCP bridge does.
    try:
        materialize_engagement_roe(
            engagement_name,
            args.target,
            out_of_scope=list(args.out_of_scope) or None,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.blue_target:
        # Blue observation is optional: the engagement runs fine without it.
        # Fail closed on a broken blue configuration, never on its absence.
        _bc = _bluecell()
        reviewed = _bc.config_path(engagement_name).exists()
        try:
            _bc.materialize_blue_config(
                engagement_name,
                _bc.build_local_target_config(
                    args.blue_target,
                    logs=[args.blue_logs] if args.blue_logs else None,
                ),
            )
        except (ValueError, OSError) as exc:
            print(f"error: blue configuration: {exc}", file=sys.stderr)
            return EXIT_CONFIG
        if reviewed:
            print(
                "blue configuration: reviewed blue.json already exists; left unchanged",
                file=sys.stderr,
            )
        else:
            print(f"blue configuration: {_bc.config_path(engagement_name)}", file=sys.stderr)

    mode_cfg = _MODE_DEFAULTS[args.scan_mode]
    timeout = args.timeout or mode_cfg["timeout"]

    try:
        result = _dispatch_scan_via_sdk(
            langgraph_url=args.langgraph_url,
            assistant=args.assistant,
            engagement_name=engagement_name,
            targets=args.target,
            instruction=instruction,
            scan_mode=args.scan_mode,
            timeout_seconds=timeout,
            non_interactive=args.non_interactive,
        )
        log.info("scan complete: %s", result)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001
        print(f"scan failed: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    return EXIT_OK
