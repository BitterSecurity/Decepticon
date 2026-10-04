"""Neo4j-backed storage for the skillogy service.

Replaces the in-memory ``SkillRegistry`` (deleted in Amendment v0.2.2
along with ``ingest.py`` — there were no live importers left after the
REST app was rewritten). The server opens a Bolt session to the Neo4j
instance that ``skillogy.builder`` populates via ``skills.cypher``.

The wire protocol is the new three-operation surface
(``find_skill`` / ``load_skill`` / ``traverse``) plus
``query_moc_summary`` — see ``server/app.py``.

Read-only enforcement
---------------------
Server-driven Cypher (``run_cypher_read`` RPC, Phase 1b-onwards
``recall``) is the obvious attack surface. The backend enforces three
defenses, layered: ``default_access_mode=READ`` on the Bolt session
(server-side), AST-style keyword denylist applied to the inbound
``query`` string (belt-and-suspenders), and a per-query parameter cap
+ row-count cap so a malformed query can't exhaust the agent context.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, LiteralString, cast

log = logging.getLogger(__name__)

_SEARCH_FIELDS: tuple[tuple[str, int], ...] = (
    ("name", 10),
    ("when_to_use", 8),
    ("tags_raw", 6),
    ("subdomain", 5),
    ("description", 4),
    ("allowed_tools", 3),
    ("mitre_attack_raw", 3),
    ("body", 1),
)
_SEARCH_TERMS_RE = re.compile(r"[^\W_][\w./+#-]*", flags=re.UNICODE)
_SEARCH_STOP_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_LUCENE_RESERVED = r'+-&|!(){}[]^"~*?:\/'
_LUCENE_ESCAPE_RE = re.compile("([" + re.escape(_LUCENE_RESERVED) + "])")


def _lucene_query(query: str | None) -> str:
    if not query:
        return ""
    terms = tuple(
        dict.fromkeys(
            term
            for match in _SEARCH_TERMS_RE.finditer(query.casefold())
            if (term := match.group().strip(".-/+#"))
            and len(term) >= 2
            and term not in _SEARCH_STOP_TERMS
        )
    )[:16]
    if not terms:
        return ""
    return " OR ".join(
        f"{field}:{_escape_lucene(term)}^{boost}"
        for field, boost in _SEARCH_FIELDS
        for term in terms
    )


def _escape_lucene(term: str) -> str:
    return _LUCENE_ESCAPE_RE.sub(r"\\\1", term)


# Write-mode Cypher keywords we refuse to forward, even though the
# Neo4j driver session is also opened in READ mode. The check is
# whole-word, case-insensitive, after stripping line comments + string
# literals so a benign body like "// MERGE example" cannot be flagged.
_WRITE_KEYWORDS = (
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "DETACH",
    "REMOVE",
    "DROP",
    "LOAD",
    "USING PERIODIC COMMIT",
    "FOREACH",
)


def _path_under_any_prefix(path: str | None, prefixes: list[str]) -> bool:
    """Return True iff ``path`` starts with any of the given prefixes.

    Used to enforce the per-role path-prefix ACL (ADR-0008). An empty
    or missing ``path`` (e.g. a non-``:Skill`` neighbour in a traverse
    result) is treated as "not gated" so the caller can apply the
    label-based skip itself; the empty-prefix list short-circuit lives
    at the call site.
    """
    if not path:
        return False
    return any(path.startswith(p) for p in prefixes)


class CypherWriteRejected(ValueError):
    """Raised when a client query trips the write-keyword denylist."""


_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_STRING_RE = re.compile(r"'([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\"")
_WORD_BOUNDARY = r"(?<![A-Za-z_])({kw})(?![A-Za-z_])"


def _strip_noise(query: str) -> str:
    """Drop line comments + string literals before keyword scanning."""
    no_comments = _LINE_COMMENT_RE.sub("", query)
    return _STRING_RE.sub("''", no_comments)


def assert_read_only(query: str) -> None:
    """Raise ``CypherWriteRejected`` if ``query`` contains a write keyword."""
    cleaned = _strip_noise(query)
    for kw in _WRITE_KEYWORDS:
        pattern = _WORD_BOUNDARY.format(kw=re.escape(kw))
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            raise CypherWriteRejected(
                f"Cypher write keyword {kw!r} is not allowed in read-only RPC"
            )


class Neo4jBackend:
    """Thin Bolt wrapper used by the FastAPI / grpcio server.

    Created once at server boot, shared across requests. Holds a single
    driver instance; sessions are short-lived (request-scoped). The
    driver is closed in ``close()`` so unit tests with testcontainers
    can tear it down deterministically.
    """

    def __init__(
        self,
        *,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
        max_rows: int = 200,
    ) -> None:
        try:
            from neo4j import GraphDatabase  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "Skillogy server requires the neo4j driver. Install with: pip install neo4j>=5.24"
            ) from exc
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database
        self._max_rows = max_rows
        self._fulltext_ready = False

    def close(self) -> None:
        self._driver.close()

    # ---- bulk cypher ingest (used by service boot to seed the graph) ----

    def bulk_ingest_cypher(self, cypher_text: str) -> int:
        """Execute ``cypher_text`` against Neo4j as a sequence of statements.

        The builder emits ``MERGE``-only statements, each terminated by
        ``;`` at end of line — naive splitting on ``;`` alone fragments
        any statement whose string property contains a semicolon (a
        common case: skill bodies and descriptions). Splitting on
        ``;\\n`` instead respects the emitter's contract and round-trips
        the dump cleanly. Idempotent re-runs are safe. Uses a write
        session because startup ingest is the one path that legitimately
        writes; runtime endpoints use read-only sessions.

        Returns the number of statements executed.
        """
        statements = [s.strip() for s in cypher_text.split(";\n") if s.strip()]
        with self._driver.session(database=self._database) as session:
            for stmt in statements:
                # Strip any trailing ``;`` left by the final-statement
                # edge case (the file ends in ``;`` without a newline).
                session.run(stmt.rstrip(";").rstrip())
        return len(statements)

    # ---- vector index + embedding backfill (hybrid retrieval, ADR-0011) ----

    # Native Neo4j vector index over the per-skill embedding. Created
    # idempotently at boot AFTER the cypher dump loads; embeddings are
    # then backfilled through the litellm proxy (the dump stays embedding-
    # free so a model swap is a re-embed, not a rebuild). The query side
    # (find_skill) uses this index for the semantic-local leg of the
    # hybrid score, and silently skips it when no embeddings exist.
    VECTOR_INDEX_NAME = "skill_embedding"

    def ensure_vector_index(self, dim: int) -> None:
        """Create the ``:Skill(embedding)`` vector index if absent (idempotent).

        ``dim`` is inlined as a literal — Neo4j does not accept a query
        parameter for ``vector.dimensions`` in index DDL. It is coerced to
        ``int`` first so the f-string cannot carry an injection.
        """
        dim_literal = int(dim)
        cypher = (
            f"CREATE VECTOR INDEX {self.VECTOR_INDEX_NAME} IF NOT EXISTS "
            "FOR (s:Skill) ON (s.embedding) "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {dim_literal}, "
            "`vector.similarity_function`: 'cosine'}}"
        )
        with self._driver.session(database=self._database) as session:
            session.run(cypher)

    # Native Neo4j full-text (Lucene/BM25) index backing the term-search leg.
    FULLTEXT_INDEX_NAME = "skill_text"

    DEFAULT_FULLTEXT_ANALYZER = "english"

    def ensure_fulltext_index(self) -> None:
        """Create the ``:Skill`` full-text index if absent (idempotent).

        Indexes the Skill metadata and body fields used by the SaaS search
        router. Query-time boosts favor names and curated trigger vocabulary;
        body text supplies low-weight recall.

        NOTE ON CHANGING THE ANALYZER: ``IF NOT EXISTS`` matches on the index's
        SCHEMA (label + properties), not its name, so once an index exists this
        call is a no-op and a changed analyzer is silently ignored — the same
        trap as changing embedding dimensions against ``ensure_vector_index``.
        We detect that case and warn rather than dropping the operator's index
        out from under them; applying it needs an explicit
        ``DROP INDEX skill_text`` followed by a restart.
        """
        analyzer = (
            os.environ.get("DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER", "").strip()
            or self.DEFAULT_FULLTEXT_ANALYZER
        )
        if re.fullmatch(r"[A-Za-z0-9_.-]+", analyzer) is None:
            raise ValueError("invalid DECEPTICON_SKILLOGY_FULLTEXT_ANALYZER")
        # Validate against the server's own list rather than a local
        # enumeration, so this never drifts from what Neo4j supports.
        available = self._available_fulltext_analyzers()
        if available and analyzer not in available:
            log.warning(
                "full-text analyzer %r is not offered by this Neo4j (available: %s); "
                "falling back to the server default",
                analyzer,
                ", ".join(sorted(available)),
            )
            analyzer = ""

        existing = self._existing_fulltext_analyzer()
        if existing is not None:
            if analyzer and existing != analyzer:
                log.warning(
                    "full-text index '%s' already exists with analyzer %r, but %r is "
                    "configured. CREATE ... IF NOT EXISTS matches on schema, not name, "
                    "so this is a no-op. To apply it: DROP INDEX %s and restart.",
                    self.FULLTEXT_INDEX_NAME,
                    existing,
                    analyzer,
                    self.FULLTEXT_INDEX_NAME,
                )
            self._await_fulltext_index()
            self._fulltext_ready = True
            return

        options = (
            f"OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{analyzer}'}}}}" if analyzer else ""
        )
        properties = ", ".join(f"s.{field}" for field, _ in _SEARCH_FIELDS)
        cypher = (
            f"CREATE FULLTEXT INDEX {self.FULLTEXT_INDEX_NAME} IF NOT EXISTS "
            f"FOR (s:Skill) ON EACH [{properties}] "
            f"{options}"
        ).strip()
        with self._driver.session(database=self._database) as session:
            session.run(cast(LiteralString, cypher)).consume()
        self._await_fulltext_index()
        self._fulltext_ready = True
        log.info(
            "created full-text index '%s' (analyzer: %s)",
            self.FULLTEXT_INDEX_NAME,
            analyzer or "server default",
        )

    def _await_fulltext_index(self) -> None:
        with self._driver.session(database=self._database) as session:
            session.run(
                "CALL db.awaitIndex($name, $timeout_seconds)",
                name=self.FULLTEXT_INDEX_NAME,
                timeout_seconds=300,
            ).consume()

    # Both helpers below catch ``Neo4jError`` rather than ``Exception`` on
    # purpose: a server that cannot answer the probe is a tolerable degradation
    # (we fall back to the server-default analyzer / attempt the DDL and let
    # Neo4j reject an invalid one), but a KeyError or AttributeError here is OUR
    # bug and must surface. A blanket catch previously hid exactly that — this
    # procedure yields a column named ``analyzer``, the code read ``name``, and
    # the resulting KeyError was swallowed into "no analyzers available", which
    # silently rejected every configured analyzer.

    def _available_fulltext_analyzers(self) -> set[str]:
        """Analyzer names this server offers, or an empty set if unreachable."""
        from neo4j.exceptions import Neo4jError  # noqa: PLC0415 — lazy, see __init__

        try:
            with self._driver.session(
                database=self._database, default_access_mode="READ"
            ) as session:
                return {
                    record["analyzer"]
                    for record in session.run(
                        "CALL db.index.fulltext.listAvailableAnalyzers() YIELD analyzer "
                        "RETURN analyzer"
                    )
                }
        except Neo4jError as exc:
            log.debug("could not list full-text analyzers: %s", exc)
            return set()

    def _existing_fulltext_analyzer(self) -> str | None:
        """The analyzer of the existing full-text index, or ``None`` if absent.

        Returns ``""`` when the index exists but names no analyzer, so callers
        can still distinguish "exists" from "absent".
        """
        from neo4j.exceptions import Neo4jError  # noqa: PLC0415 — lazy, see __init__

        try:
            with self._driver.session(
                database=self._database, default_access_mode="READ"
            ) as session:
                record = session.run(
                    "SHOW INDEXES YIELD name, type, options "
                    "WHERE name = $name AND type = 'FULLTEXT' "
                    "RETURN options AS options",
                    name=self.FULLTEXT_INDEX_NAME,
                ).single()
        except Neo4jError as exc:
            log.debug("could not inspect full-text index: %s", exc)
            return None
        if record is None:
            return None
        options = record.get("options") or {}
        config = options.get("indexConfig") or {} if isinstance(options, dict) else {}
        value = config.get("fulltext.analyzer") if isinstance(config, dict) else None
        return str(value) if value else ""

    def fetch_skills_for_embedding(self) -> list[dict[str, Any]]:
        """Return the text fields each skill is embedded from, plus the sha of
        the input that produced its current embedding (``None`` if never
        embedded). The caller re-embeds only rows whose recomputed input sha
        differs — so a content edit re-embeds, an unchanged corpus is a no-op.
        """
        cypher = (
            "MATCH (s:Skill) "
            "RETURN s.path AS path, s.name AS name, "
            "       coalesce(s.description, '') AS description, "
            "       coalesce(s.when_to_use, '') AS when_to_use, "
            "       s.embedding_input_sha256 AS embedding_input_sha256"
        )
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            return [dict(record) for record in session.run(cypher)]

    def write_embeddings(self, rows: list[dict[str, Any]]) -> int:
        """Persist embeddings onto their ``:Skill`` nodes.

        Each row is ``{"path": str, "vector": list[float], "sha": str}``.
        Uses ``db.create.setNodeVectorProperty`` (the supported way to store a
        vector so the index picks it up) and records the input sha so the next
        boot can skip unchanged skills. Returns the number of nodes written.
        """
        if not rows:
            return 0
        cypher = (
            "UNWIND $rows AS row "
            "MATCH (s:Skill {path: row.path}) "
            "CALL db.create.setNodeVectorProperty(s, 'embedding', row.vector) "
            "SET s.embedding_input_sha256 = row.sha "
            "RETURN count(s) AS written"
        )
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, rows=rows).single()
        return 0 if result is None else int(result["written"])

    # ---- skill ops ----

    def load_skill(
        self,
        path: str,
        *,
        allowed_path_prefixes: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one ``:Skill`` node by canonical path OR unique frontmatter
        name. Returns its full property dict, or ``None`` if no such skill
        exists.

        Agents routinely pass a skill ``name`` (the field ``find_skill``
        surfaces most prominently — e.g. ``load_skill("oauth")``), not the
        ``/skills/.../SKILL.md`` path. A path-only match silently returned
        ``None`` for every name, which forced a fragile client-side
        ``find_skill`` fallback that the mixed APT + web skill corpus
        pollutes (``"oauth"``/``"ssrf"`` never resolved while ``"sqli"`` did,
        purely by which name happened to win the polluted keyword search).
        Match by exact ``path`` first (always unique), then fall back to an
        exact ``name`` match.

        When ``allowed_path_prefixes`` is non-empty, the **resolved** skill's
        path must be under a listed prefix, else ``None`` (the same shape the
        agent sees for a genuinely missing skill — ADR-0008). The check is
        applied AFTER resolution: a bare name has no prefix of its own, so
        gating on the *input* would reject every name-based load. ``None`` /
        empty list preserves the unrestricted behaviour for the standalone
        library, the skillogy CLI, and pytest, where no role context exists.
        """
        query = (
            "MATCH (s:Skill) WHERE s.path = $arg OR s.name = $arg "
            "RETURN properties(s) AS props "
            "ORDER BY CASE WHEN s.path = $arg THEN 0 ELSE 1 END "
            "LIMIT 1"
        )
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            result = session.run(query, arg=path).single()
        if result is None:
            return None
        props = dict(result["props"])
        if allowed_path_prefixes and not _path_under_any_prefix(
            str(props.get("path", "")), allowed_path_prefixes
        ):
            return None
        return props

    def health(self) -> dict[str, Any]:
        """Return service liveness + a count of :Skill nodes in the graph."""
        query = "MATCH (s:Skill) RETURN count(s) AS skill_count"
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            result = session.run(query).single()
        skill_count = 0 if result is None else int(result["skill_count"])
        return {"status": "ok", "skill_count": skill_count}

    # ---- relationship-aware search (used by find_skill RPC) ----

    def find_skill(
        self,
        *,
        query: str | None = None,
        subdomain: str | None = None,
        mitre_id: str | None = None,
        tag: str | None = None,
        tactic_id: str | None = None,
        limit: int = 20,
        allowed_path_prefixes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Relationship-aware skill discovery — hybrid retrieval (ADR-0011).

        The structured filters are hard AND constraints; each prunes the
        candidate set via a different edge:

        - ``subdomain``: ``(s:Skill)-[:IN_PHASE]->(:Phase {name: $sub})``.
        - ``mitre_id``: ``(s:Skill)-[:IMPLEMENTS]->(:Technique {id: $id})``
          where ``$id`` can be a top-level T1xxx or a sub-T1xxx.yyy.
        - ``tag``: ``(s:Skill)-[:TAGGED]->(:Tag {name: $tag})``.
        - ``tactic_id``: anchors on a Tactic, follows ``HAS_TECHNIQUE`` to
          its techniques, then back to skills via ``IMPLEMENTS``.

        ``query`` (free text) drives **ranking** via two legs fused with
        reciprocal-rank fusion:

        - *lexical* — field-weighted term search over Skill metadata and body.
          Names and ``when_to_use`` triggers carry the strongest weights;
          body text provides lower-weight recall.
        - *semantic* — cosine k-NN over the per-skill embedding vector
          index, so a term nobody wrote in ``when_to_use`` can still find
          the skill.

        The semantic leg is **opt-in**: when no embeddings exist (the
        litellm proxy is unconfigured, or the corpus was never embedded),
        ``find_skill`` returns the pure term-search result. That path is
        self-sufficient — semantic retrieval improves recall on unanticipated
        vocabulary, it is not a prerequisite for intent-shaped queries. The
        structured-only path (no ``query``) is unchanged and name-ordered.

        Returns each match's ``name``, ``path``, ``subdomain``,
        ``description``, the matched dimensions (``matched_mitre``,
        ``matched_tags``) and ``matched_by`` — which retrieval legs returned
        the row (``["lexical"]``, ``["semantic"]``, both, or
        ``["structured"]`` for a filter-only query) — so the agent can see
        *why* a skill came back and how strongly. ``matched_by`` is also the
        degradation signal: a free-text query whose every hit says only
        ``["lexical"]`` means the semantic leg did not run.
        """
        from decepticon.skillogy import embeddings  # noqa: PLC0415

        limit = int(min(max(limit, 1), 100))
        # Over-fetch from each leg so reciprocal-rank fusion has signal to
        # work with before the final top-``limit`` cut.
        cand_n = min(max(limit * 3, 30), 100)

        # Structured (edge) filters — shared verbatim by both legs.
        structured: list[str] = []
        shared: dict[str, Any] = {}
        if subdomain:
            structured.append("(s)-[:IN_PHASE]->(:Phase {name: $subdomain})")
            shared["subdomain"] = subdomain
        if tag:
            structured.append("(s)-[:TAGGED]->(:Tag {name: $tag})")
            shared["tag"] = tag
        if mitre_id:
            structured.append("(s)-[:IMPLEMENTS]->(:Technique {id: $mitre_id})")
            shared["mitre_id"] = mitre_id
        if tactic_id:
            structured.append(
                "(s)-[:IMPLEMENTS]->(:Technique)<-[:HAS_TECHNIQUE]-(:Tactic {id: $tactic_id})"
            )
            shared["tactic_id"] = tactic_id
        # Per-role path-prefix ACL (ADR-0008) — applied identically to both
        # legs. Empty/missing leaves both unrestricted (CLI / pytest / lib).
        acl_clause: str | None = None
        if allowed_path_prefixes:
            acl_clause = "ANY(p IN $allowed_path_prefixes WHERE s.path STARTS WITH p)"
            shared["allowed_path_prefixes"] = list(allowed_path_prefixes)

        if not _lucene_query(query) and not structured:
            raise ValueError(
                "find_skill requires at least one of: query, subdomain, mitre_id, tag, tactic_id"
            )

        searchable = bool(_lucene_query(query))

        lexical = self._find_lexical(query, structured, acl_clause, shared, cand_n)

        # Semantic leg only when there is searchable free text AND it embeds.
        # Gated on ``searchable`` rather than ``query`` so an unusable query does
        # not spend an embedding round-trip to score nothing.
        query_vec = embeddings.embed_text(query) if searchable and query is not None else None
        if query_vec is None:
            # Degraded (or structured-only) path. Label it so the caller can
            # tell "semantic found nothing" from "semantic never ran" — a
            # searchable query returning only ``["lexical"]`` hits is the
            # signal that embeddings are unavailable.
            leg = ["lexical"] if searchable else ["structured"]
            return [{**rec, "matched_by": list(leg)} for rec in lexical[:limit]]

        semantic = self._find_semantic(query_vec, structured, acl_clause, shared, cand_n)
        return self._rrf_fuse(lexical, semantic, limit)

    # Shared RETURN tail so the lexical and semantic legs yield identical row
    # shapes (``score`` is appended by the semantic leg only and stripped at
    # fusion time).
    _ENRICH_TAIL = (
        "OPTIONAL MATCH (s)-[:IMPLEMENTS]->(t:Technique) OPTIONAL MATCH (s)-[:TAGGED]->(tg:Tag) "
    )
    _RETURN_FIELDS = (
        "s.name AS name, s.path AS path, s.subdomain AS subdomain, "
        "s.description AS description, matched_mitre, matched_tags"
    )

    def _find_lexical(
        self,
        query: str | None,
        structured: list[str],
        acl_clause: str | None,
        shared: dict[str, Any],
        cand_n: int,
    ) -> list[dict[str, Any]]:
        """Full-text (BM25) + structured leg, relevance-ordered.

        Free text goes to the ``skill_text`` full-text index over the Skill
        metadata and body fields. Lucene applies analyzer matching and BM25;
        query-time field boosts prioritize names and usage triggers.

        This replaced a whole-query ``CONTAINS`` predicate. That clause matched
        the ENTIRE query string as one substring, so any multi-word query — i.e.
        exactly what an agent produces when asked to describe an objective —
        matched nothing and this leg returned zero rows, leaving retrieval
        wholly dependent on the optional embedding leg.

        Structured filters and the ACL are applied as post-filters, mirroring
        ``_find_semantic``: an index-backed procedure call cannot pre-apply
        them, so we over-fetch and prune. The structured-only path (no
        ``query``) does not touch the index and keeps its legacy name-ordering.
        """
        params = dict(shared)
        params["cand_n"] = cand_n
        lucene = _lucene_query(query)

        if lucene and not getattr(self, "_fulltext_ready", False):
            return self._find_lexical_substring_fallback(
                query, structured, acl_clause, shared, cand_n
            )

        if not lucene:
            wheres = list(structured)
            if acl_clause:
                wheres.append(acl_clause)
            cypher = (
                "MATCH (s:Skill) "
                f"WHERE {' AND '.join(wheres)} "
                f"{self._ENRICH_TAIL}"
                "WITH s, collect(DISTINCT t.id) AS matched_mitre, "
                "     collect(DISTINCT tg.name) AS matched_tags "
                f"RETURN {self._RETURN_FIELDS} "
                "ORDER BY name "
                "LIMIT $cand_n"
            )
            with self._driver.session(
                database=self._database, default_access_mode="READ"
            ) as session:
                return [
                    dict(record)
                    for record in session.run(cast(LiteralString, cypher), parameters=params)
                ]

        post_filters = list(structured)
        if acl_clause:
            post_filters.append(acl_clause)
        params.update({"index_name": self.FULLTEXT_INDEX_NAME, "lucene": lucene})
        where = f"WHERE {' AND '.join(post_filters)} " if post_filters else ""
        cypher = (
            "CALL db.index.fulltext.queryNodes($index_name, $lucene) "
            "YIELD node AS s, score "
            f"{where}"
            f"{self._ENRICH_TAIL}"
            "WITH s, score, collect(DISTINCT t.id) AS matched_mitre, "
            "     collect(DISTINCT tg.name) AS matched_tags "
            f"RETURN {self._RETURN_FIELDS} "
            "ORDER BY score DESC, name "
            "LIMIT $cand_n"
        )
        from neo4j.exceptions import ClientError  # noqa: PLC0415 — lazy, see __init__

        try:
            with self._driver.session(
                database=self._database, default_access_mode="READ"
            ) as session:
                return [
                    dict(record)
                    for record in session.run(cast(LiteralString, cypher), parameters=params)
                ]
        except ClientError as exc:
            # Narrow on purpose: the only ClientError this call can raise that we
            # can act on is "no such fulltext schema index" — the index is created
            # at boot (``ensure_fulltext_index``), so this means an older graph or
            # a failed DDL. Anything else (auth, syntax, server state) is a real
            # fault and must propagate. Degrade to the pre-index predicate so the
            # agent still gets keyword hits, and say why.
            if "no such fulltext schema index" not in str(exc).lower():
                raise
            log.warning(
                "full-text index '%s' is missing; falling back to substring matching. "
                "Multi-word queries will under-retrieve until it exists — restart "
                "skillogy to create it.",
                self.FULLTEXT_INDEX_NAME,
            )
            return self._find_lexical_substring_fallback(
                query, structured, acl_clause, shared, cand_n
            )

    def _find_lexical_substring_fallback(
        self,
        query: str | None,
        structured: list[str],
        acl_clause: str | None,
        shared: dict[str, Any],
        cand_n: int,
    ) -> list[dict[str, Any]]:
        """Pre-index behaviour: whole-query substring match, name-ordered.

        Only reached when the full-text index is missing. Retained purely so a
        missing index degrades instead of erroring; it is NOT the intended path
        and under-retrieves any multi-word query by construction.
        """
        wheres = list(structured)
        params = dict(shared)
        params["cand_n"] = cand_n
        params["query"] = query
        wheres.append(
            "(toLower(s.name) CONTAINS toLower($query) "
            "OR toLower(coalesce(s.description, '')) CONTAINS toLower($query) "
            "OR toLower(coalesce(s.when_to_use, '')) CONTAINS toLower($query))"
        )
        if acl_clause:
            wheres.append(acl_clause)
        cypher = (
            "MATCH (s:Skill) "
            f"WHERE {' AND '.join(wheres)} "
            f"{self._ENRICH_TAIL}"
            "WITH s, collect(DISTINCT t.id) AS matched_mitre, "
            "     collect(DISTINCT tg.name) AS matched_tags "
            f"RETURN {self._RETURN_FIELDS} "
            "ORDER BY name "
            "LIMIT $cand_n"
        )
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            return [dict(record) for record in session.run(cypher, parameters=params)]

    def _find_semantic(
        self,
        query_vec: list[float],
        structured: list[str],
        acl_clause: str | None,
        shared: dict[str, Any],
        cand_n: int,
    ) -> list[dict[str, Any]]:
        """Vector k-NN leg over the ``:Skill(embedding)`` index, score-ordered.

        ``db.index.vector.queryNodes`` cannot pre-apply the structured /
        ACL predicates, so we over-fetch ``k`` neighbours and filter after —
        ``k`` is widened when filters are present since many neighbours will
        be dropped.
        """
        post_filters = list(structured)
        if acl_clause:
            post_filters.append(acl_clause)
        k = min(cand_n * 5, 500) if post_filters else cand_n
        params = dict(shared)
        params.update(
            {"index_name": self.VECTOR_INDEX_NAME, "k": k, "qvec": query_vec, "cand_n": cand_n}
        )
        where = f"WHERE {' AND '.join(post_filters)} " if post_filters else ""
        cypher = (
            "CALL db.index.vector.queryNodes($index_name, $k, $qvec) "
            "YIELD node AS s, score "
            f"{where}"
            f"{self._ENRICH_TAIL}"
            "WITH s, score, collect(DISTINCT t.id) AS matched_mitre, "
            "     collect(DISTINCT tg.name) AS matched_tags "
            f"RETURN {self._RETURN_FIELDS}, score "
            "ORDER BY score DESC "
            "LIMIT $cand_n"
        )
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            return [dict(record) for record in session.run(cypher, parameters=params)]

    @staticmethod
    def _rrf_fuse(
        lexical: list[dict[str, Any]],
        semantic: list[dict[str, Any]],
        limit: int,
        *,
        rrf_k: int = 60,
    ) -> list[dict[str, Any]]:
        """Reciprocal-rank fusion of the two ranked legs, keyed by skill path.

        Each leg contributes ``1 / (rrf_k + rank)`` to a skill's score; a
        skill found by both legs ranks above one found by either alone. Ties
        break on name for deterministic output. ``rrf_k`` is the standard
        rank-smoothing constant (60).

        Each row carries ``matched_by`` — the legs that returned it, in leg
        order. The raw vector ``score`` is still stripped (it is not
        comparable across queries); ``matched_by`` is the part the agent can
        actually reason with: a hit found by both legs is a stronger match
        than one found by either alone.
        """
        scores: dict[str, float] = {}
        record_by_path: dict[str, dict[str, Any]] = {}
        legs_by_path: dict[str, list[str]] = {}
        for leg_name, leg in (("lexical", lexical), ("semantic", semantic)):
            for rank, rec in enumerate(leg):
                path = rec["path"]
                record_by_path.setdefault(path, rec)
                legs_by_path.setdefault(path, []).append(leg_name)
                scores[path] = scores.get(path, 0.0) + 1.0 / (rrf_k + rank + 1)
        ordered = sorted(
            record_by_path.values(),
            key=lambda r: (-scores[r["path"]], r.get("name") or ""),
        )
        return [
            {
                **{k: v for k, v in r.items() if k != "score"},
                "matched_by": legs_by_path[r["path"]],
            }
            for r in ordered[:limit]
        ]

    # ---- per-phase MoC summary (used by SkillogyMiddleware system prompt) ----

    def query_moc_summary(self, phase: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return MoCs belonging to ``phase``, ordered by name.

        Each row carries ``name``, ``description`` (empty string when
        the MoC has none), and ``parent_phase``. Returns an empty list
        when the phase has no MoCs registered yet — some Phase nodes
        are placeholders until corpus coverage catches up, and the
        caller renders a "no MoCs yet" line instead of a bullet list.
        """
        cypher = (
            "MATCH (m:MoC)-[:BELONGS_TO_PHASE]->(:Phase {name: $phase}) "
            "RETURN m.name AS name, "
            "       coalesce(m.description, '') AS description, "
            "       coalesce(m.parent_phase, $phase) AS parent_phase "
            "ORDER BY name "
            "LIMIT $limit"
        )
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            return [
                dict(record)
                for record in session.run(
                    cypher,
                    parameters={"phase": phase, "limit": int(min(max(limit, 1), 100))},
                )
            ]

    # ---- explicit graph traversal (used by traverse RPC) ----

    def traverse(
        self,
        from_path: str,
        edge_types: list[str] | None = None,
        depth: int = 2,
        *,
        allowed_path_prefixes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Variable-length BFS from a Skill node along whitelisted edge types.

        Returns the neighbouring nodes flattened, each with its
        ``label``, key identifier, depth from the seed, and a string
        representation of the connecting edge type.

        When ``allowed_path_prefixes`` is non-empty (ADR-0008), the seed
        path must match a listed prefix or the call returns an empty
        list; ``:Skill`` neighbours that fall outside the allowlist are
        filtered from the result. Non-``:Skill`` neighbours (``:Tag``,
        ``:Technique``, ``:Tactic``, ``:MoC``) stay visible because
        they are classification metadata, not skill content.
        """
        if allowed_path_prefixes and not _path_under_any_prefix(from_path, allowed_path_prefixes):
            return []
        depth = max(1, min(int(depth), 5))
        # Default edge whitelist mirrors the spec §5.7.2 list.
        whitelist = edge_types or [
            "IN_PHASE",
            "IMPLEMENTS",
            "TAGGED",
            "BELONGS_TO",
            "RELATED_TO",
            "HAS_TECHNIQUE",
            "HAS_SUBTECHNIQUE",
        ]
        # Cypher relationship pattern: ``[r:A|B|C*1..N]``.
        rel_pattern = f"[r:{'|'.join(whitelist)}*1..{depth}]"
        cypher = (
            "MATCH (seed:Skill {path: $from_path}) "
            f"MATCH path = (seed)-{rel_pattern}-(neighbour) "
            "RETURN labels(neighbour) AS labels, "
            "       coalesce(neighbour.name, neighbour.id, neighbour.path) AS key, "
            "       neighbour.path AS neighbour_path, "
            "       length(path) AS hop_depth, "
            "       [rel IN relationships(path) | type(rel)] AS edge_chain "
            "LIMIT $cap"
        )
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            rows: list[dict[str, Any]] = []
            for rec in session.run(
                cypher,
                parameters={"from_path": from_path, "cap": self._max_rows},
            ):
                labels = list(rec["labels"])
                # Per ADR-0008 — drop ``:Skill`` neighbours that fall
                # outside the role's path-prefix allowlist. Non-Skill
                # neighbours (Tag/Technique/Tactic/MoC) are classification
                # metadata, not skill content, and stay visible so the
                # agent can still pivot via shared graph structure.
                if (
                    allowed_path_prefixes
                    and "Skill" in labels
                    and not _path_under_any_prefix(rec["neighbour_path"], allowed_path_prefixes)
                ):
                    continue
                rows.append(
                    {
                        "labels": labels,
                        "key": rec["key"],
                        "depth": int(rec["hop_depth"]),
                        "edge_chain": list(rec["edge_chain"]),
                    }
                )
            return rows

    # ---- read-only cypher escape hatch (used by run_cypher_read RPC, Phase 1a) ----

    def run_cypher_read(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute an agent-supplied read-only Cypher query.

        ``assert_read_only`` is the syntactic guard; the Bolt session's
        ``default_access_mode='READ'`` is the server-side guard. Results
        are capped at ``self._max_rows`` so a runaway query cannot exhaust
        the agent context window or wire bandwidth.
        """
        assert_read_only(query)
        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result.fetch(self._max_rows)]
