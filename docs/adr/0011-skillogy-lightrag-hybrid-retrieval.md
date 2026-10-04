# 0011. Skillogy find_skill uses LightRAG-style hybrid retrieval over the existing graph

- **Status:** Proposed
- **Date:** 2026-06-15 (revised 2026-06-16 after a code-review pass — embeddings
  moved from the checked-in dump to boot-ingest, MoC dropped from the embedding
  text, skillogy↔litellm coupling made explicit; deltas marked inline)
- **Deciders:** @PurpleCHOIms
- **Related:** ADR-0008 (skillogy hard ACL), `docs/design/skillogy-brain-redesign.md`, `docs/design/2026-06-12-skillogy-load-skill-by-name-pollution.md` (#670 load_skill exact-match), `packages/decepticon/decepticon/skillogy/server/neo4j_backend.py::find_skill`, `packages/decepticon/decepticon/skillogy/builder/` (ingest)

## Context

`find_skill` is the agent's entry point into the skill corpus (278 `:Skill`
nodes as of 2026-06-16, per `skills/.graph/skills.cypher`). Today its keyword
path is a **literal substring
match** on the whole query string:

```cypher
WHERE toLower(s.name) CONTAINS toLower($query)
   OR toLower(s.description) CONTAINS toLower($query)
   OR toLower(s.when_to_use) CONTAINS toLower($query)
```

This is a footgun, confirmed live on the 8x8 bug-bounty run-004
(2026-06-15):

| query | result |
|---|---|
| `redirect_uri` | 2 hits (`oauth`, `open-redirect`) ✅ |
| `oauth redirect_uri bypass` | **0 hits** ❌ |
| `redirect uri` (space) | **0 hits** ❌ |
| `oauth2` | **0 hits** ❌ |

The exact skills the agent wants **exist in the graph**, but any natural
multi-word query returns nothing because no single field contains that exact
phrase. There is no tokenization, no stemming, no semantic match, and no
ranking (`ORDER BY name` is alphabetical). Agents that phrase a rich query
get worse results than ones that guess a single magic keyword. The hunter
fleet hit this repeatedly.

The skill graph is **already curated** — `Skill` nodes linked to `Technique`
(`IMPLEMENTS`), `Tag` (`TAGGED`), `Phase` (`IN_PHASE`), and analyst `chains`.
The retrieval layer simply doesn't exploit it. This is not a "build a KG from
documents" problem (the KG exists, built deterministically by
`skillogy.builder` → `skills.cypher`); it is a "retrieve well over a curated
KG" problem.

A 2026-06-15 survey of the field (LightRAG [EMNLP'25, v1.5.3], GraphRAG-Bench
[ICLR'26]) confirms **hybrid graph retrieval** — LightRAG's dual-level
(local entity + global relationship) merge — as the strongest approach for
exactly this shape, and it maps cleanly onto our graph.

## Decision

Replace the substring keyword path in `find_skill` with **LightRAG-style
hybrid retrieval implemented directly on the existing skillogy graph** — NOT
by adopting the LightRAG library. The agent-facing API (`find_skill` /
`load_skill` / `traverse`, natural-language `query`) is unchanged; only the
backend retrieval changes.

### Why implement the algorithm, not adopt the library

| | LightRAG library (`lightrag-hku`) | Implement the algorithm in skillogy (CHOSEN) |
|---|---|---|
| Graph | builds its OWN KG via LLM entity extraction → conflicts with / duplicates our curated `Skill/Technique/Tag/Phase` schema | reuses the curated graph as-is |
| Ingest | "chunk → LLM-extract → embed" (non-deterministic, costly) | keeps the deterministic `builder` ingest; only ADDS an embedding step |
| ACL (ADR-0008) | per-role path-prefix scoping not native; must post-filter or patch internals | ACL stays exactly where it is, applied in the same Cypher |
| API contract | find_skill/load_skill/traverse would bend to LightRAG's API | 3-tool contract preserved |
| open-core footprint | heavy dep (networkx, nano-vectordb, its own storage abstraction) into OSS | only the `neo4j` driver + a litellm embedding call (see §"Skillogy↔litellm coupling") |
| Net effect | rebuild skillogy *around* LightRAG | add dual-level retrieval *onto* skillogy |

Our problem is retrieval over a curated KG; the library is optimized for
constructing a KG from documents. Impedance mismatch is high; the algorithm
is what we want.

### Architecture (dual-level hybrid, mapped to our graph)

**Ingest — embeddings computed at boot, NOT baked into the dump (revised
2026-06-16 after code review):**
- Embedding text per `:Skill` = `name + description + when_to_use` (the fields
  that actually exist on the node, confirmed `builder/skills.py:136-152`).
  **Note:** the original draft said "+ MoC summary", but there is **no
  `moc_summary` property on `:Skill`** — MoC is a separate per-phase `:MoC`
  node (`(:MoC)-[:BELONGS_TO_PHASE]->(:Phase)`, `neo4j_backend.py:300`). If we
  want MoC context in the vector, ingest must resolve `Skill→IN_PHASE→Phase←
  BELONGS_TO_PHASE←MoC` and concatenate; given the marginal recall benefit and
  the join cost, **MoC is dropped from the embedding text** for v1 (`subdomain`
  is already in `name`/`description` context).
- **Do NOT emit embeddings into the checked-in `skills.cypher`.** That dump is
  already git-tracked at 3.85 MB / ~12k lines; adding 278 × `EMBED_DIM` floats
  would bloat it several-fold on every rebuild, make the dump depend on a
  (potentially non-deterministic, version-drifting) embedding provider, and
  force CI to carry embedding-model credentials. Instead, compute embeddings
  **at boot-ingest**: after the skillogy service bulk-loads `skills.cypher`
  into Neo4j, it embeds each `:Skill` once and writes `s.embedding` directly to
  the graph (one ~278-vector pass, cached by content hash so restarts are
  free). The git dump stays embedding-free and reproducible without any
  embedding creds.
- Create a Neo4j **native vector index** on `:Skill(embedding)` after the embed
  pass. Deployed Neo4j is **`neo4j:5.26.28-community`** (`docker-compose.yml:117`),
  which supports native vector indexes.
- Embeddings via the **litellm gateway** (a cloud embedding model, e.g.
  `voyage-3` / `text-embedding-3-large` — same endpoint used at query time so
  ingest and query share a vector space). 278 skills → trivial one-time cost.

**Skillogy↔litellm coupling (new dependency — confirmed gap):**
- The skillogy service is currently **isolated from the LLM proxy**: its
  container env carries only `SKILLOGY_NEO4J_*` (`docker-compose.yml` skillogy
  service), and no skillogy code imports litellm or any embedding API. Only the
  `langgraph` service gets `DECEPTICON_LLM__PROXY_URL` /
  `DECEPTICON_LLM__PROXY_API_KEY` (`docker-compose.yml:375-376`).
- This ADR therefore **adds a runtime dependency**: the skillogy container must
  be given the proxy URL + key so both boot-ingest and query-time `find_skill`
  can embed. Thread `DECEPTICON_LLM__PROXY_URL` + `DECEPTICON_LLM__PROXY_API_KEY`
  (or a dedicated `DECEPTICON_SKILLOGY_EMBED_*`) into the skillogy service.
- **OSS / no-credentials posture:** when no embedding model is reachable, both
  boot-ingest embedding and query embedding are skipped and `find_skill`
  **runs the existing substring path** (the documented fallback below). Semantic
  search is an opt-in upgrade that activates only when an embedding model is
  wired; the default OSS experience is unchanged, not broken.

**Query (`find_skill` keyword path becomes hybrid):**
1. **local** — embed the query, `db.index.vector.queryNodes` top-k `:Skill`
   by cosine (semantic entity match — "oauth redirect_uri bypass" now finds
   `oauth` / `open-redirect` by meaning, not substring).
2. **global** — from those seed skills, graph-expand one hop over
   `IMPLEMENTS` / `TAGGED` / `IN_PHASE` / analyst `chains` to pull the
   related playbook cluster (relationship-level recall — e.g. a confirmed-XSS
   skill surfaces its `chains/xss-to-…` neighbours).
3. **hybrid merge + rerank** — fuse local + global (and, for exact tokens
   like a CVE id or tool name, a keyword signal) via reciprocal-rank fusion;
   rank by fused score. Replaces `ORDER BY name`.
4. **ACL** — the existing `allowed_path_prefixes` filter (ADR-0008) applies to
   the fused candidate set. **Reposition note:** today the ACL is one
   `WHERE … ANY(p IN $allowed_path_prefixes …)` clause appended before
   `ORDER BY name` (`neo4j_backend.py:270`). In the hybrid path the candidates
   come from `db.index.vector.queryNodes` + graph expansion, so the same
   path-prefix predicate must be re-applied over the fused set (e.g. a `WHERE`
   after the vector `YIELD`, or a post-fetch Python filter) — semantically
   unchanged, but it moves.

Facet filters (`subdomain`, `mitre_id`, `tag`, `tactic_id`) keep their exact
behaviour and continue to AND with the keyword path.

### Backward compatibility

- No agent prompt changes — agents still pass natural language to `query`.
- `find_skill` with no `query` (pure facet search) is unchanged.
- `load_skill` (exact path/name, #670) and `traverse` are untouched.
- If the vector index is absent (e.g. an old `skills.cypher` without
  embeddings), `find_skill` falls back to the current substring path so the
  service degrades gracefully rather than 500ing.

## Consequences

**Positive**
- Multi-word / semantic queries work; the curated graph is finally exploited
  for recall. Directly fixes the run-004 footgun.
- All existing guarantees preserved (ACL, deterministic ingest, 3-tool API,
  open-core boundary).

**Negative / costs**
- **New service dependency:** the skillogy container must now reach the litellm
  proxy (it currently cannot — see §"Skillogy↔litellm coupling"). Without it,
  `find_skill` silently runs the legacy substring path.
- Embeddings are computed **at boot-ingest into Neo4j**, not baked into
  `skills.cypher` (revised — see Ingest). This keeps the 3.85 MB git dump
  embedding-free (no bloat, no provider non-determinism in version control, no
  embedding creds in CI), at the cost of a one-time ~278-vector embed pass on
  first boot (cached by content hash; restarts are free).
- We own the retrieval/rerank code and must track RAG advances ourselves
  (the tradeoff accepted for control + zero schema conflict).
- A query-time embedding call is added to the `find_skill` hot path
  (one small embedding request; cache by query string).

## Implementation plan (for the implementing session)

Touch points, in dependency order. Each step is independently testable.

### Step 1 — embedding helper (new module)
`skillogy/embeddings.py` (new):
- `embed_text(text: str) -> list[float]` and `embed_batch(texts) -> list[list[float]]`,
  calling the **litellm gateway** (`DECEPTICON_SKILLOGY_EMBED_MODEL`, default chosen
  per Open Questions; base URL + key reuse the existing proxy env the agents use).
- A small on-disk cache keyed by `sha256(model + "\n" + text)` so re-builds and
  repeated queries don't re-spend. Used by BOTH ingest (Step 2) and query (Step 4).
- Returns the embedding dimension via a `EMBED_DIM` constant (drives the index DDL).

### Step 2 — boot-ingest: embed each skill into Neo4j (NOT into the dump)
- Do this in the **service boot path** (`skillogy/__main__.py`, after
  `_maybe_ingest` bulk-loads `skills.cypher`), not in `builder/skills.py`. The
  builder/`skills.cypher` stays embedding-free.
- After load: for each `:Skill` missing `s.embedding`, compute
  `embed_text(name + "\n" + description + "\n" + when_to_use)` and `SET
  s.embedding`. (No `moc_summary` — that field does not exist on `:Skill`;
  see Ingest note.) Cache by content hash so restarts don't re-embed.
- No `emit.py` / `builder/skills.py` change needed for embeddings. (`emit.py::
  cypher_literal` *does* already handle `list[float]` via `repr`, confirmed —
  only relevant if a future revision decides to bake them in.)

### Step 3 — vector index DDL (boot ingest)
- In the same boot path (a sibling `_ensure_indexes`): after the embed pass, run
  `CREATE VECTOR INDEX skill_embedding IF NOT EXISTS FOR (s:Skill) ON s.embedding
   OPTIONS {indexConfig: {`vector.dimensions`: EMBED_DIM, `vector.similarity_function`: 'cosine'}}`.
  Idempotent; the deployed `neo4j:5.26.28-community` supports native vector indexes.

### Step 4 — find_skill hybrid retrieval (backend)
`server/neo4j_backend.py::find_skill` — replace ONLY the keyword Path E
(the `query` CONTAINS block, `neo4j_backend.py:255-263` today) and the
`ORDER BY name` (line 282):
1. **local**: if `query`, `qvec = embed_text(query)`, then
   `CALL db.index.vector.queryNodes('skill_embedding', $k, $qvec) YIELD node AS s, score`
   for the top-k seeds (k ≈ 20).
2. **global**: one-hop expand from the seeds over the existing edges —
   `OPTIONAL MATCH (s)-[:IMPLEMENTS|TAGGED|IN_PHASE]-(:*)<-[...]-(rel:Skill)` plus
   the analyst `chains` relation — collect related `:Skill` nodes as additional
   candidates with a discounted score.
3. **fuse + rerank**: merge local + global (+ an optional exact-substring keyword
   signal for tokens like CVE ids / tool names) via reciprocal-rank fusion; order by
   fused score. This replaces `ORDER BY name`.
4. **ACL**: keep the existing `allowed_path_prefixes` filter (ADR-0008) over the
   fused candidate set — unchanged.
- **Fallback**: if the vector index is missing (old dump) or the embed call fails,
  fall back to the current substring path so the service never 500s.
- Facet filters (`subdomain`/`mitre_id`/`tag`/`tactic_id`) keep AND-ing as today.

### Step 5 — tests
- `tests/unit/skillogy/`: the run-004 footgun cases must pass —
  `"oauth redirect_uri bypass"`, `"redirect uri"`, `"oauth2"` all return the
  `oauth` / `open-redirect` skills; ranking puts the best match first; ACL still
  scopes; facet-only (no query) unchanged; missing-index fallback path covered.
- A `respx`/stub embedding so unit tests don't hit the network; one integration
  test (marked) against a live Neo4j + embedding for the real vector path.

### Step 6 — container env / image (NOT CI)
- Because embeddings are computed at boot (not in `skills.cypher`), **CI needs
  no embedding creds** and the builder is unchanged.
- Thread the proxy env into the **skillogy service** in `docker-compose.yml`
  (it currently has only `SKILLOGY_NEO4J_*`): add `DECEPTICON_LLM__PROXY_URL`
  + `DECEPTICON_LLM__PROXY_API_KEY` (mirroring the `langgraph` service,
  `docker-compose.yml:375-376`) and the downstream overlay. Absent → substring
  fallback (no failure).

## Open questions

- **Embedding model**: `voyage-3` vs `text-embedding-3-large` vs a code/security-tuned
  model — pick on retrieval quality over the security corpus. (Decision needed
  before Step 1; sets `EMBED_DIM`.)
- **Rerank fusion weights** (local vs global vs keyword) — tune on a small
  labelled query set drawn from real hunt logs.
- **Re-embedding cadence**: only changed skills need re-embedding; key the
  cache on a content hash in the builder (Step 1 cache covers this).
- **k and expansion fan-out**: top-k seeds (≈20) and one-hop vs two-hop global
  expansion — start conservative (k=20, one hop) and tune.

## Implementation update — 2026-10-04

The original fixed `skill_embedding` index cannot change dimensions through
`CREATE ... IF NOT EXISTS`. Skillogy now creates `skill_embedding_<dimension>`
over `embedding_<dimension>`, waits for it to become online, and backfills that
property before enabling semantic lookup. A model switch to a different
dimension therefore creates a separate index; an existing installation
re-embeds its skills once on upgrade. The old index and property are left in
place so upgrades do not delete data. If backfill fails or a provider returns
the wrong vector length, `find_skill` continues to use lexical results until
a later successful boot completes the backfill.
