"""``python -m decepticon.cli`` dispatcher."""

from __future__ import annotations

import sys

from decepticon.cli.audit import main as audit_main
from decepticon.cli.auth import main as auth_main
from decepticon.cli.blue_engagement import main as blue_engagement_main
from decepticon.cli.export_transcript import main as export_transcript_main
from decepticon.cli.scan import main as scan_main
from decepticon.cli.telemetry import main as telemetry_main
from decepticon.cli.zip import main as zip_main


def _print_help() -> int:
    print(
        "decepticon-cli — headless / CI entry\n\n"
        "Subcommands:\n"
        "  scan               Run a one-shot security scan and emit SARIF\n"
        "  auth               Show provider/auth configuration (API keys + subscriptions)\n\n"
        "  blue-engagement    Create/read the Blue target configuration (blue.json)\n\n"
        "  audit              Verify engagement audit ledgers\n"
        "  zip                Export/import engagement workspaces as ZIP archives\n\n"
        "  export-transcript  Render an engagement event log as Markdown\n\n"
        "  telemetry          Inspect/control usage telemetry (status|preview|off|on)\n\n"
        "Run a subcommand with --help for its flags.",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in {"-h", "--help"}:
        return _print_help()
    sub = args[0]
    rest = args[1:]
    if sub == "scan":
        return scan_main(rest)
    if sub == "auth":
        return auth_main(rest)
    if sub == "audit":
        return audit_main(rest)
    if sub == "blue-engagement":
        try:
            return blue_engagement_main(rest)
        except RuntimeError as error:
            # Blue is an optional capability (the AutoBot package). A missing
            # or broken blue dependency surfaces as a clean install hint,
            # never a traceback.
            print(f"blue-engagement: {error}", file=sys.stderr)
            return 2
    if sub == "zip":
        return zip_main(rest)
    if sub == "export-transcript":
        return export_transcript_main(rest)
    if sub == "telemetry":
        return telemetry_main(rest)
    print(f"unknown subcommand: {sub}\n", file=sys.stderr)
    _print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
