"""Headless Blue engagement configuration surface (``decepticon-cli blue-engagement``).

``create`` validates and materializes ``blue.json`` into the Blue-owned
workspace root (fail-closed, reviewed configuration is never overwritten);
``env`` loads the reviewed configuration and emits the sensor compose
environment as JSON for client adapters (the TUI's ``/blue up``) so the
validation and mapping stay single-sourced in this package.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _bluecell():
    """Resolve the blue engagement-configuration module (optional dependency)."""
    try:
        from autobot.bluecell import engagement_config as bc
    except ImportError as exc:  # pragma: no cover - install-shape dependent
        raise RuntimeError(
            "Blue engagement configuration requires the AutoBot package. "
            "Install it with: pip install decepticon[blue]  (or install "
            "autobot from the BitterSecurity/AutoBot repository)"
        ) from exc
    return bc


CREATE_HELP = (
    "create --label LABEL --target 127.0.0.1:3000 [--logs /abs/dir] "
    "[--rules web-rules.jsonl] [--watch-interval 15] [--watch-batch 25] [--dry-run]"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blue-engagement", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    create = sub.add_parser("create", help="materialize blue.json (fail-closed, never overwrites)")
    create.add_argument("--label", required=True)
    create.add_argument("--target", required=True, help="local upstream, e.g. 127.0.0.1:3000")
    create.add_argument("--logs", help="absolute host log directory to tail")
    create.add_argument("--rules", help="rule file relative to the Blue workspace")
    create.add_argument("--watch-interval", type=float)
    create.add_argument("--watch-batch", type=int)
    create.add_argument(
        "--dry-run", action="store_true", help="print the resolved config without writing"
    )

    env = sub.add_parser("env", help="emit the sensor compose environment as JSON")
    env.add_argument(
        "target_id", nargs="?", help="target id from blue.json (default: the enabled one)"
    )

    return parser


def _build_config(args: argparse.Namespace, bc) -> dict:
    watch: dict = {}
    if args.watch_interval is not None:
        watch["interval_seconds"] = args.watch_interval
    if args.watch_batch is not None:
        watch["batch_size"] = args.watch_batch
    return bc.build_local_target_config(
        args.target,
        logs=[args.logs] if args.logs else None,
        rules_file=args.rules,
        watch=watch or None,
    )


def _print_env(args: argparse.Namespace, bc) -> int:
    label = os.environ.get("DECEPTICON_ENGAGEMENT", "").strip()
    if not label:
        print("blue-engagement env requires DECEPTICON_ENGAGEMENT or --label", file=sys.stderr)
        return 2
    try:
        config, destination = bc.load_blue_config(label)
        root = bc.blue_workspace(label)
        target = bc.resolve_target(config, args.target_id)
        payload = {
            "label": label,
            "workspace": str(root),
            "config_path": str(destination),
            "target_id": str(target["id"]),
            "env": bc.compose_env(config, target, root),
        }
    except (ValueError, FileNotFoundError, OSError) as error:
        print(f"blue-engagement env: {error}", file=sys.stderr)
        return 1
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    bc = _bluecell()  # resolved lazily — Blue is an optional capability
    if args.action == "env":
        return _print_env(args, bc)
    label = args.label
    if not label:
        print("blue-engagement create requires --label", file=sys.stderr)
        return 2
    config = _build_config(args, bc)
    if args.dry_run:
        try:
            bc.validate_blue_config(config, root=bc.blue_workspace(label))
        except ValueError as error:
            print(f"blue-engagement create: {error}", file=sys.stderr)
            return 1
        print(json.dumps(config, indent=2))
        return 0
    try:
        already = bc.config_path(label).exists()
        destination = bc.materialize_blue_config(label, config)
    except (ValueError, OSError) as error:
        print(f"blue-engagement create: {error}", file=sys.stderr)
        return 1
    print(f"blue workspace: {bc.blue_workspace(label)}")
    if already:
        print(f"{destination} already exists — the reviewed configuration wins, nothing written.")
    else:
        print(f"wrote {destination}")
    rules = getattr(args, "rules", None)
    if rules:
        rules_path = bc.blue_workspace(label) / rules
        if rules_path.is_file():
            # Detection patterns are not secret and the sensor container
            # (uid 10001) reads them through the read-only engagement mount.
            # codeql[py/overly-permissive-file]
            rules_path.chmod(0o644)
    return 0
