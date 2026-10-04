"""Unit tests for the boot-time embedding backfill (ADR-0011).

Exercises the orchestration in ``embed_ingest.ingest_embeddings`` against a
fake backend and a faked embeddings module — no Neo4j, no proxy. Pins the
incremental-by-sha contract (re-embed only changed/missing skills), the
silent skip when the proxy is unconfigured, and per-row failure handling.
"""

from __future__ import annotations

from typing import Any

import pytest

from decepticon.skillogy import embed_ingest, embeddings


class _FakeBackend:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.index_dim: int | None = None
        self.read_dim: int | None = None
        self.write_dim: int | None = None
        self.ready_dim: int | None = None
        self.written: list[dict[str, Any]] = []

    def ensure_vector_index(self, dim: int) -> None:
        self.index_dim = dim

    def fetch_skills_for_embedding(self, dim: int) -> list[dict[str, Any]]:
        self.read_dim = dim
        return self._rows

    def write_embeddings(self, rows: list[dict[str, Any]], dim: int) -> int:
        self.write_dim = dim
        self.written.extend(rows)
        return len(rows)

    def mark_vector_ready(self, dim: int) -> None:
        self.ready_dim = dim


@pytest.fixture
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_dim", lambda: 8)
    monkeypatch.setattr(embeddings, "embed_model", lambda: "test-model")
    # Deterministic, non-None vector per input.
    monkeypatch.setattr(
        embeddings, "embed_batch", lambda texts: [[float(len(t))] * 8 for t in texts]
    )


def test_skips_when_proxy_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "available", lambda: False)
    backend = _FakeBackend([{"path": "/p", "name": "n", "description": "d", "when_to_use": "w"}])
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 0, "skipped": 0, "failed": 0}
    assert backend.index_dim is None  # no index DDL when unavailable
    assert backend.written == []


def test_embeds_all_missing(_fake_embeddings: None) -> None:
    backend = _FakeBackend(
        [
            {"path": "/a", "name": "alpha", "description": "d1", "when_to_use": "w1"},
            {"path": "/b", "name": "beta", "description": "d2", "when_to_use": "w2"},
        ]
    )
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 2, "skipped": 0, "failed": 0}
    assert backend.index_dim == 8
    assert backend.read_dim == backend.write_dim == 8
    assert backend.ready_dim == 8
    assert {r["path"] for r in backend.written} == {"/a", "/b"}
    assert all("vector" in r and "sha" in r for r in backend.written)


def test_skips_unchanged_by_sha(_fake_embeddings: None) -> None:
    text = embed_ingest.build_embed_text("alpha", "d1", "w1")
    sha = embed_ingest._input_sha("test-model", text)
    backend = _FakeBackend(
        [
            {
                "path": "/a",
                "name": "alpha",
                "description": "d1",
                "when_to_use": "w1",
                "embedding_input_sha256": sha,  # already current
                "embedded": True,
            },
            {"path": "/b", "name": "beta", "description": "d2", "when_to_use": "w2"},
        ]
    )
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 1, "skipped": 1, "failed": 0}
    assert [r["path"] for r in backend.written] == ["/b"]


def test_changed_content_reembeds(_fake_embeddings: None) -> None:
    stale_sha = embed_ingest._input_sha("test-model", "totally different text")
    backend = _FakeBackend(
        [
            {
                "path": "/a",
                "name": "alpha",
                "description": "NEW description",
                "when_to_use": "w1",
                "embedding_input_sha256": stale_sha,
                "embedded": True,
            }
        ]
    )
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 1, "skipped": 0, "failed": 0}


def test_model_change_reembeds(_fake_embeddings: None) -> None:
    # Same text, but the stored sha was produced by a DIFFERENT model →
    # folding the model into the sha must force a re-embed (no stale vectors).
    text = embed_ingest.build_embed_text("alpha", "d1", "w1")
    old_model_sha = embed_ingest._input_sha("some-other-model", text)
    backend = _FakeBackend(
        [
            {
                "path": "/a",
                "name": "alpha",
                "description": "d1",
                "when_to_use": "w1",
                "embedding_input_sha256": old_model_sha,
                "embedded": True,
            }
        ]
    )
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 1, "skipped": 0, "failed": 0}


def test_missing_dimension_specific_vector_reembeds_even_with_current_sha(
    _fake_embeddings: None,
) -> None:
    text = embed_ingest.build_embed_text("alpha", "d1", "w1")
    backend = _FakeBackend(
        [
            {
                "path": "/a",
                "name": "alpha",
                "description": "d1",
                "when_to_use": "w1",
                "embedding_input_sha256": embed_ingest._input_sha("test-model", text),
                "embedded": False,
            }
        ]
    )

    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]

    assert stats == {"embedded": 1, "skipped": 0, "failed": 0}
    assert backend.write_dim == 8


def test_empty_text_skipped(_fake_embeddings: None) -> None:
    backend = _FakeBackend([{"path": "/a", "name": "", "description": "", "when_to_use": ""}])
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 0, "skipped": 1, "failed": 0}
    assert backend.written == []


def test_failed_embedding_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_dim", lambda: 8)
    # First input embeds, second returns None (proxy hiccup).
    monkeypatch.setattr(embeddings, "embed_batch", lambda texts: [[1.0] * 8, None])
    backend = _FakeBackend(
        [
            {"path": "/a", "name": "alpha", "description": "d", "when_to_use": "w"},
            {"path": "/b", "name": "beta", "description": "d", "when_to_use": "w"},
        ]
    )
    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]
    assert stats == {"embedded": 1, "skipped": 0, "failed": 1}
    assert [r["path"] for r in backend.written] == ["/a"]
    assert backend.ready_dim is None


def test_wrong_embedding_dimension_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_dim", lambda: 8)
    monkeypatch.setattr(embeddings, "embed_batch", lambda texts: [[1.0] * 7 for _ in texts])
    backend = _FakeBackend(
        [{"path": "/a", "name": "alpha", "description": "d", "when_to_use": "w"}]
    )

    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]

    assert stats == {"embedded": 0, "skipped": 0, "failed": 1}
    assert backend.written == []
    assert backend.ready_dim is None


def test_partial_database_write_does_not_enable_semantic_search(
    _fake_embeddings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _FakeBackend(
        [{"path": "/a", "name": "alpha", "description": "d", "when_to_use": "w"}]
    )
    monkeypatch.setattr(backend, "write_embeddings", lambda rows, dim: 0)

    stats = embed_ingest.ingest_embeddings(backend)  # type: ignore[arg-type]

    assert stats == {"embedded": 0, "skipped": 0, "failed": 1}
    assert backend.ready_dim is None


def test_build_embed_text_excludes_blank_fields() -> None:
    assert embed_ingest.build_embed_text("name", "", "  ") == "name"
    assert embed_ingest.build_embed_text("name", "desc", "trigger") == "name\ndesc\ntrigger"
