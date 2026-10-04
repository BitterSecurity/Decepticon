"""The bundled graph is validated and published as one release."""

from __future__ import annotations

from typing import cast

import pytest

from decepticon.skillogy.catalog_sync import (
    CatalogRelease,
    _owned_statement,
    reconcile_bundled_catalog,
)
from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

_DUMP = (
    "// generated\nMERGE (n:Tag {name: 'web'});\n"
    "MERGE (n:Skill {name: 'test'})\nSET n.path = '/skills/test/SKILL.md';\n"
    "MATCH (a:Skill {name: 'test'}), (b:Tag {name: 'web'})\n"
    "MERGE (a)-[r:TAGGED]->(b);\n"
)


def test_release_rejects_empty_or_skillless_dump() -> None:
    with pytest.raises(ValueError, match="no Skill nodes"):
        CatalogRelease.from_cypher("MERGE (n:Tag {name: 'web'});\n")


def test_bundle_tagging_preserves_original_statements() -> None:
    release = CatalogRelease.from_cypher(_DUMP)
    assert release.skill_count == 1
    assert len(release.statements) == 3
    assert "n.catalog_release = $catalog_release" in _owned_statement(release.statements[1])
    assert "r.catalog_release = $catalog_release" in _owned_statement(release.statements[2])


class _Result:
    def __init__(self, row=None) -> None:
        self.row = row

    def single(self):
        return self.row

    def consume(self):
        return None


class _Transaction:
    def __init__(self, current_digest: str | None, skill_count: int) -> None:
        self.current_digest = current_digest
        self.skill_count = skill_count
        self.queries: list[str] = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def run(self, query: str, **kwargs):
        self.queries.append(query)
        if "RETURN c.digest" in query:
            return _Result({"digest": self.current_digest})
        if "RETURN count(s)" in query:
            return _Result({"skill_count": self.skill_count})
        return _Result()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _Session:
    def __init__(self, transaction: _Transaction) -> None:
        self.transaction = transaction

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def run(self, query: str, **kwargs):
        if "RETURN c.digest" in query:
            return _Result({"digest": self.transaction.current_digest})
        assert "CREATE CONSTRAINT skillogy_catalog_name" in query
        return _Result()

    def begin_transaction(self, *, timeout: int):
        assert timeout == 600
        return self.transaction


class _Driver:
    def __init__(self, transaction: _Transaction) -> None:
        self.transaction = transaction

    def session(self, *, database: str):
        assert database == "neo4j"
        return _Session(self.transaction)


class _Backend:
    def __init__(self, transaction: _Transaction) -> None:
        self._driver = _Driver(transaction)
        self._database = "neo4j"


def _backend(transaction: _Transaction) -> Neo4jBackend:
    return cast(Neo4jBackend, _Backend(transaction))


def test_same_release_skips_graph_mutations() -> None:
    release = CatalogRelease.from_cypher(_DUMP)
    tx = _Transaction(release.digest, skill_count=0)

    assert not reconcile_bundled_catalog(_backend(tx), release)
    assert tx.queries == []
    assert not tx.committed


def test_verification_failure_does_not_commit_release() -> None:
    release = CatalogRelease.from_cypher(_DUMP)
    tx = _Transaction(None, skill_count=0)

    with pytest.raises(RuntimeError, match="verification failed"):
        reconcile_bundled_catalog(_backend(tx), release)

    assert not tx.committed
    assert not any("c.digest =" in query for query in tx.queries)


def test_changed_release_marks_and_publishes_after_verification() -> None:
    release = CatalogRelease.from_cypher(_DUMP)
    tx = _Transaction("old-digest", skill_count=1)

    assert reconcile_bundled_catalog(_backend(tx), release)
    assert tx.committed
    assert "catalog_owner = $catalog_owner" in "\n".join(tx.queries)
    assert "c.digest = $release" in tx.queries[-1]
    assert not any("s.built_at =" in query for query in tx.queries)


def test_first_managed_release_removes_only_known_legacy_records() -> None:
    release = CatalogRelease.from_cypher(_DUMP)
    tx = _Transaction(None, skill_count=1)

    assert reconcile_bundled_catalog(_backend(tx), release)
    assert any("s.built_at = $built_at" in query for query in tx.queries)
    assert any(
        "SET s:SkillogyLegacySkill" in query and "REMOVE s:Skill" in query for query in tx.queries
    )
    assert any("r.catalog_owner IS NULL" in query for query in tx.queries)
