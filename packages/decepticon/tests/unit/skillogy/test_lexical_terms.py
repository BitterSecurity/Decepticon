"""Unit tests for free-text query preparation in the find_skill term leg.

``_lucene_query`` is the only pure-Python step between an agent's query and the
full-text index. It exists because the pre-index clause substring-matched the
WHOLE query string against name / description / when_to_use — so any multi-word
query (exactly what an agent produces when told to describe an objective)
matched nothing and this leg returned zero rows, leaving retrieval wholly
dependent on the optional embedding leg.

Tokenisation, stemming and term weighting are deliberately NOT tested here:
they belong to Lucene/BM25, which derives term weight from document frequency in
the live corpus. There is no stopword list and no per-field weight to pin — that
is the point. The BM25 ranking itself is covered by live verification against a
running graph.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from decepticon.skillogy.server.neo4j_backend import _lucene_query


@pytest.mark.parametrize("empty", ["", "   ", "\n\t", None])
def test_blank_query_yields_empty_so_caller_takes_structured_path(empty) -> None:
    # An empty expression routes find_skill to the unscored structured-only
    # branch rather than handing Lucene a query it would reject.
    assert _lucene_query(empty) == ""


def test_multi_word_intent_query_survives_intact() -> None:
    """The regression case: this must reach the index as real terms."""
    out = _lucene_query("dump credentials from lsass process memory")
    for term in ("dump", "credentials", "lsass", "process", "memory"):
        assert term in out


def test_hyphenated_terms_do_not_become_negations() -> None:
    """A bare '-' is Lucene's NOT operator; corpus tag names contain hyphens."""
    out = _lucene_query("port-scan sub-domain")
    assert "\\-" in out  # escaped to a literal, not parsed as an operator
    assert "port" in out and "scan" in out


def test_attack_identifier_is_not_mangled() -> None:
    out = _lucene_query("T1558.003 kerberoasting")
    assert "T1558" in out and "003" in out
    assert "kerberoasting" in out


@pytest.mark.parametrize("char", list('+-&|!(){}[]^"~*?:\\/'))
def test_every_reserved_character_is_escaped(char) -> None:
    """A stray operator in agent prose must not throw a Lucene parse error."""
    out = _lucene_query(f"lsass {char} dump")
    assert f"\\{char}" in out


def test_surrounding_whitespace_trimmed() -> None:
    assert _lucene_query("  kerberoast  ") == "kerberoast"


@pytest.mark.parametrize(
    "non_latin",
    [
        "메모리에서 자격증명 추출",  # Korean
        "メモリから資格情報を抽出",  # Japanese
        "从内存中提取凭据",  # Chinese
        "1558 003",  # digits only — also unmatchable against English text
    ],
)
def test_query_without_latin_letters_is_unsearchable(non_latin) -> None:
    """The corpus is English; a query with no English words scores 0 against
    every skill, so it must be reported rather than silently returning nothing."""
    assert _lucene_query(non_latin) == ""


def test_mixed_script_query_keeps_its_latin_terms() -> None:
    out = _lucene_query("메모리에서 lsass dump 하기")
    assert "lsass" in out and "dump" in out


def test_unsearchable_query_with_no_filters_raises_actionable_error() -> None:
    """find_skill must refuse rather than fall through to a filter-only query
    that would return arbitrary skills as if the query had matched them."""
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    # The driver connects lazily, so the guard is reachable without a server.
    be = Neo4jBackend(uri="bolt://localhost:1", user="neo4j", password="x")
    with pytest.raises(ValueError, match="no searchable English terms"):
        be.find_skill(query="메모리에서 자격증명 추출")
    with pytest.raises(ValueError, match="at least one of"):
        be.find_skill(query="   ")


def test_unsearchable_query_with_filters_is_labelled_structured(monkeypatch) -> None:
    """An unusable query alongside a real filter must not claim the term leg ran.

    Verified live against the seeded graph before this guard existed: a Korean
    query plus subdomain returned hits tagged ["lexical"], asserting that term
    matching had contributed to a ranking it never touched.
    """
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend(uri="bolt://localhost:1", user="neo4j", password="x")
    row = {"name": "n", "path": "/p", "subdomain": "s", "description": "d"}
    monkeypatch.setattr(Neo4jBackend, "_find_lexical", lambda *a, **k: [dict(row)])

    hits = be.find_skill(query="메모리에서 자격증명 추출", subdomain="credential-access")
    assert [h["matched_by"] for h in hits] == [["structured"]]


def test_wrong_query_embedding_dimension_uses_lexical_results(monkeypatch) -> None:
    from decepticon.skillogy import embeddings
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    lexical = {"name": "credential", "path": "/credential"}
    monkeypatch.setattr(Neo4jBackend, "_find_lexical", lambda *a, **k: [dict(lexical)])
    semantic = MagicMock()
    monkeypatch.setattr(Neo4jBackend, "_find_semantic", semantic)
    monkeypatch.setattr(embeddings, "embed_text", lambda query: [0.1])
    monkeypatch.setattr(embeddings, "embed_dim", lambda: 2)

    hits = be.find_skill(query="credential")

    assert hits == [{**lexical, "matched_by": ["lexical"]}]
    semantic.assert_not_called()


def test_semantic_search_waits_for_backfill(monkeypatch) -> None:
    from decepticon.skillogy import embeddings
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    lexical = {"name": "credential", "path": "/credential"}
    monkeypatch.setattr(Neo4jBackend, "_find_lexical", lambda *a, **k: [dict(lexical)])
    semantic = MagicMock()
    monkeypatch.setattr(Neo4jBackend, "_find_semantic", semantic)
    monkeypatch.setattr(embeddings, "embed_text", lambda query: [0.1])
    monkeypatch.setattr(embeddings, "embed_dim", lambda: 1)

    hits = be.find_skill(query="credential")

    assert hits == [{**lexical, "matched_by": ["lexical"]}]
    semantic.assert_not_called()


def test_vector_index_and_property_follow_configured_dimension(monkeypatch) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    monkeypatch.setattr(
        "decepticon.skillogy.server.neo4j_backend._runtime_query", lambda cypher: cypher
    )

    be = Neo4jBackend.__new__(Neo4jBackend)
    be._database = "neo4j"
    be._driver = MagicMock()
    session = be._driver.session.return_value.__enter__.return_value

    be.ensure_vector_index(768)
    be.mark_vector_ready(768)
    be.ensure_vector_index(1536)
    assert be._vector_ready_dim is None
    queries = [
        call.args[0] for call in session.run.call_args_list if "CREATE VECTOR INDEX" in call.args[0]
    ]
    assert "skill_embedding_768" in queries[0] and "s.embedding_768" in queries[0]
    assert "skill_embedding_1536" in queries[1] and "s.embedding_1536" in queries[1]
    waits = [call for call in session.run.call_args_list if "db.awaitIndex" in call.args[0]]
    assert [call.kwargs["name"] for call in waits] == [
        "skill_embedding_768",
        "skill_embedding_1536",
    ]

    be.fetch_skills_for_embedding(768)
    assert "s.embedding_768 IS NOT NULL AS embedded" in session.run.call_args.args[0]

    be.write_embeddings([{"path": "/a", "vector": [0.1], "sha": "sha"}], 768)
    assert session.run.call_args.kwargs["property_name"] == "embedding_768"

    be._find_semantic([0.1, 0.2], [], None, {}, 5)
    assert session.run.call_args.kwargs["parameters"]["index_name"] == "skill_embedding_2"


@pytest.mark.parametrize("existing", [None, "english"])
def test_fulltext_index_is_online_before_search(monkeypatch, existing) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    be._database = "neo4j"
    be._driver = MagicMock()
    session = be._driver.session.return_value.__enter__.return_value
    monkeypatch.setattr(Neo4jBackend, "_available_fulltext_analyzers", lambda self: {"english"})
    monkeypatch.setattr(Neo4jBackend, "_existing_fulltext_analyzer", lambda self: existing)
    monkeypatch.delenv("DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER", raising=False)

    be.ensure_fulltext_index()

    queries = [call.args[0] for call in session.run.call_args_list]
    assert be._fulltext_ready is True
    assert any("db.awaitIndex" in query for query in queries)
    assert any("CREATE FULLTEXT INDEX" in query for query in queries) is (existing is None)
    if existing is None:
        create = next(query for query in queries if "CREATE FULLTEXT INDEX" in query)
        assert "s.name" in create
        assert "s.when_to_use" in create
        assert "s.description" in create
        assert "s.body" not in create


def test_search_uses_fallback_until_fulltext_index_is_ready(monkeypatch) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    be._fulltext_ready = False
    fallback = MagicMock(return_value=[{"path": "/skill"}])
    monkeypatch.setattr(Neo4jBackend, "_find_lexical_substring_fallback", fallback)

    hits = be._find_lexical("kerberos ticket", [], None, {}, 10)

    assert hits == [{"path": "/skill"}]
    fallback.assert_called_once()


def test_filtered_search_limits_after_acl_and_structured_filters(monkeypatch) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    neo4j_exceptions = ModuleType("neo4j.exceptions")
    neo4j_exceptions.ClientError = type("ClientError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "neo4j.exceptions", neo4j_exceptions)
    monkeypatch.setattr(
        "decepticon.skillogy.server.neo4j_backend._runtime_query", lambda cypher: cypher
    )

    be = Neo4jBackend.__new__(Neo4jBackend)
    be._fulltext_ready = True
    be._database = "neo4j"
    be._driver = MagicMock()
    session = be._driver.session.return_value.__enter__.return_value
    session.run.return_value = []

    be._find_lexical(
        "credential",
        ["s.subdomain = $subdomain"],
        "ANY(p IN $allowed_path_prefixes WHERE s.path STARTS WITH p)",
        {"subdomain": "post-exploit", "allowed_path_prefixes": ["/skills/standard/"]},
        20,
    )

    cypher = session.run.call_args.args[0]
    # Filters can drop fulltext hits, so the candidate fetch widens before
    # post-filtering — same rationale as the vector leg.
    assert "queryNodes($index_name, $lucene, {limit: $k})" in cypher
    assert session.run.call_args.kwargs["parameters"]["k"] == 100
    assert "WHERE s.subdomain = $subdomain AND ANY(" in cypher
    assert cypher.index("WHERE") < cypher.index("LIMIT $cand_n")


def test_fulltext_analyzer_rejects_unsafe_configuration(monkeypatch) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    monkeypatch.setenv(
        "DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER", "english'} MATCH (n) DETACH DELETE n"
    )
    with pytest.raises(ValueError, match="invalid DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER"):
        be.ensure_fulltext_index()
