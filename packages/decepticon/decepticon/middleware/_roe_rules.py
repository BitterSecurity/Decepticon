"""Load the engagement's machine RoE block.

Network scope is enforced only by the OpenShell sandbox policy compiled from
``plan/roe.json`` (``middleware/policy_compiler.py``); execution time windows by
``TimeWindowMiddleware``. Both read the machine block through this loader.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from decepticon_core.types.roe import MachineEnforcement

log = logging.getLogger(__name__)


def load_machine_enforcement(workspace_path: str | None) -> MachineEnforcement:
    """Read ``<workspace>/plan/roe.json:machine_enforcement``.

    Returns ``MachineEnforcement(configured=False)`` when the file or block
    is missing or unreadable.
    """
    if not workspace_path:
        return MachineEnforcement(configured=False)
    roe_path = Path(workspace_path) / "plan" / "roe.json"
    if not roe_path.exists():
        return MachineEnforcement(configured=False)
    try:
        data = json.loads(roe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("roe: failed to read %s: %s", roe_path, exc)
        return MachineEnforcement(configured=False)
    block = data.get("machine_enforcement") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return MachineEnforcement(configured=False)
    return MachineEnforcement.from_dict(block)
