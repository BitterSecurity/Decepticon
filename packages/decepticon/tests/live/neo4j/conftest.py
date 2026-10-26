"""Shared fixtures for KG integration tests against live Neo4j.

The fixtures skip every test cleanly when the env vars are not set or
the driver cannot connect, so the integration suite is safe to run in
any environment — it just no-ops on stacks without Neo4j.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from decepticon.middleware.kg_internal.store import KGStore, KGStoreConfigError

_DEFAULT_TEST_URI = "bolt://localhost:7687"
_DEFAULT_TEST_USER = "neo4j"
_DEFAULT_TEST_PASSWORD = "decepticon-graph"


def _maybe_seed_defaults() -> None:
    """If the developer hasn't set DECEPTICON_NEO4J_* but the compose
    stack is running on localhost, fill in the compose defaults so the
    integration suite "just works" on a normal dev box.

    No-op when any var is already set — explicit values always win.
    """
    if not os.environ.get("DECEPTICON_NEO4J_URI"):
        os.environ.setdefault("DECEPTICON_NEO4J_URI", _DEFAULT_TEST_URI)
    if not os.environ.get("DECEPTICON_NEO4J_USER"):
        os.environ.setdefault("DECEPTICON_NEO4J_USER", _DEFAULT_TEST_USER)
    if not os.environ.get("DECEPTICON_NEO4J_PASSWORD"):
        os.environ.setdefault("DECEPTICON_NEO4J_PASSWORD", _DEFAULT_TEST_PASSWORD)


@pytest.fixture(scope="session")
def kgstore() -> Iterator[KGStore]:
    """A live :class:`KGStore` against compose Neo4j.

    Skips the test when env vars are missing or the driver cannot
    open / round-trip a trivial query.
    """
    _maybe_seed_defaults()

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
        pytest.skip(f"DECEPTICON_NEO4J_* not configured: {exc}")
    except Exception as exc:  # pragma: no cover — depends on live service
        if store is not None:
            store.close()
        pytest.skip(f"Neo4j not reachable for KG integration tests: {exc}")

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
