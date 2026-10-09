"""Shared run-scoped threat model — single source of truth for target analysis.

Every agent reads the same threat model before hunting. Prevents redundant recon
and misaligned severity ratings. Models are keyed by normalized target identity.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


MAX_SIZE = 512 * 1024  # 512KB
MIN_LENGTH = 400
MAX_AMENDMENTS = 40
REQUIRED_SECTIONS = ["Overview", "Trust Boundaries", "Attack Surface", "Severity Calibration"]


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _models_path() -> Path:
    return _workspace() / "threat_models"


def _normalize_target(target: str) -> str:
    """Normalize target identity: git remotes, URLs, paths all collapse to one key."""
    target = target.strip()
    # Strip git credentials
    target = re.sub(r"://[^@]+@", "://", target)
    # scp-style git remote -> normalized
    m = re.match(r"^git@([^:]+):(.+?)(\.git)?$", target)
    if m:
        target = f"https://{m.group(1)}/{m.group(2)}"
    # Strip .git suffix
    target = re.sub(r"\.git$", "", target)
    # URL normalize
    parsed = urlparse(target)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname.lower()
        port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
        path = parsed.path.rstrip("/")
        return f"{host}{port}{path}"
    # Local path -> absolute
    p = Path(target)
    if p.exists():
        return str(p.resolve())
    return target.lower().strip("/")


_lock = threading.Lock()


def _model_file(target: str) -> Path:
    key = _normalize_target(target)
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", key)[:200]
    return _models_path() / f"{safe}.json"


@tool
def get_threat_model(target: str) -> str:
    """Retrieve the shared threat model for a target.

    WHEN TO USE: Before starting any testing on a target. Read the shared
    threat model to understand trust boundaries, attack surface, and
    severity calibration agreed by the team.
    """
    p = _model_file(target)
    if not p.exists():
        return _json({"exists": False, "target": target, "normalized": _normalize_target(target)})
    try:
        data = json.loads(p.read_text("utf-8"))
        return _json({"exists": True, **data})
    except (OSError, json.JSONDecodeError) as e:
        return _json({"error": str(e)})


@tool
def save_threat_model(target: str, content: str) -> str:
    """Save or replace the shared threat model for a target.

    WHEN TO USE: After initial reconnaissance, create the canonical threat
    model that all agents will reference. Must include 4 sections:
    Overview, Trust Boundaries, Attack Surface, Severity Calibration.

    Args:
        target: The target identifier
        content: The threat model text (min 400 chars, must contain all 4 required sections)
    """
    if len(content) < MIN_LENGTH:
        return _json({"error": f"content too short ({len(content)} chars, min {MIN_LENGTH})"})
    if len(content.encode()) > MAX_SIZE:
        return _json({"error": f"content exceeds {MAX_SIZE} bytes"})

    missing = [s for s in REQUIRED_SECTIONS if s.lower() not in content.lower()]
    if missing:
        return _json(
            {"error": f"missing required sections: {missing}", "required": REQUIRED_SECTIONS}
        )

    with _lock:
        p = _model_file(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "target": target,
            "normalized_key": _normalize_target(target),
            "content": content,
            "created_at": time.time(),
            "amendments": [],
        }
        p.write_text(json.dumps(data, indent=2, default=str), "utf-8")
    return _json({"saved": True, "target": target, "size": len(content)})


@tool
def amend_threat_model(target: str, addendum: str, agent_name: str = "") -> str:
    """Append an amendment to the existing threat model.

    WHEN TO USE: When new information surfaces during testing that changes
    the threat model (new trust boundary, attack surface expansion, etc.).
    Amendments are append-only and attributed.
    """
    if not addendum.strip():
        return _json({"error": "empty addendum"})

    with _lock:
        p = _model_file(target)
        if not p.exists():
            return _json(
                {"error": "no threat model exists for this target; use save_threat_model first"}
            )
        data = json.loads(p.read_text("utf-8"))
        if len(data.get("amendments", [])) >= MAX_AMENDMENTS:
            return _json(
                {"error": f"max amendments ({MAX_AMENDMENTS}) reached; re-save the full model"}
            )
        data.setdefault("amendments", []).append(
            {
                "text": addendum,
                "agent": agent_name,
                "added_at": time.time(),
            }
        )
        p.write_text(json.dumps(data, indent=2, default=str), "utf-8")
    return _json({"amended": True, "total_amendments": len(data["amendments"])})


THREAT_MODEL_TOOLS = [get_threat_model, save_threat_model, amend_threat_model]
