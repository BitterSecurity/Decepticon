"""Keep the planner's inline field tables in sync with its Pydantic models."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel

from decepticon_core.types.engagement import (
    CONOPS,
    AbortPlan,
    CleanupPlan,
    ContactPlan,
    DataHandlingPlan,
    DeconflictionPlan,
    ThreatProfile,
)

_SKILLS = Path(__file__).parents[3] / "decepticon/skills/standard/soundwave"
_MODELS = [
    ("threat-profile", ThreatProfile),
    ("conops-template", CONOPS),
    ("deconfliction-template", DeconflictionPlan),
    ("contact-template", ContactPlan),
    ("data-handling-template", DataHandlingPlan),
    ("abort-template", AbortPlan),
    ("cleanup-template", CleanupPlan),
]


def _type_label(annotation: Any) -> str:
    if annotation is type(None):
        return "None"
    origin = get_origin(annotation)
    if origin is UnionType:
        return " \\| ".join(_type_label(arg) for arg in get_args(annotation))
    if origin:
        return f"{origin.__name__}[{', '.join(_type_label(arg) for arg in get_args(annotation))}]"
    return annotation.__name__


def _leaf_types(annotation: Any) -> list[type]:
    args = get_args(annotation)
    if args:
        return [leaf for arg in args for leaf in _leaf_types(arg)]
    return [annotation]


def _schema_rows(model: type[BaseModel]) -> list[str]:
    rows = []
    nested = {}
    properties = model.model_json_schema()["properties"]
    for name, field in model.model_fields.items():
        rules = []
        for leaf in _leaf_types(field.annotation):
            if issubclass(leaf, Enum):
                rules.append("one of " + ", ".join(f"`{member.value}`" for member in leaf))
            elif issubclass(leaf, BaseModel):
                rules.append("each object: " + ", ".join(f"`{key}`" for key in leaf.model_fields))
                nested[leaf.__name__] = leaf
        for key, label in (
            ("minimum", ">="),
            ("maximum", "<="),
            ("exclusiveMinimum", ">"),
            ("exclusiveMaximum", "<"),
        ):
            schema = properties[name]
            if key in schema:
                rules.append(f"{label} {schema[key]}")
        required = "yes" if field.is_required() else "no"
        rows.append(
            f"| `{name}` | {_type_label(field.annotation)} | {required} | {'; '.join(rules)} |"
        )
    for nested_model in sorted(nested.values(), key=lambda cls: cls.__name__):
        rows.extend(_schema_rows(nested_model))
    return rows


@pytest.mark.parametrize(("skill", "model"), _MODELS)
def test_planning_skill_schema_matches_validator(skill: str, model: type[BaseModel]) -> None:
    body = (_SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    section = body.split("## Document schema (derived from the validator's model)\n", 1)[1]
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    assert rows == _schema_rows(model)
