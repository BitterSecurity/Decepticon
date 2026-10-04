"""Agent-facing ATT&CK ID lookup against the pinned, verified STIX bundle.

The Skillogy importer and this tool share one bootstrap/cache path. The STIX
bundle is fetched only on first lookup, SHA-256 checked, and then indexed in
memory. An unavailable bundle produces a recoverable tool error; it never
turns an unvalidated ID into a plausible-looking result.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from decepticon.skillogy.builder.stix_bootstrap import ensure_stix_bundle


def _attack_id(obj: dict[str, Any]) -> str | None:
    for ref in obj.get("external_references") or []:
        if isinstance(ref, dict) and ref.get("source_name") == "mitre-attack":
            value = ref.get("external_id")
            if isinstance(value, str):
                return value
    return None


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    path: Path = ensure_stix_bundle()
    objects = json.loads(path.read_text(encoding="utf-8")).get("objects", [])
    by_id: dict[str, dict[str, Any]] = {}
    stix_to_attack: dict[str, str] = {}
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        attack_id = _attack_id(obj)
        if not attack_id:
            continue
        if obj.get("type") in {"attack-pattern", "x-mitre-tactic", "intrusion-set"}:
            by_id[attack_id] = obj
            stix_to_attack[str(obj.get("id"))] = attack_id
    group_techniques: dict[str, list[str]] = {}
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("type") != "relationship":
            continue
        if obj.get("relationship_type") != "uses" or obj.get("revoked"):
            continue
        group = stix_to_attack.get(str(obj.get("source_ref")))
        technique = stix_to_attack.get(str(obj.get("target_ref")))
        if group and technique and group.startswith("G") and technique.startswith("T"):
            group_techniques.setdefault(group, []).append(technique)
    return by_id, group_techniques


def _lookup(identifier: str, expected_type: str) -> str:
    try:
        by_id, group_techniques = _index()
        obj = by_id.get(identifier.strip().upper())
        if obj is None or obj.get("type") != expected_type:
            return json.dumps({"error": f"unknown ATT&CK ID {identifier!r}"})
        attack_id = _attack_id(obj)
        result: dict[str, Any] = {
            "id": attack_id,
            "name": obj.get("name", ""),
            "description": (obj.get("description") or "")[:600],
        }
        if expected_type == "attack-pattern":
            result["tactics"] = sorted(
                {
                    phase.get("phase_name")
                    for phase in obj.get("kill_chain_phases") or []
                    if isinstance(phase, dict)
                    and phase.get("kill_chain_name") == "mitre-attack"
                    and isinstance(phase.get("phase_name"), str)
                }
            )
            result["is_subtechnique"] = bool(obj.get("x_mitre_is_subtechnique"))
        elif expected_type == "x-mitre-tactic":
            result["shortname"] = obj.get("x_mitre_shortname", "")
        elif expected_type == "intrusion-set":
            result["aliases"] = list(obj.get("aliases") or [])[:20]
            result["technique_ids"] = sorted(set(group_techniques.get(attack_id or "", [])))[:100]
        return json.dumps(result, ensure_ascii=False)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return json.dumps({"error": f"ATT&CK lookup unavailable: {exc}"})


@tool
def mitre_lookup_technique(technique_id: str) -> str:
    """Validate an ATT&CK technique ID (Txxxx or Txxxx.xxx) against pinned Enterprise STIX."""
    return _lookup(technique_id, "attack-pattern")


@tool
def mitre_lookup_tactic(tactic_id: str) -> str:
    """Validate an ATT&CK tactic ID (TAxxxx) against pinned Enterprise STIX."""
    return _lookup(tactic_id, "x-mitre-tactic")


@tool
def mitre_lookup_group(group_id: str) -> str:
    """Validate an ATT&CK group ID (Gxxxx) against pinned Enterprise STIX."""
    return _lookup(group_id, "intrusion-set")


MITRE_TOOLS: tuple[BaseTool, ...] = (
    mitre_lookup_technique,
    mitre_lookup_tactic,
    mitre_lookup_group,
)


def get_mitre_tools(role: str | None = None, **_: Any) -> list[BaseTool]:
    """Contribute read-only lookup tools only to roles that author ATT&CK IDs."""
    if role in {"decepticon", "exploit", "finding_reporter"}:
        return list(MITRE_TOOLS)
    return []
