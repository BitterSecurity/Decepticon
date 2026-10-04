"""Runtime Skillogy reads have a server-side Neo4j transaction deadline."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

Query = pytest.importorskip("neo4j").Query


@pytest.fixture
def backend() -> Neo4jBackend:
    instance = Neo4jBackend.__new__(Neo4jBackend)
    instance._database = "neo4j"
    instance._driver = MagicMock()
    instance._max_rows = 20
    instance._fulltext_ready = True
    return instance


@pytest.mark.parametrize(
    "read",
    [
        lambda backend: backend.load_skill("/skills/shared/demo/SKILL.md"),
        lambda backend: backend.health(),
        lambda backend: backend.query_moc_summary("reconnaissance"),
        lambda backend: backend.traverse("/skills/shared/demo/SKILL.md"),
        lambda backend: backend.run_cypher_read("MATCH (s:Skill) RETURN s"),
        lambda backend: backend._find_lexical("demo", [], None, {}, 10),
        lambda backend: backend._find_semantic([0.1, 0.2], [], None, {}, 10),
    ],
)
def test_agent_facing_reads_have_five_second_query_timeout(backend, read) -> None:
    session = backend._driver.session.return_value.__enter__.return_value
    session.run.return_value = MagicMock()
    session.run.return_value.__iter__.return_value = iter(())
    session.run.return_value.fetch.return_value = []

    read(backend)

    query = session.run.call_args.args[0]
    assert isinstance(query, Query)
    assert query.timeout == 5.0
    assert backend._driver.session.call_args.kwargs["default_access_mode"] == "READ"
