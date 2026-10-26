"""Explicit live Neo4j fixtures; use a disposable, operator-selected instance.

Configuration/connectivity failures fail the selected live lane. Ordinary test
collection excludes this directory before importing service clients.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from decepticon.middleware.kg_internal.store import KGStore, KGStoreConfigError


@pytest.fixture(scope="session")
def kgstore() -> Iterator[KGStore]:
    """A live KGStore against explicitly configured Neo4j."""
    missing = [
        key
        for key in ("DECEPTICON_NEO4J_URI", "DECEPTICON_NEO4J_USER", "DECEPTICON_NEO4J_PASSWORD")
        if not os.environ.get(key)
    ]
    if missing:
        pytest.fail("Explicit live Neo4j configuration required: " + ", ".join(missing))

    # Single try-block keeps ``store`` definition + smoke check together
    # so CodeQL doesn't flag ``store.close()`` as touching a possibly
    # uninitialised local.
    store: KGStore | None = None
    try:
        store = KGStore.from_env()
        # Connectivity smoke + result-shape sanity. ``schema`` is the
        # reserved label the runner uses; re-using it matches production.
        # The shape check catches CI environments where the ``neo4j``
        # driver is somehow stubbed (e.g. by an in-process mock that
        # returns ``MagicMock`` for every Cypher call). Without this,
        # those stubs silently pass the connectivity smoke and the
        # integration tests then fail against a fake graph instead of
        # skipping.
        rows = store.execute_read("RETURN 1 AS ok", {}, engagement="schema")
        if not (
            isinstance(rows, list)
            and len(rows) == 1
            and isinstance(rows[0], dict)
            and rows[0].get("ok") == 1
        ):
            raise RuntimeError(
                f"Neo4j smoke returned an unexpected shape "
                f"(type={type(rows).__name__}, "
                f"len={len(rows) if hasattr(rows, '__len__') else 'N/A'}); "
                f"the driver is probably stubbed."
            )
    except KGStoreConfigError as exc:
        if store is not None:
            store.close()
        pytest.fail(f"Live Neo4j configuration invalid: {exc}")
    except Exception as exc:  # pragma: no cover — depends on live service
        if store is not None:
            store.close()
        pytest.fail(f"Live Neo4j unavailable: {type(exc).__name__}")

    # ``store`` is guaranteed non-None here: every except path above
    # calls ``pytest.skip`` which raises. The assert narrows the type
    # for static checkers that don't model ``pytest.skip`` as NoReturn.
    assert store is not None
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def engagement(kgstore: KGStore) -> Iterator[str]:
    """Unique engagement label per test; auto-cleaned post-test."""
    label = f"itest-{uuid.uuid4().hex[:12]}"
    try:
        yield label
    finally:
        # Best-effort cleanup. Reset failures must not mask test
        # assertions, so swallow.
        try:
            kgstore.execute_write(
                "MATCH (n) WHERE n.engagement = $eng DETACH DELETE n",
                {"eng": label},
                engagement=label,
            )
        except Exception:  # pragma: no cover — best-effort
            pass
