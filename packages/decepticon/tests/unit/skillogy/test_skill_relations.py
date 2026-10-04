"""Skill relation declarations must compile into safe, closed graph edges."""

from __future__ import annotations

import pytest

from decepticon.skillogy.builder.relations import relation_edges


def test_direction_and_symmetric_semantics():
    edges = relation_edges(
        "report",
        {"version": 1, "requires": ["protocol"], "composes_with": ["summary"]},
        {"report", "protocol", "summary"},
    )
    assert [(e.from_key, e.edge_type, e.to_key) for e in edges] == [
        ("report", "REQUIRES", "protocol"),
        ("report", "COMPOSES_WITH", "summary"),
        ("summary", "COMPOSES_WITH", "report"),
    ]


@pytest.mark.parametrize(
    "block",
    [
        {"version": 2, "requires": ["protocol"]},
        {"version": 1, "requires": ["missing"]},
        {"version": 1, "requires": ["report"]},
        {"version": 1, "requires": ["protocol"], "validated_by": ["protocol"]},
        {"version": 1, "requires": "protocol"},
        {"version": 1, "BAD_REL": ["protocol"]},
    ],
)
def test_reject_invalid_or_ambiguous_relations(block):
    with pytest.raises(ValueError):
        relation_edges("report", block, {"report", "protocol"})
