"""Boot-time catalog publication and embedding backfill ordering."""

from __future__ import annotations

from decepticon.skillogy import __main__ as skillogy_main


class _FakeBackend:
    def __init__(self) -> None:
        self.fulltext_index_calls = 0

    def ensure_fulltext_index(self) -> None:
        self.fulltext_index_calls += 1


def test_publishes_bundled_release_on_boot(monkeypatch, tmp_path) -> None:
    cypher = tmp_path / "skills.cypher"
    cypher.write_text(
        "MERGE (n:Skill {name: 'x'})\nSET n.path = '/skills/x/SKILL.md';\n", encoding="utf-8"
    )
    monkeypatch.setenv("SKILLOGY_CYPHER_PATH", str(cypher))
    releases = []
    monkeypatch.setattr(
        skillogy_main,
        "reconcile_bundled_catalog",
        lambda backend, release: releases.append(release),
    )

    skillogy_main._reconcile_catalog(_FakeBackend())  # type: ignore[arg-type]

    assert len(releases) == 1
    assert releases[0].skill_count == 1


def test_embedding_backfill_follows_catalog_reconcile(monkeypatch) -> None:
    import decepticon.skillogy.embed_ingest as embed

    calls: list[str] = []
    monkeypatch.setattr(
        skillogy_main, "_reconcile_catalog", lambda backend: calls.append("catalog")
    )
    monkeypatch.setattr(embed, "ingest_embeddings", lambda backend: calls.append("embeddings"))

    backend = _FakeBackend()
    skillogy_main._ingest_in_background(backend)  # type: ignore[arg-type]

    assert backend.fulltext_index_calls == 1
    assert calls == ["catalog", "embeddings"]


def test_failed_reconcile_still_attempts_backfill(monkeypatch) -> None:
    import decepticon.skillogy.embed_ingest as embed

    def _fail(backend: object) -> None:
        raise RuntimeError("neo4j offline")

    calls: list[object] = []
    monkeypatch.setattr(skillogy_main, "_reconcile_catalog", _fail)
    monkeypatch.setattr(embed, "ingest_embeddings", lambda backend: calls.append(backend))
    backend = _FakeBackend()

    skillogy_main._ingest_in_background(backend)  # type: ignore[arg-type]

    assert calls == [backend]


def test_fulltext_index_ensured_even_without_embeddings(monkeypatch) -> None:
    import decepticon.skillogy.embed_ingest as embed

    monkeypatch.setattr(skillogy_main, "_reconcile_catalog", lambda backend: None)

    def _no_proxy(backend: object) -> None:
        raise RuntimeError("no proxy")

    monkeypatch.setattr(embed, "ingest_embeddings", _no_proxy)

    backend = _FakeBackend()
    skillogy_main._ingest_in_background(backend)  # type: ignore[arg-type]

    assert backend.fulltext_index_calls == 1
