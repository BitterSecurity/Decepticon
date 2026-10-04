"""Boot-time embedding backfill for skillogy hybrid retrieval (ADR-0011).

Runs once per container boot, AFTER the ``skills.cypher`` dump has been loaded
(see ``__main__._seed_if_empty``). It creates the vector index and embeds every
skill whose embedding-input text changed since the last boot — so the dump
stays embedding-free (a model swap is a re-embed, not a rebuild) and an
unchanged corpus is a no-op.

Degrades silently: when the litellm proxy is not configured the whole step is
skipped and ``find_skill`` keeps using full-text lexical search.
"""

from __future__ import annotations

import hashlib
import logging

from decepticon.skillogy import embeddings
from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

log = logging.getLogger("skillogy")

# Embed in chunks so a 278-skill corpus is a few proxy round-trips, not one
# giant request (and a transient failure loses only one chunk).
_CHUNK_SIZE = 64


def build_embed_text(name: str, description: str, when_to_use: str) -> str:
    """The text a skill is embedded from.

    Per ADR-0011 the embedding signal is ``name`` + ``description`` +
    ``when_to_use`` — the fields an agent's natural-language query actually
    rhymes with. The skill ``body`` is deliberately excluded (too long, dilutes
    the signal) and so is the MoC summary (it lives on a different node and is
    rendered separately in the system prompt).
    """
    parts = [p.strip() for p in (name, description, when_to_use) if p and p.strip()]
    return "\n".join(parts)


def _input_sha(model: str, text: str) -> str:
    """SHA of the embedding *input identity* — the model PLUS the text.

    Folding the model in means swapping ``DECEPTICON_SKILLOGY_EMBED_MODEL``
    (even to one with the same dimension) changes every sha, so the next boot
    re-embeds the whole corpus instead of silently serving vectors from the
    old model. Mirrors the disk cache key in ``embeddings._cache_key``.
    """
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def ingest_embeddings(backend: Neo4jBackend) -> dict[str, int]:
    """Create the vector index and backfill changed/missing skill embeddings.

    Returns a small stats dict (``{"embedded": n, "skipped": m, "failed": k}``)
    for the boot log. Never raises — a failed backfill leaves semantic search
    disabled for this boot, and ``find_skill`` uses full-text lexical results.
    """
    if not embeddings.available():
        log.info("skillogy embeddings unavailable (no litellm proxy env); skipping vector ingest")
        return {"embedded": 0, "skipped": 0, "failed": 0}

    dim = embeddings.embed_dim()
    backend.ensure_vector_index(dim)

    model = embeddings.embed_model()
    rows = backend.fetch_skills_for_embedding(dim)
    pending: list[tuple[str, str, str]] = []  # (path, text, sha)
    skipped = 0
    for row in rows:
        text = build_embed_text(row["name"], row["description"], row["when_to_use"])
        if not text:
            skipped += 1
            continue
        sha = _input_sha(model, text)
        if row.get("embedded") and row.get("embedding_input_sha256") == sha:
            skipped += 1
            continue
        pending.append((row["path"], text, sha))

    embedded = 0
    failed = 0
    for start in range(0, len(pending), _CHUNK_SIZE):
        chunk = pending[start : start + _CHUNK_SIZE]
        vectors = embeddings.embed_batch([text for _, text, _ in chunk])
        writeback: list[dict[str, object]] = []
        for (path, _text, sha), vec in zip(chunk, vectors, strict=True):
            if vec is None:
                failed += 1
                continue
            if len(vec) != dim:
                log.warning(
                    "skillogy embedding for %s has %d dimensions, expected %d", path, len(vec), dim
                )
                failed += 1
                continue
            writeback.append({"path": path, "vector": vec, "sha": sha})
        if writeback:
            written = backend.write_embeddings(writeback, dim)
            embedded += written
            failed += len(writeback) - written

    if failed == 0:
        backend.mark_vector_ready(dim)

    log.info(
        "skillogy embedding ingest: %d embedded, %d unchanged, %d failed",
        embedded,
        skipped,
        failed,
    )
    return {"embedded": embedded, "skipped": skipped, "failed": failed}
