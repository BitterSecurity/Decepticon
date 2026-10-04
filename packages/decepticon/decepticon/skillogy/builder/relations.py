"""Typed, checked skill-to-skill relation declarations for the OSS graph."""

from __future__ import annotations

import re
from typing import Any

from decepticon.skillogy.builder.model import Edge

RELATION_FIELDS: dict[str, str] = {
    "requires": "REQUIRES",
    "validated_by": "VALIDATED_BY",
    "composes_with": "COMPOSES_WITH",
    "specializes": "SPECIALIZES",
    "alternative_to": "ALTERNATIVE_TO",
    "conflicts_with": "CONFLICTS_WITH",
}
SYMMETRIC = frozenset({"COMPOSES_WITH", "ALTERNATIVE_TO", "CONFLICTS_WITH"})
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def relation_edges(
    source: str,
    raw: Any,
    known_names: set[str],
) -> list[Edge]:
    """Validate one skillogy v1 block and emit closed-catalog edges.

    Unknown targets fail the build so typos cannot silently become dangling
    graph relationships. A target may carry only one semantic from a source.
    """
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError(f"{source}: metadata.skillogy must be a v1 mapping")
    unknown_fields = set(raw) - {"version", *RELATION_FIELDS}
    if unknown_fields:
        raise ValueError(f"{source}: unsupported skillogy fields {sorted(unknown_fields)}")
    seen: set[str] = set()
    edges: list[Edge] = []
    for field, edge_type in RELATION_FIELDS.items():
        targets = raw.get(field, [])
        if not isinstance(targets, list):
            raise ValueError(f"{source}: skillogy.{field} must be a list")
        for target in targets:
            if not isinstance(target, str) or not _NAME.fullmatch(target):
                raise ValueError(f"{source}: invalid skillogy.{field} target {target!r}")
            if target == source:
                raise ValueError(f"{source}: self-referential skillogy relation")
            if target in seen:
                raise ValueError(f"{source}: duplicate or conflicting skillogy target {target}")
            if target not in known_names:
                raise ValueError(f"{source}: unknown skillogy target {target}")
            seen.add(target)
            edges.append(Edge(edge_type, "Skill", "name", source, "Skill", "name", target))
            if edge_type in SYMMETRIC:
                edges.append(Edge(edge_type, "Skill", "name", target, "Skill", "name", source))
    return edges
