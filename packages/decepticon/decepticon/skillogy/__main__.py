"""``python -m decepticon.skillogy`` — run the Skillogy server.

Boot sequence:

1. Build a ``Neo4jBackend`` against the configured Bolt URI (waits for
   the graph to be reachable; the compose ``depends_on: neo4j (healthy)``
   gating means it should already be up).
2. Compare the CI-built ``skills.cypher`` digest to the published release.
   Changed builds are reconciled in a transaction, including on persistent
   databases. An unchanged release is a cheap no-op.
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

from decepticon.skillogy.catalog_sync import CatalogRelease, reconcile_bundled_catalog
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


def _reconcile_catalog(backend: Neo4jBackend) -> None:
    """Publish the bundled graph when its release digest changes."""
    cypher_path = Path(os.environ.get("SKILLOGY_CYPHER_PATH", "/app/skills.cypher"))
    if not cypher_path.exists():
        raise FileNotFoundError(f"bundled skills.cypher not found at {cypher_path}")
    release = CatalogRelease.from_cypher(cypher_path.read_text(encoding="utf-8"))
    if not reconcile_bundled_catalog(backend, release):
        log.info("bundled Skillogy catalog %s already current", release.digest[:12])


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
    """Reconcile the bundled catalog off the main thread.

    On a cold or upgraded container the bundled ``skills.cypher`` is several
    MB and Neo4j MERGEs thousands of statements over the Bolt driver.
    Running it on the main
    thread blocks ``uvicorn.run()`` from binding the listener until the
    reconcile finishes, which leaves ``/v1/health`` unreachable and Docker's
    healthcheck flapping past ``start_period``. We move it to a daemon
    thread so the REST server comes up first and the healthcheck passes
    immediately; the new corpus is committed in the background. An unchanged
    release only checks the persisted digest.
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
        _reconcile_catalog(backend)
    except Exception as exc:  # noqa: BLE001
        log.error("bundled catalog reconciliation failed: %r", exc)

    # Hybrid retrieval (ADR-0011): create the vector index and embed every
    # skill whose embedding-input text changed since the last boot. The dump is
    # deliberately embedding-free, so this is what makes semantic find_skill
    # possible; it degrades to a no-op when the proxy env is absent.
    #
    # This runs on every boot even when the catalog digest is unchanged.
    # Embedding is content-hashed and idempotent, so it also catches up graphs
    # changed by out-of-band incremental ingest.
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

    # Start the catalog reconcile off the main thread before serving so
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
