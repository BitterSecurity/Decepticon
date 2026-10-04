"""Unit tests for the boot seed guard + embedding backfill ordering.

The skill graph is persistent, so the boot path must seed an empty
database exactly once and never re-run the corpus against a populated
one. These two cases are the whole seed contract.

The embedding backfill (ADR-0011) has the OPPOSITE requirement: it must run on
every boot regardless of whether the seed did anything, because a persistent
graph is populated in the steady state. It used to live inside
``_seed_if_empty`` past the early return, which meant no managed/AuraDB
deployment ever embedded its corpus and ``find_skill`` silently degraded to
substring-only forever.
"""

from __future__ import annotations

from decepticon.skillogy import __main__ as skillogy_main


class _FakeBackend:
    def __init__(self, skill_count: int) -> None:
        self._skill_count = skill_count
        self.ingested: list[str] = []
        self.fulltext_index_calls = 0

    def health(self) -> dict:
        return {"status": "ok", "skill_count": self._skill_count}

    def bulk_ingest_cypher(self, cypher_text: str) -> int:
        self.ingested.append(cypher_text)
        return cypher_text.count(";")

    def ensure_fulltext_index(self) -> None:
        self.fulltext_index_calls += 1


def test_seed_skipped_when_graph_already_populated() -> None:
    backend = _FakeBackend(skill_count=326)
    skillogy_main._seed_if_empty(backend)  # type: ignore[arg-type]
    assert backend.ingested == []  # a populated graph is never re-seeded


def test_seed_runs_once_when_graph_empty(monkeypatch, tmp_path) -> None:
    cypher = tmp_path / "skills.cypher"
    cypher.write_text("MERGE (n:Skill {name: 'x'});\n", encoding="utf-8")
    monkeypatch.setenv("SKILLOGY_CYPHER_PATH", str(cypher))

    backend = _FakeBackend(skill_count=0)
    skillogy_main._seed_if_empty(backend)  # type: ignore[arg-type]
    assert len(backend.ingested) == 1  # empty graph → seeded exactly once


def test_embedding_backfill_runs_on_populated_graph(monkeypatch) -> None:
    """The regression that made prod substring-only: a populated graph short-
    circuits the seed, and the backfill must still run."""
    import decepticon.skillogy.embed_ingest as embed

    calls: list[object] = []
    monkeypatch.setattr(embed, "ingest_embeddings", lambda backend: calls.append(backend))

    backend = _FakeBackend(skill_count=326)
    skillogy_main._ingest_in_background(backend)  # type: ignore[arg-type]

    assert backend.ingested == []  # seed still skipped
    assert calls == [backend]  # ...but the corpus is still embedded


def test_fulltext_index_ensured_even_without_embeddings(monkeypatch) -> None:
    """Term search is the primary path and has no external dependency, so its
    index must be created on every boot — including when embeddings are absent."""
    import decepticon.skillogy.embed_ingest as embed

    def _no_proxy(backend: object) -> None:
        raise RuntimeError("no litellm proxy configured")

    monkeypatch.setattr(embed, "ingest_embeddings", _no_proxy)

    backend = _FakeBackend(skill_count=326)
    skillogy_main._ingest_in_background(backend)  # type: ignore[arg-type]

    assert backend.fulltext_index_calls == 1


def test_embedding_backfill_failure_does_not_kill_boot(monkeypatch) -> None:
    def _boom(backend: object) -> None:
        raise RuntimeError("proxy unreachable")

    import decepticon.skillogy.embed_ingest as embed

    monkeypatch.setattr(embed, "ingest_embeddings", _boom)

    # REST must stay up so /v1/health can report the situation.
    skillogy_main._ingest_in_background(_FakeBackend(skill_count=326))  # type: ignore[arg-type]
