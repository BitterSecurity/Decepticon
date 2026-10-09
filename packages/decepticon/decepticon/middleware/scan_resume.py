"""Scan resume — persist and restore engagement state.

Saves agent coordinator state, objective progress, and workspace snapshots
to enable resuming an interrupted engagement from where it left off.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _state_dir() -> Path:
    return _workspace() / ".resume"


class ScanResumeManager:
    """Manages scan state persistence for resume capability."""

    def __init__(self):
        self._state_dir = _state_dir()

    def save_checkpoint(self, state: dict[str, Any]) -> Path:
        """Save a checkpoint of the current scan state."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent_states": state.get("agent_states", {}),
            "objectives_progress": state.get("objectives_progress", {}),
            "current_wave": state.get("current_wave"),
            "findings_count": state.get("findings_count", 0),
            "coverage_count": state.get("coverage_count", 0),
            "budget_used": state.get("budget_used", 0.0),
            "exit_reason": state.get("exit_reason"),
        }
        path = self._state_dir / "checkpoint.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(checkpoint, indent=2, default=str), "utf-8")
        tmp.replace(path)
        return path

    def load_checkpoint(self) -> dict[str, Any] | None:
        """Load the latest checkpoint if one exists."""
        path = self._state_dir / "checkpoint.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def has_checkpoint(self) -> bool:
        return (self._state_dir / "checkpoint.json").exists()

    def clear_checkpoint(self) -> None:
        path = self._state_dir / "checkpoint.json"
        if path.exists():
            path.unlink()

    def list_resumable_runs(self, runs_dir: str = "") -> list[dict]:
        """List all runs with checkpoints available for resume."""
        base = Path(runs_dir) if runs_dir else Path("/workspace")
        resumable = []
        for d in sorted(base.iterdir()) if base.exists() else []:
            cp = d / ".resume" / "checkpoint.json"
            if cp.exists():
                try:
                    data = json.loads(cp.read_text("utf-8"))
                    resumable.append(
                        {
                            "run_name": d.name,
                            "path": str(d),
                            "checkpoint_time": data.get("timestamp_iso"),
                            "findings": data.get("findings_count", 0),
                            "exit_reason": data.get("exit_reason"),
                        }
                    )
                except Exception:
                    pass
        return resumable
