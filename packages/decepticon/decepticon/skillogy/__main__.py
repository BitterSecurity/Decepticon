"""``python -m decepticon.skillogy`` — run the Skillogy server.

Phase 1a (Amendment v0.2.2) boot sequence:

1. Build a ``Neo4jBackend`` against the configured Bolt URI (waits for
   the graph to be reachable; the compose ``depends_on: neo4j (healthy)``
   gating means it should already be up).
2. Seed the CI-built ``skills.cypher`` dump into Neo4j **only when the
   graph is empty** — a fresh/first-boot database self-heals, and a
   persistent one (a managed Neo4j, or a compose volume) is left
   untouched instead of re-MERGEing the whole corpus on every boot.
   Corpus changes (a new SKILL.md) are an out-of-band incremental
   ingest, not a boot concern.
3. Start the FastAPI REST app on ``$SKILLOGY_REST_PORT``.

Environment variables:
  SKILLOGY_REST_PORT          (default 9100)
  SKILLOGY_NEO4J_URI          (default ``bolt://neo4j:7687``)
  SKILLOGY_NEO4J_USER         (default ``neo4j``)
  SKILLOGY_NEO4J_PASSWORD     (default ``decepticon-graph``)
  SKILLOGY_NEO4J_DATABASE     (default ``neo4j``; managed backends e.g. Aura
                              name the database after the instance id)
  SKILLOGY_CYPHER_PATH        (default ``/app/skills.cypher`` — baked into the image)
  SKILLOGY_API_KEY            (optional Bearer-token auth for the protected endpoints)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from decepticon.skillogy.server.app import build_app
from decepticon.skillogy.server.neo4j_backend import Neo4jBackend

log = logging.getLogger("skillogy")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # The Neo4j driver logs every cartesian-product hint at INFO during
    # bulk ingest — thousands of edge MERGEs against AssetType etc. trip
    # this. It's expected (the MERGE intentionally joins disconnected
    # nodes); silencing the notification logger keeps boot logs readable.
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


def _build_backend() -> Neo4jBackend:
    return Neo4jBackend(
        uri=os.environ.get("SKILLOGY_NEO4J_URI", "bolt://neo4j:7687"),
        user=os.environ.get("SKILLOGY_NEO4J_USER", "neo4j"),
        password=os.environ.get("SKILLOGY_NEO4J_PASSWORD", "decepticon-graph"),
        database=os.environ.get("SKILLOGY_NEO4J_DATABASE", "neo4j"),
    )


def _seed_if_empty(backend: Neo4jBackend) -> None:
    """Seed ``skills.cypher`` into Neo4j only when the graph is empty.

    The skill graph is persistent (a managed Neo4j, or a self-hosted
    instance with a data volume), so re-running the full corpus on every
    boot is wasted work — the builder's ``MERGE`` statements re-touch
    thousands of already-present nodes for no net change. Instead we seed
    once: a fresh/empty database self-heals on first boot, and a populated
    one is left untouched. Adding a skill is a separate incremental ingest
    run out of band (rebuild the cypher for the new SKILL.md and apply it),
    not something a boot re-derives.

    The seed is idempotent MERGE, so if two replicas race on a cold empty
    database the double-write is harmless.
    """
    if backend.health()["skill_count"] > 0:
        log.info("skill graph already populated; skipping seed")
        return
    cypher_path = Path(os.environ.get("SKILLOGY_CYPHER_PATH", "/app/skills.cypher"))
    if not cypher_path.exists():
        log.warning(
            "skills.cypher not found at %s; serving an empty graph until "
            "the operator ingests something else",
            cypher_path,
        )
        return
    cypher_text = cypher_path.read_text(encoding="utf-8")
    n = backend.bulk_ingest_cypher(cypher_text)
    log.info("seeded %d Cypher statements from %s", n, cypher_path)


def _start_rest(backend: Neo4jBackend, port: int, started_at: float) -> None:
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "Skillogy REST requires uvicorn. Install with: pip install uvicorn"
        ) from exc
    app = build_app(backend, started_at=started_at)
    # 0.0.0.0 is intentional: the Skillogy container is only exposed on
    # decepticon-net; docker-compose pins the host port to 127.0.0.1.
    config = uvicorn.Config(  # nosec B104
        app=app, host="0.0.0.0", port=port, log_level="info"
    )
    uvicorn.Server(config).run()


def _ingest_in_background(backend: Neo4jBackend) -> None:
    """Run the first-boot seed off the main thread.

    On a cold container with an empty database the bundled
    ``skills.cypher`` is ~3.7 MB and Neo4j MERGEs ~6000 statements over
    the bolt driver — that takes several minutes. Running it on the main
    thread blocks ``uvicorn.run()`` from binding the listener until the
    seed finishes, which leaves ``/v1/health`` unreachable and Docker's
    healthcheck flapping past ``start_period``. We move it to a daemon
    thread so the REST server comes up first and the healthcheck passes
    immediately; the corpus then loads in the background and the existing
    ``skill_count`` field in ``/v1/health`` reports its progress. When the
    graph is already populated the seed is a cheap no-op count check.
    """
    try:
        backend.ensure_fulltext_index()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "full-text index creation failed: %r — find_skill will fall back to "
            "substring matching and under-retrieve multi-word queries",
            exc,
        )

    try:
        _seed_if_empty(backend)
    except Exception as exc:  # noqa: BLE001
        # Failing to seed is loud but not fatal — the operator may
        # be running against a Neo4j that was pre-loaded out of band
        # (a different cypher file, or a manual seed). REST stays up
        # so health probes can report the situation.
        log.error("cypher seed failed: %r — continuing without it", exc)

    # Hybrid retrieval (ADR-0011): create the vector index and embed every
    # skill whose embedding-input text changed since the last boot. The dump is
    # deliberately embedding-free, so this is what makes semantic find_skill
    # possible; it degrades to a no-op when the proxy env is absent.
    #
    # This MUST run outside _seed_if_empty. It used to be the tail of that
    # function, so an ALREADY-POPULATED graph — the steady state for any
    # persistent Neo4j, i.e. every managed/AuraDB deployment — hit the
    # skill_count>0 early return and never reached the backfill. The corpus was
    # therefore never embedded and find_skill silently served substring-only
    # results forever. Embedding is content-hashed and idempotent, so running it
    # on every boot is cheap (a no-op on an unchanged corpus) and is what lets a
    # graph seeded before ADR-0011 — or one whose skills changed out of band via
    # an incremental ingest — catch up without a re-seed.
    try:
        from decepticon.skillogy.embed_ingest import ingest_embeddings  # noqa: PLC0415

        ingest_embeddings(backend)
    except Exception as exc:  # noqa: BLE001
        log.error("embedding backfill failed: %r — semantic find_skill unavailable", exc)


def main() -> int:
    _setup_logging()
    rest_port = int(os.environ.get("SKILLOGY_REST_PORT", "9100"))

    backend = _build_backend()
    started_at = time.time()

    # Start the bulk cypher ingest off the main thread before serving so
    # the REST listener comes up immediately. See ``_ingest_in_background``
    # for the rationale. The thread is a daemon so a SIGTERM during ingest
    # tears it down with the process.
    ingest_thread = threading.Thread(
        target=_ingest_in_background,
        args=(backend,),
        name="skillogy-ingest",
        daemon=True,
    )
    ingest_thread.start()

    def _handle_term(_signum, _frame):
        log.info("SIGTERM received; closing backend and exiting")
        try:
            backend.close()
        finally:
            sys.exit(0)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_term)

    _start_rest(backend, rest_port, started_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
