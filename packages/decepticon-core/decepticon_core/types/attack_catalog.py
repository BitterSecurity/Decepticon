from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from decepticon_core.types.engagement import AttackAnnotation, AttackTechnique


@lru_cache(maxsize=1)
def enterprise_attack_catalog() -> dict[str, Any]:
    path = Path(__file__).with_name("attack_catalog_19_2.json")
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_attack_annotation(
    tactic_id: str, technique_ids: list[str]
) -> AttackAnnotation:
    catalog = enterprise_attack_catalog()
    tactic = catalog["tactics"].get(tactic_id)
    if tactic is None:
        raise ValueError(f"Unknown or revoked ATT&CK tactic {tactic_id!r} in v{catalog['version']}")

    techniques: list[AttackTechnique] = []
    for technique_id in dict.fromkeys(technique_ids):
        technique = catalog["techniques"].get(technique_id)
        if technique is None:
            replacement = catalog["replaced_by"].get(technique_id)
            suggestion = f"; official replacement: {replacement}" if replacement else ""
            raise ValueError(
                f"Unknown or revoked ATT&CK technique {technique_id!r} "
                f"in v{catalog['version']}{suggestion}"
            )
        if tactic_id not in technique["tactics"]:
            allowed = ", ".join(technique["tactics"])
            raise ValueError(
                f"ATT&CK technique {technique_id} does not belong to {tactic_id} "
                f"in v{catalog['version']}; catalog tactics: {allowed}"
            )
        techniques.append(
            AttackTechnique(
                id=technique_id,
                name=technique["name"],
                description=technique["description"],
            )
        )

    return AttackAnnotation(
        catalog=catalog["catalog"],
        version=catalog["version"],
        tactic_id=tactic_id,
        tactic_name=tactic["name"],
        tactic_description=tactic["description"],
        techniques=techniques,
    )
