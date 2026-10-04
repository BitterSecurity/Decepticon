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
    out = _lucene_query("dump credentials from lsass process memory")
    for term in ("dump", "credentials", "lsass", "process", "memory"):
        assert f"name:{term}^10" in out
        assert f"when_to_use:{term}^8" in out
    assert "name:from^10" not in out


def test_hyphenated_terms_do_not_become_negations() -> None:
    out = _lucene_query("port-scan sub-domain")
    assert "name:port\\-scan^10" in out
    assert "tags_raw:sub\\-domain^6" in out


def test_attack_identifier_is_not_mangled() -> None:
    out = _lucene_query("T1558.003 kerberoasting")
    assert "mitre_attack_raw:t1558.003^3" in out
    assert "name:kerberoasting^10" in out


@pytest.mark.parametrize("char", list('+-&|!(){}[]^"~*?:\\/'))
def test_every_reserved_character_is_escaped(char) -> None:
    out = _lucene_query(f"lsass {char} dump")
    assert "name:lsass^10" in out
    assert "name:dump^10" in out


def test_surrounding_whitespace_trimmed() -> None:
    assert _lucene_query("  kerberoast  ") == _lucene_query("kerberoast")


@pytest.mark.parametrize(
    "non_latin",
    [
        "메모리에서 자격증명 추출",  # Korean
        "メモリから資格情報を抽出",  # Japanese
        "从内存中提取凭据",  # Chinese
        "1558 003",
    ],
)
def test_query_without_latin_letters_is_preserved(non_latin) -> None:
    assert f"name:{non_latin.split()[0]}^10" in _lucene_query(non_latin)


def test_mixed_script_query_keeps_its_latin_terms() -> None:
    out = _lucene_query("메모리에서 lsass dump 하기")
    assert "name:lsass^10" in out and "name:dump^10" in out


def test_field_weights_match_the_skill_intent_priority() -> None:
    out = _lucene_query("credential")
    assert "name:credential^10" in out
    assert "when_to_use:credential^8" in out
    assert "tags_raw:credential^6" in out
    assert "body:credential^1" in out


def test_query_is_bounded_to_sixteen_unique_terms() -> None:
    out = _lucene_query(" ".join(f"term{i}" for i in range(20)))
    assert "name:term15^10" in out
    assert "name:term16^10" not in out
    assert out.count(" OR ") == 127


def test_blank_query_with_no_filters_raises_actionable_error() -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    with pytest.raises(ValueError, match="at least one of"):
        be.find_skill(query="   ")


def test_blank_query_with_filters_is_labelled_structured(monkeypatch) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    row = {"name": "n", "path": "/p", "subdomain": "s", "description": "d"}
    monkeypatch.setattr(Neo4jBackend, "_find_lexical", lambda *a, **k: [dict(row)])

    hits = be.find_skill(query="   ", subdomain="credential-access")
    assert [h["matched_by"] for h in hits] == [["structured"]]


def test_unicode_query_reaches_semantic_search(monkeypatch) -> None:
    from decepticon.skillogy import embeddings
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    row = {"name": "credential-access", "path": "/credential", "score": 0.9}
    monkeypatch.setattr(Neo4jBackend, "_find_lexical", lambda *a, **k: [])
    monkeypatch.setattr(Neo4jBackend, "_find_semantic", lambda *a, **k: [dict(row)])
    monkeypatch.setattr(
        embeddings, "embed_text", lambda query: [0.1] if query == "자격증명 추출" else None
    )

    hits = be.find_skill(query="자격증명 추출")
    assert hits[0]["path"] == "/credential"
    assert hits[0]["matched_by"] == ["semantic"]


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
        assert "s.tags_raw" in create
        assert "s.body" in create


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
    assert "queryNodes($index_name, $lucene)" in cypher
    assert "WHERE s.subdomain = $subdomain AND ANY(" in cypher
    assert cypher.index("WHERE") < cypher.index("LIMIT $cand_n")
    assert "limit: $k" not in cypher


def test_fulltext_analyzer_rejects_unsafe_configuration(monkeypatch) -> None:
    from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

    be = Neo4jBackend.__new__(Neo4jBackend)
    monkeypatch.setenv(
        "DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER", "english'} MATCH (n) DETACH DELETE n"
    )
    with pytest.raises(ValueError, match="invalid DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER"):
        be.ensure_fulltext_index()
