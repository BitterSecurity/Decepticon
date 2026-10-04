"""Unit tests for the reciprocal-rank fusion in hybrid find_skill (ADR-0011).

``_rrf_fuse`` is the pure-Python core of hybrid retrieval — it merges the
lexical and semantic ranked legs without touching Neo4j, so we can pin its
behaviour deterministically. The cypher legs themselves are covered by live
verification against a running graph.
"""

from __future__ import annotations

from typing import Any

from decepticon.skillogy.server.neo4j_backend import Neo4jBackend


def _rec(name: str, path: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "subdomain": "web",
        "description": f"desc {name}",
        "matched_mitre": [],
        "matched_tags": [],
        **extra,
    }


def test_found_by_both_legs_outranks_single_leg() -> None:
    # "shared" is rank-2 in lexical and rank-1 in semantic → highest fused.
    lexical = [_rec("a", "/a"), _rec("shared", "/shared")]
    semantic = [_rec("shared", "/shared", score=0.9), _rec("b", "/b", score=0.5)]
    out = Neo4jBackend._rrf_fuse(lexical, semantic, limit=10)
    assert out[0]["path"] == "/shared"
    assert {r["path"] for r in out} == {"/a", "/shared", "/b"}


def test_score_field_stripped() -> None:
    semantic = [_rec("s", "/s", score=0.9)]
    out = Neo4jBackend._rrf_fuse([], semantic, limit=10)
    assert out == [_rec("s", "/s", matched_by=["semantic"])]
    assert "score" not in out[0]


def test_matched_by_reports_both_legs() -> None:
    # The agent's only signal for match strength: a hit found by both legs
    # must say so, and a single-leg hit must not claim the other leg.
    lexical = [_rec("shared", "/shared"), _rec("lex-only", "/l")]
    semantic = [_rec("shared", "/shared", score=0.9), _rec("sem-only", "/s", score=0.4)]
    by_path = {
        r["path"]: r["matched_by"] for r in Neo4jBackend._rrf_fuse(lexical, semantic, limit=10)
    }
    assert by_path["/shared"] == ["lexical", "semantic"]
    assert by_path["/l"] == ["lexical"]
    assert by_path["/s"] == ["semantic"]


def test_respects_limit() -> None:
    lexical = [_rec(f"n{i}", f"/n{i}") for i in range(5)]
    out = Neo4jBackend._rrf_fuse(lexical, [], limit=3)
    assert len(out) == 3
    # lexical order preserved when there is no semantic leg
    assert [r["path"] for r in out] == ["/n0", "/n1", "/n2"]


def test_deterministic_tiebreak_by_name() -> None:
    # Two skills each found once at the same rank → equal score, name breaks tie.
    lexical = [_rec("zebra", "/z")]
    semantic = [_rec("apple", "/a", score=0.9)]
    out = Neo4jBackend._rrf_fuse(lexical, semantic, limit=10)
    assert [r["name"] for r in out] == ["apple", "zebra"]


def test_empty_both_legs() -> None:
    assert Neo4jBackend._rrf_fuse([], [], limit=10) == []


def test_dedup_keeps_first_seen_record() -> None:
    # Same path in both legs: the lexical record (seen first) is kept, so the
    # output shape is the score-free one.
    lexical = [_rec("dup", "/dup", description="lexical desc")]
    semantic = [_rec("dup", "/dup", score=0.9, description="semantic desc")]
    out = Neo4jBackend._rrf_fuse(lexical, semantic, limit=10)
    assert len(out) == 1
    assert out[0]["description"] == "lexical desc"
    assert "score" not in out[0]
